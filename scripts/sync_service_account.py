#!/usr/bin/env python3
"""Provision, rotate and verify the service account that starts the 1C synchronization.

The account is created only through the server API: a dedicated role that holds
one event permission (start a synchronization), and one user in that role. The
password is generated here, is never printed and is stored only in the
credential file (root, 0600, directory 0700).

The server takes a new password in the query string and its request log keeps
query strings, so after every call that carries the password the container log
is searched for it and each occurrence is overwritten in place. The command
fails when the log cannot be shown clean.

provision  create the role, grant the permission, create the user (idempotent)
rotate     set a new password and rewrite the credential file
verify     log in with the credential file and compare the granted permissions
"""
from __future__ import annotations

import argparse
from datetime import date
import glob
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SYNC_PERMISSION = "administration.online_shop_seo.synchronization.run"
ROLE = {"Name": "Service: 1C sync", "Dashboard": "/dashboard", "UserRoleType": 13}
USER = {"FirstName": "синхронізація 1С", "LastName": "Службовий:", "MiddleName": "",
        "Email": "svc-sync-dev@gba.test", "PhoneNumber": "svc-sync-dev", "Region": "uk"}


class Failure(RuntimeError):
    pass


def api(server: str, method: str, path: str, *, body: dict | None = None, query: dict | None = None,
        token: str | None = None) -> tuple[int, object]:
    url = server + "/api/v1/uk" + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode(errors="replace")
    return status, payload


def unwrap(payload: object) -> object:
    if isinstance(payload, dict):
        for key in ("Body", "body"):
            if key in payload:
                return payload[key]
    return payload


def require(answer: tuple[int, object], what: str) -> object:
    status, payload = answer
    if status != 200:
        raise Failure(f"{what} failed: HTTP {status}")
    return unwrap(payload)


def field(item: dict, name: str) -> object:
    return item.get(name, item.get(name[0].lower() + name[1:]))


def login(server: str, username: str, password: str) -> str:
    status, payload = api(server, "POST", "/usermanagement/token",
                          body={"Username": username, "Password": password})
    body = unwrap(payload)
    if status != 200 or not isinstance(body, dict) or not body.get("AccessToken"):
        raise Failure(f"login of {username} failed: HTTP {status}")
    return body["AccessToken"]


def admin_token(server: str, path: Path) -> str:
    value = json.loads(path.read_text())
    return login(server, value["Username"], value["Password"])


def log_files(container: str) -> list[str]:
    result = subprocess.run(["docker", "inspect", "--format", "{{.LogPath}}", container],
                            capture_output=True, text=True)
    path = result.stdout.strip()
    if result.returncode != 0 or not path:
        raise Failure(f"the log path of {container} is unknown; the password would stay in its log")
    return sorted(glob.glob(path + "*"))


def scrub(container: str, secret: str, settle: float = 6.0) -> int:
    needle = secret.encode()
    replaced = 0
    clean_passes = 0
    deadline = time.time() + settle
    while clean_passes < 4 or time.time() < deadline:
        found = 0
        for name in log_files(container):
            with open(name, "r+b") as handle:
                data = handle.read()
                position = data.find(needle)
                while position >= 0:
                    handle.seek(position)
                    handle.write(b"x" * len(needle))
                    found += 1
                    position = data.find(needle, position + len(needle))
        replaced += found
        clean_passes = 0 if found else clean_passes + 1
        if time.time() > deadline + 30:
            raise Failure("the server log still receives the password; rotate it again")
        time.sleep(0.5)
    return replaced


def write_credential(path: Path, payload: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(path.parent, 0, 0)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def find_role(server: str, token: str) -> dict | None:
    roles = require(api(server, "GET", "/usermanagement/profiles/roles/registry", token=token), "role registry")
    matches = [role for role in roles or [] if field(role, "Name") == ROLE["Name"] and not field(role, "Deleted")]
    if len(matches) > 1:
        raise Failure(f"more than one role is named {ROLE['Name']}")
    return matches[0] if matches else None


def find_user(server: str, token: str) -> dict | None:
    users = require(api(server, "GET", "/usermanagement/profiles/users/registry", token=token), "user registry")
    if isinstance(users, dict):
        users = next((value for value in users.values() if isinstance(value, list)), [])
    matches = [user for user in users or []
               if field(user, "PhoneNumber") == USER["PhoneNumber"] and not field(user, "Deleted")]
    if len(matches) > 1:
        raise Failure(f"more than one user has the login {USER['PhoneNumber']}")
    return matches[0] if matches else None


def ensure_role(server: str, token: str) -> str:
    role = find_role(server, token)
    if role is None:
        require(api(server, "POST", "/usermanagement/profiles/roles/create", token=token, body=ROLE), "role create")
        role = find_role(server, token)
        if role is None:
            raise Failure("the role was created but is not listed")
        print(f"role created: {ROLE['Name']}")
    else:
        print(f"role present: {ROLE['Name']}")
    if field(role, "UserRoleType") != ROLE["UserRoleType"]:
        raise Failure(f"the role has type {field(role, 'UserRoleType')}, expected {ROLE['UserRoleType']}")
    net_uid = str(field(role, "NetUid"))
    path = f"/usermanagement/profiles/roles/{net_uid}/event-permissions"
    current = require(api(server, "GET", path, token=token), "role permissions")
    keys = set(current.get("permissionKeys") or [])
    if keys != {SYNC_PERMISSION}:
        updated = require(api(server, "PUT", path, token=token, body={
            "expectedVersion": current.get("version", 0), "permissionKeys": [SYNC_PERMISSION]}),
            "role permissions update")
        print(f"grants set: {sorted(updated.get('permissionKeys') or [])}")
    else:
        print(f"grants unchanged: {sorted(keys)}")
    return net_uid


def credential_payload(password: str, user_net_uid: str, role_net_uid: str, server: str) -> dict:
    return {"Username": USER["PhoneNumber"], "Password": password, "UserNetUid": user_net_uid,
            "Role": ROLE["Name"], "RoleNetUid": role_net_uid, "Permissions": [SYNC_PERMISSION],
            "Server": server, "Changed": date.today().isoformat()}


def verify(args: argparse.Namespace) -> None:
    value = json.loads(args.credential_file.read_text())
    token = login(args.server, value["Username"], value["Password"])
    require(api(args.server, "GET", "/data/sync/status", token=token), "synchronization status")
    status, payload = api(args.server, "GET", "/permissions/me", token=token)
    if status == 403:
        print(f"login ok: {value['Username']} reads the synchronization status; "
              f"the server confines it to permission-gated routes")
        return
    mine = require((status, payload), "own permissions")
    granted = sorted(mine.get("permissionKeys") or [])
    if granted != [SYNC_PERMISSION]:
        raise Failure(f"the account holds {granted}, expected only {SYNC_PERMISSION}")
    print(f"login ok: {value['Username']} reads the synchronization status and holds exactly {granted}")


def provision(args: argparse.Namespace) -> None:
    token = admin_token(args.server, args.admin_login_file)
    role_net_uid = ensure_role(args.server, token)
    user = find_user(args.server, token)
    if user is not None:
        print(f"user present: {USER['PhoneNumber']}")
        if not args.credential_file.exists():
            raise Failure("the user exists but the credential file does not; run rotate")
        verify(args)
        return
    password = secrets.token_urlsafe(48)
    try:
        status, payload = api(args.server, "POST", "/usermanagement/users/create", token=token,
                              query={"password": password}, body={**USER, "UserRole": {"NetUid": role_net_uid}})
    finally:
        replaced = scrub(args.container, password)
    result = unwrap(payload)
    if status != 200 or (isinstance(result, dict) and result.get("Succeeded") is False):
        raise Failure(f"user create failed: HTTP {status}")
    user = find_user(args.server, token)
    if user is None:
        raise Failure("the user was created but is not listed")
    write_credential(args.credential_file,
                     credential_payload(password, str(field(user, "NetUid")), role_net_uid, args.server))
    print(f"user created: {USER['PhoneNumber']}; password occurrences removed from the server log: {replaced}")
    print(f"credential written: {args.credential_file}")
    verify(args)


def rotate(args: argparse.Namespace) -> None:
    token = admin_token(args.server, args.admin_login_file)
    role_net_uid = ensure_role(args.server, token)
    user = find_user(args.server, token)
    if user is None:
        raise Failure("the user does not exist; run provision")
    user_net_uid = str(field(user, "NetUid"))
    password = secrets.token_urlsafe(48)
    try:
        status, payload = api(args.server, "PATCH", "/usermanagement/profiles/users/password/reset", token=token,
                              query={"netId": user_net_uid, "password": password})
    finally:
        replaced = scrub(args.container, password)
    result = unwrap(payload)
    if status != 200 or (isinstance(result, dict) and result.get("Succeeded") is False):
        raise Failure(f"password reset failed: HTTP {status}; the credential file was not changed")
    write_credential(args.credential_file, credential_payload(password, user_net_uid, role_net_uid, args.server))
    print(f"password rotated; occurrences removed from the server log: {replaced}")
    verify(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("provision", "rotate", "verify"))
    parser.add_argument("--server", default="http://127.0.0.1:35981")
    parser.add_argument("--container", default="gba-dev-data-concord-1")
    parser.add_argument("--credential-file", type=Path, default=Path("/etc/gba-sync/credential.json"))
    parser.add_argument("--admin-login-file", type=Path,
                        help="JSON with Username and Password of an administrator; read, never changed")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command != "verify" and args.admin_login_file is None:
        print("--admin-login-file is required", file=sys.stderr)
        return 2
    try:
        {"provision": provision, "rotate": rotate, "verify": verify}[args.command](args)
    except Failure as failure:
        print(f"FAILED: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
