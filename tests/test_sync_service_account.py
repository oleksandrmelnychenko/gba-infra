import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_service_account as account


class FakeServer:
    def __init__(self, role_keys=None, user=False, me_status=200, create_status=200, body_passwords=True):
        self.roles, self.users, self.calls, self.passwords = [], [], [], []
        self.role_keys, self.version = list(role_keys or []), 0
        self.me_status, self.create_status = me_status, create_status
        self.body_passwords, self.urls = body_passwords, []
        if user:
            self.roles.append({"Name": account.ROLE["Name"], "NetUid": "role-1", "UserRoleType": 13, "Deleted": False})
            self.users.append({"PhoneNumber": account.USER["PhoneNumber"], "NetUid": "user-1", "Deleted": False})

    def __call__(self, server, method, path, *, body=None, query=None, token=None):
        self.calls.append((method, path))
        self.urls.append(path + "?" + account.urllib.parse.urlencode(query or {}))
        if path == "/usermanagement/token":
            if body["Username"] == "admin":
                return 200, {"Body": {"AccessToken": "admin-token"}}
            ok = self.passwords and body["Password"] == self.passwords[-1]
            return (200, {"Body": {"AccessToken": "service-token"}}) if ok else (401, None)
        if path.endswith("/roles/registry"):
            return 200, {"Body": self.roles}
        if path.endswith("/roles/create"):
            self.roles.append({**body, "NetUid": "role-1", "Deleted": False})
            return 200, {"Body": {}}
        if path.endswith("/event-permissions"):
            if method == "PUT":
                assert body["expectedVersion"] == self.version
                self.role_keys, self.version = body["permissionKeys"], self.version + 1
            return 200, {"Body": {"permissionKeys": self.role_keys, "version": self.version}}
        if path.endswith("/users/registry"):
            return 200, {"Body": self.users}
        if path.endswith("/users/create"):
            assert not query
            if self.body_passwords:
                self.passwords.append(body["Password"])
            if self.create_status == 200:
                self.users.append({**body, "NetUid": "user-1", "Deleted": False})
            return self.create_status, {"Body": {"Succeeded": self.create_status == 200}}
        if path.endswith("/users/password/reset"):
            assert query == {"netId": "user-1"}
            if self.body_passwords:
                self.passwords.append(body["Password"])
            return 200, {"Body": {"Succeeded": True}}
        if path == "/data/sync/status":
            return (200, {"Body": {}}) if token == "service-token" else (401, None)
        if path == "/permissions/me":
            return self.me_status, {"Body": {"permissionKeys": self.role_keys}}
        raise AssertionError(path)


@pytest.fixture
def stand(tmp_path):
    admin = tmp_path / "admin.json"
    admin.write_text(json.dumps({"Username": "admin", "Password": "admin-password"}))
    args = SimpleNamespace(server="http://server", container="server", admin_login_file=admin,
                           credential_file=tmp_path / "etc/credential.json")
    scrubbed = []
    with patch.object(account, "scrub", side_effect=lambda container, secret: scrubbed.append(secret) or 2), \
            patch.object(account.os, "chown"):
        yield args, scrubbed


def test_provision_creates_a_role_with_one_permission_and_a_user_and_stores_the_password(stand, capsys):
    args, scrubbed = stand
    server = FakeServer()
    with patch.object(account, "api", side_effect=server):
        account.provision(args)
    assert server.role_keys == [account.SYNC_PERMISSION]
    stored = json.loads(args.credential_file.read_text())
    assert stored["Username"] == "svc-sync-dev" and stored["Password"] == server.passwords[0]
    assert len(stored["Password"]) >= 48 and scrubbed == [stored["Password"]]
    assert stat.S_IMODE(args.credential_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(args.credential_file.parent.stat().st_mode) == 0o700
    output = capsys.readouterr()
    assert stored["Password"] not in output.out + output.err and "admin-password" not in output.out + output.err


def test_provision_is_idempotent_and_does_not_change_the_password(stand):
    args, scrubbed = stand
    server = FakeServer()
    with patch.object(account, "api", side_effect=server):
        account.provision(args)
        before = args.credential_file.read_text()
        account.provision(args)
    assert args.credential_file.read_text() == before and len(server.passwords) == 1 and len(scrubbed) == 1
    assert [call for call in server.calls if call[1].endswith("/roles/create")] == [("POST", "/usermanagement/profiles/roles/create")]


def test_extra_grants_on_the_role_are_reduced_to_the_synchronization_permission(stand):
    args, _ = stand
    server = FakeServer(role_keys=[account.SYNC_PERMISSION, "administration.users.user.create"], user=True)
    with patch.object(account, "api", side_effect=server):
        account.rotate(args)
    assert server.role_keys == [account.SYNC_PERMISSION]


def test_failed_create_scrubs_the_log_and_writes_no_credential(stand):
    args, scrubbed = stand
    server = FakeServer(create_status=400)
    with patch.object(account, "api", side_effect=server), pytest.raises(account.Failure):
        account.provision(args)
    assert scrubbed == server.passwords and not args.credential_file.exists()


def test_the_password_never_travels_in_a_url(stand):
    args, scrubbed = stand
    server = FakeServer()
    with patch.object(account, "api", side_effect=server):
        account.provision(args)
        account.rotate(args)
    assert len(scrubbed) == 2
    assert not any(secret in url or "password" in url.split("?", 1)[1].lower()
                   for secret in scrubbed for url in server.urls)


def test_a_server_that_ignores_the_body_password_leaves_the_credential_file_unchanged(stand):
    args, _ = stand
    server = FakeServer()
    with patch.object(account, "api", side_effect=server):
        account.provision(args)
        before = args.credential_file.read_text()
        server.body_passwords = False
        with pytest.raises(account.Failure, match="did not apply the password"):
            account.rotate(args)
        assert args.credential_file.read_text() == before
        account.verify(args)


def test_rotate_replaces_the_password_and_the_old_one_stops_working(stand):
    args, scrubbed = stand
    server = FakeServer()
    with patch.object(account, "api", side_effect=server):
        account.provision(args)
        first = json.loads(args.credential_file.read_text())["Password"]
        account.rotate(args)
        second = json.loads(args.credential_file.read_text())["Password"]
        assert first != second and scrubbed == [first, second]
        assert server("http://server", "POST", "/usermanagement/token",
                      body={"Username": "svc-sync-dev", "Password": first})[0] == 401
        account.verify(args)


def test_verify_fails_when_the_account_holds_anything_else_and_accepts_a_confined_account(stand, capsys):
    args, _ = stand
    server = FakeServer()
    with patch.object(account, "api", side_effect=server):
        account.provision(args)
        server.role_keys = [account.SYNC_PERMISSION, "sales.ukraine.sale.view"]
        with pytest.raises(account.Failure, match="expected only"):
            account.verify(args)
        server.me_status = 403
        account.verify(args)
    assert "confines it to permission-gated routes" in capsys.readouterr().out


def test_scrub_overwrites_every_occurrence_in_place_in_all_log_files(tmp_path):
    secret = "S" * 64
    current, rotated = tmp_path / "c-json.log", tmp_path / "c-json.log.1"
    current.write_bytes(b"start /create?password=" + secret.encode() + b" - \nfinish password=" + secret.encode() + b" 200\n")
    rotated.write_bytes(b"old " + secret.encode() + b"\n")
    size = current.stat().st_size
    with patch.object(account, "log_files", return_value=[str(current), str(rotated)]), \
            patch.object(account.time, "sleep"):
        assert account.scrub("server", secret, settle=0) == 3
    assert secret.encode() not in current.read_bytes() + rotated.read_bytes()
    assert current.stat().st_size == size and current.read_bytes().count(b"x" * 64) == 2
    assert current.read_bytes().startswith(b"start /create?password=xxxx")


def test_scrub_fails_when_the_log_path_is_unknown():
    with patch.object(account.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="")), \
            pytest.raises(account.Failure, match="log path"):
        account.scrub("missing", "secret", settle=0)
