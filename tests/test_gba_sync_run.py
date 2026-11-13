from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gba_sync_run as runner


def fleet_report(unhealthy=()):
    services = {name: {"up": True, "healthy": name not in unhealthy, "active": "active", "enabled": "enabled",
                       **({"reason": "HTTP 503, status 'degraded'"} if name in unhealthy else {})}
                for name in runner.health.SERVICES}
    return {"checked_at": "now", "maintenance": {"present": False}, "started": [], "services": services,
            "down": [], "unhealthy": list(unhealthy)}


class FakeStand:
    def __init__(self, outcomes=None, token_code=200, return_ids=True, report_runs=True,
                 shop_status="Healthy", search_ok=True, busy=False):
        self.outcomes = {"amg": "Finished", "fenix": "Finished"} | (outcomes or {})
        self.token_code, self.return_ids, self.report_runs = token_code, return_ids, report_runs
        self.shop_status, self.search_ok, self.busy = shop_status, search_ok, busy
        self.terminal = {"PipelineRunId": "0ld0ld", "Status": "Finished", "StartedAtUtc": "2026-01-01T00:00:00Z"}
        self.active, self.polls_left = None, 0
        self.calls, self.started, self.tokens = [], [], 0
        self.operations = []

    def answer(self, body, code=200):
        return code, json.dumps({"Body": body, "StatusCode": code, "Message": ""}).encode()

    def __call__(self, url, *, data=None, headers=None, method=None, timeout=60):
        self.calls.append(url)
        if url.endswith("/usermanagement/token"):
            self.tokens += 1
            assert {key.lower(): value for key, value in json.loads(data).items()} == \
                {"username": "u", "password": "p"}
            if self.token_code != 200:
                return self.answer(None, self.token_code)
            return self.answer({"AccessToken": "token", "AccessTokenExpiresIn": 900})
        if url.endswith("/data/sync/status"):
            assert headers["Authorization"] == "Bearer token"
            if self.active and self.polls_left > 0:
                self.polls_left -= 1
                return self.answer({"IsInProgress": True, "LastTerminalRun": self.terminal})
            if self.active:
                if self.report_runs:
                    self.terminal = self.active
                self.active = None
            return self.answer({"IsInProgress": self.busy, "LastTerminalRun": self.terminal})
        if "/data/sync/start/session" in url:
            name = "amg" if "forAmg=true" in url else "fenix"
            self.started.append(name)
            self.operations.append(headers["X-GBA-Sync-Operation-Id"])
            if self.outcomes[name] == "refused":
                return self.answer(None, 409)
            identifier = str(uuid.uuid4())
            self.active = {"PipelineRunId": identifier.replace("-", ""), "Status": self.outcomes[name],
                           "FailedStep": None if self.outcomes[name] == "Finished" else "Sales",
                           "StartedAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
            self.polls_left = 2
            return self.answer({"PipelineRunId": identifier} if self.return_ids else None)
        if url == "http://server/health":
            return 200, b"Healthy"
        if url == "http://shop/health":
            return 200, json.dumps({"status": self.shop_status, "checks": [
                {"name": "search-index", "status": self.shop_status}]}).encode()
        if url.endswith("/elasticsearch/health"):
            synced = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
            return self.answer({"healthy": True, "indexExists": True, "stale": False, "lastSyncUtc": synced})
        if url.endswith("/elasticsearch/sync/full"):
            self.started.append("search")
            return self.answer({"success": True}) if self.search_ok else self.answer({"error": "stale"}, 503)
        raise AssertionError(url)


@pytest.fixture
def stand(tmp_path):
    login = tmp_path / "login.json"
    login.write_text(json.dumps({"UserName": "u", "Password": "p"}))
    def make(mode="light", *extra, **server):
        fake = FakeStand(**server)
        args = runner.build_parser().parse_args([
            "--mode", mode, "--login-file", str(login), "--state-dir", str(tmp_path / "sync"),
            "--ai-state-dir", str(tmp_path / "fleet"), "--server", "http://server", "--shop-api", "http://shop",
            "--start-grace", "0", "--status-timeout", "0", "--login-timeout", "0", "--busy-wait", "0", "--search-settle", "0",
            *extra])
        return fake, runner.Runner(args), login
    with patch.object(runner.time, "sleep"), \
            patch.object(runner.health, "check_fleet", return_value=fleet_report()) as fleet:
        make.fleet = fleet
        yield make


def execute(fake, job):
    with patch.object(runner, "http_request", side_effect=fake):
        return job.run()


def latest(job):
    return json.loads((job.state_dir / "latest.json").read_text())


def test_light_runs_amg_then_fenix_and_matches_each_result_to_its_own_run(stand):
    fake, job, login = stand("light", "--consume-login")
    assert execute(fake, job) == 0
    assert fake.started == ["amg", "fenix"]
    assert len(set(fake.operations)) == 2
    result = latest(job)
    assert result["ok"] and result["sessions"] and result["downstream"] == {}
    assert all(base["ok"] and base["matchedBy"] == "pipeline run id" for base in result["bases"].values())
    assert result["bases"]["amg"]["pipelineRun"] != result["bases"]["fenix"]["pipelineRun"]
    assert not login.exists()
    make_fleet_calls = stand.fleet.call_count
    assert make_fleet_calls == 0


def test_stale_terminal_run_is_not_taken_for_the_started_session(stand):
    fake, job, _ = stand("light", report_runs=False)
    assert execute(fake, job) == runner.EXIT_FAILED
    result = latest(job)
    assert not result["ok"] and not result["bases"]["amg"]["ok"]
    assert "reported no terminal state for the started run" in result["bases"]["amg"]["reason"]
    assert "0ld0ld" in result["bases"]["amg"]["reason"]


def test_missing_run_id_falls_back_to_a_new_terminal_run_started_after_the_call(stand):
    fake, job, _ = stand("light", "--start-grace", "600", return_ids=False)
    assert execute(fake, job) == 0
    assert "started after the start call" in latest(job)["bases"]["fenix"]["matchedBy"]


def test_failed_amg_is_reported_per_base_and_fenix_still_runs(stand):
    fake, job, _ = stand("light", outcomes={"amg": "Failed"})
    assert execute(fake, job) == runner.EXIT_FAILED
    assert fake.started == ["amg", "fenix"]
    result = latest(job)
    assert result["bases"]["amg"]["ok"] is False and result["bases"]["amg"]["failedStep"] == "Sales"
    assert result["bases"]["fenix"]["ok"] is True and result["sessions"] is False


def test_refused_start_is_reported_and_the_other_base_still_runs(stand):
    fake, job, _ = stand("light", outcomes={"amg": "refused"})
    assert execute(fake, job) == runner.EXIT_FAILED
    assert fake.started == ["amg", "fenix"]
    assert "HTTP 409" in latest(job)["bases"]["amg"]["reason"]


def test_login_file_survives_a_refused_credential_and_a_dry_run(stand):
    fake, job, login = stand("light", "--consume-login", token_code=401)
    assert execute(fake, job) == runner.EXIT_LOGIN
    assert login.exists() and fake.started == []
    fake, job, login = stand("light", "--consume-login", "--dry-run")
    assert execute(fake, job) == 0
    assert login.exists() and fake.started == [] and fake.tokens == 1


def test_unreadable_or_malformed_login_file_is_exit_two(stand, tmp_path):
    fake, job, login = stand("light", "--consume-login")
    login.write_text("not json")
    assert execute(fake, job) == runner.EXIT_LOGIN
    assert login.exists() and fake.tokens == 0
    login.unlink()
    fake, job, _ = stand("light")
    assert execute(fake, job) == runner.EXIT_LOGIN


def test_busy_server_and_unavailable_status_stop_before_any_session(stand):
    fake, job, _ = stand("light", busy=True)
    assert execute(fake, job) == runner.EXIT_BUSY
    assert fake.started == []
    assert job.args.login_file.exists()
    fake, job, _ = stand("light")
    def broken(url, **kwargs):
        if "status" in url:
            raise OSError("connection refused")
        return fake(url, **kwargs)
    with patch.object(runner, "http_request", side_effect=broken):
        assert job.run() == runner.EXIT_PREFLIGHT
    assert fake.started == []


def test_full_checks_ai_and_shop_health_bodies_before_the_sessions(stand):
    stand.fleet.return_value = fleet_report(unhealthy=("pricing",))
    fake, job, _ = stand("full")
    assert execute(fake, job) == runner.EXIT_PREFLIGHT
    assert fake.started == []
    assert any("gba-pricing" in problem for problem in latest(job)["preflight_problems"])
    assert stand.fleet.call_args.kwargs == {"start_inactive": True}
    stand.fleet.return_value = fleet_report()
    fake, job, _ = stand("full", shop_status="Unhealthy")
    assert execute(fake, job) == runner.EXIT_PREFLIGHT
    assert fake.started == [] and "shop API" in latest(job)["preflight_problems"][0]


def fleet_process(job, *, exit_code=0, status="complete", started_delta=1, seen=None):
    def invoke(command, **kwargs):
        if seen is not None:
            seen.append(command)
        day = command[command.index("--cycle-date") + 1]
        since = datetime.fromisoformat(command[command.index("--fresh-since") + 1])
        job.args.ai_state_dir.mkdir(parents=True, exist_ok=True)
        if exit_code != runner.FLEET_LOCK_BUSY:
            (job.args.ai_state_dir / f"{day}.json").write_text(json.dumps({
                "status": status, "run_id": "fleet-run", "stages": {"reco": {"status": "complete"}},
                "started_at": (since + timedelta(seconds=started_delta)).isoformat()}))
        return SimpleNamespace(returncode=exit_code)
    return patch.object(runner.subprocess, "run", side_effect=invoke)


def test_full_refreshes_search_and_ai_for_the_kyiv_calendar_date(stand):
    fake, job, _ = stand("full")
    commands = []
    with fleet_process(job, seen=commands):
        assert execute(fake, job) == 0
    assert fake.started == ["amg", "fenix", "search"]
    command = commands[0]
    assert command[command.index("--cycle-date") + 1] == datetime.now(runner.KYIV).date().isoformat()
    assert "--force" in command and "--wait-lock" in command and "--fresh-since" in command
    assert latest(job)["downstream"] == {"search": True, "ai": True}


def test_failed_search_rebuild_is_reported_and_the_ai_refresh_still_runs(stand):
    fake, job, _ = stand("refresh", search_ok=False)
    commands = []
    with fleet_process(job, seen=commands):
        assert execute(fake, job) == runner.EXIT_FAILED
    assert len(commands) == 1
    assert latest(job)["downstream"] == {"search": False, "ai": True}
    assert "runs anyway" in (job.state_dir / "run.log").read_text()


def test_fleet_lock_held_or_cycle_older_than_the_sync_is_not_success(stand):
    for options, text in (({"exit_code": runner.FLEET_LOCK_BUSY}, "held the lock"),
                          ({"started_delta": -3600}, "before the data changed"),
                          ({"status": "failed", "exit_code": 1}, "exited 1"),
                          ({"status": "running"}, "not complete")):
        fake, job, _ = stand("refresh")
        with fleet_process(job, **options):
            assert execute(fake, job) == runner.EXIT_FAILED
        assert latest(job)["downstream"]["ai"] is False
        assert text in (job.state_dir / "run.log").read_text().splitlines()[-2]


def test_unhealthy_service_after_the_refresh_fails_the_ai_step(stand):
    fake, job, _ = stand("refresh")
    stand.fleet.side_effect = [fleet_report(), fleet_report(unhealthy=("nba",))]
    with fleet_process(job):
        assert execute(fake, job) == runner.EXIT_FAILED
    assert latest(job)["downstream"] == {"search": True, "ai": False}


def test_no_finished_base_skips_downstream_in_full_mode(stand):
    fake, job, _ = stand("full", outcomes={"amg": "Failed", "fenix": "Failed"})
    with fleet_process(job) as process:
        assert execute(fake, job) == runner.EXIT_FAILED
        process.assert_not_called()
    assert fake.started == ["amg", "fenix"] and latest(job)["downstream"] == {}


def test_one_finished_base_still_refreshes_downstream_but_the_run_is_failed(stand):
    fake, job, _ = stand("full", outcomes={"amg": "Failed"})
    with fleet_process(job) as process:
        assert execute(fake, job) == runner.EXIT_FAILED
        assert process.call_count == 1
    assert latest(job)["downstream"] == {"search": True, "ai": True} and not latest(job)["ok"]


def test_dry_run_full_prints_the_plan_and_starts_nothing(stand, capsys):
    fake, job, login = stand("full", "--dry-run", "--consume-login")
    with patch.object(runner.subprocess, "run") as process:
        assert execute(fake, job) == 0
        process.assert_not_called()
    output = capsys.readouterr().out
    assert fake.started == [] and login.exists() and not job.state_dir.exists()
    assert "PLAN 1." in output and "session AMG" in output and "AI fleet refresh for cycle" in output
    assert "nothing was started" in output and '"p"' not in output and "token" not in output.lower().replace(
        "produced a token", "")
    assert stand.fleet.call_args.kwargs == {"start_inactive": False}


def test_unit_command_line_is_still_accepted():
    unit = (Path(__file__).resolve().parents[1] / "systemd/gba-sync@.service").read_text()
    line = next(item for item in unit.splitlines() if item.startswith("ExecStart="))
    words = line.split()[2:]
    words = [word.replace("%i", "light").replace("${GBA_SYNC_CUTOVER_ID}", "cutover") for word in words]
    args = runner.build_parser().parse_args(words)
    assert args.mode == "light" and args.consume_login and args.cutover_id == "cutover"
    assert args.login_file == Path("/var/lib/gba-sync/login.private.json") and not args.dry_run
    assert args.credential_file == Path("/etc/gba-sync/credential.json")


def credential(tmp_path, mode=0o600, content=None):
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(content if content is not None else
                               {"Username": "u", "Password": "p", "Role": "Service: 1C sync"}))
    path.chmod(mode)
    return path


def test_persistent_credential_is_preferred_and_neither_file_is_deleted(stand, tmp_path):
    path = credential(tmp_path)
    fake, job, login = stand("light", "--consume-login", "--credential-file", str(path))
    with patch.object(runner, "CREDENTIAL_OWNER", path.stat().st_uid):
        assert execute(fake, job) == 0
    assert fake.started == ["amg", "fenix"]
    assert path.exists() and login.exists()
    assert latest(job)["ok"] and latest(job)["failure"] is None


def test_one_time_login_is_used_when_the_credential_file_is_absent(stand, tmp_path):
    fake, job, login = stand("light", "--consume-login", "--credential-file", str(tmp_path / "absent.json"))
    assert execute(fake, job) == 0
    assert fake.started == ["amg", "fenix"] and not login.exists()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o700, 0o400])
def test_credential_file_with_another_mode_is_refused_without_falling_back(stand, tmp_path, mode):
    path = credential(tmp_path, mode)
    fake, job, login = stand("light", "--consume-login", "--credential-file", str(path))
    with patch.object(runner, "CREDENTIAL_OWNER", path.stat().st_uid):
        assert execute(fake, job) == runner.EXIT_LOGIN
    assert fake.tokens == 0 and fake.started == [] and login.exists() and path.exists()
    result = latest(job)
    assert not result["ok"] and result["exit"] == runner.EXIT_LOGIN
    assert "is refused" in result["failure"] and f"mode {mode:04o}" in result["failure"]


def test_credential_file_of_another_owner_or_a_symlink_is_refused(stand, tmp_path):
    path = credential(tmp_path)
    fake, job, _ = stand("light", "--credential-file", str(path))
    with patch.object(runner, "CREDENTIAL_OWNER", path.stat().st_uid + 1):
        assert execute(fake, job) == runner.EXIT_LOGIN
    assert "owned by uid" in latest(job)["failure"] and fake.tokens == 0
    link = tmp_path / "link.json"
    link.symlink_to(path)
    fake, job, _ = stand("light", "--credential-file", str(link))
    with patch.object(runner, "CREDENTIAL_OWNER", path.stat().st_uid):
        assert execute(fake, job) == runner.EXIT_LOGIN
    assert "not a regular file" in latest(job)["failure"] and fake.tokens == 0


def test_credential_file_without_a_password_is_refused(stand, tmp_path):
    path = credential(tmp_path, content={"Username": "u"})
    fake, job, _ = stand("light", "--credential-file", str(path))
    with patch.object(runner, "CREDENTIAL_OWNER", path.stat().st_uid):
        assert execute(fake, job) == runner.EXIT_LOGIN
    assert "no Username and Password" in latest(job)["failure"]


def test_refused_persistent_credential_fails_the_run_and_keeps_the_file(stand, tmp_path):
    path = credential(tmp_path)
    fake, job, _ = stand("light", "--credential-file", str(path), token_code=401)
    with patch.object(runner, "CREDENTIAL_OWNER", path.stat().st_uid):
        assert execute(fake, job) == runner.EXIT_LOGIN
    assert path.exists() and fake.started == []
    assert "refused the credential (HTTP 401); the credential file was left in place" in latest(job)["failure"]


def test_neither_the_password_nor_the_token_reaches_the_log_or_the_state(stand, tmp_path, capsys):
    path = credential(tmp_path, content={"Username": "u", "Password": "p", "Note": "s3cr3t-marker"})
    secret = json.loads(path.read_text())
    fake, job, _ = stand("light", "--credential-file", str(path), outcomes={"amg": "Failed"})
    def answering(url, **kwargs):
        code, raw = fake(url, **kwargs)
        if url.endswith("/usermanagement/token"):
            raw = json.dumps({"Body": {"AccessToken": "t0ken-marker", "AccessTokenExpiresIn": 900}}).encode()
        return code, raw
    def stand_in(url, *, headers=None, **kwargs):
        if headers and headers.get("Authorization") == "Bearer t0ken-marker":
            headers = {**headers, "Authorization": "Bearer token"}
        return answering(url, headers=headers, **kwargs)
    with patch.object(runner, "CREDENTIAL_OWNER", path.stat().st_uid), \
            patch.object(runner, "http_request", side_effect=stand_in):
        assert job.run() == runner.EXIT_FAILED
    written = capsys.readouterr().out + (job.state_dir / "run.log").read_text() \
        + (job.state_dir / "latest.json").read_text()
    assert "t0ken-marker" not in written and secret["Note"] not in written
    assert '"Password"' not in written and '"p"' not in written
    assert '"source": "credential file"' in written


def test_dry_run_with_the_persistent_credential_names_it_in_the_plan(stand, tmp_path, capsys):
    path = credential(tmp_path)
    fake, job, login = stand("light", "--dry-run", "--consume-login", "--credential-file", str(path))
    with patch.object(runner, "CREDENTIAL_OWNER", path.stat().st_uid):
        assert execute(fake, job) == 0
    output = capsys.readouterr().out
    assert f"log in with the persistent credential {path} (never deleted)" in output
    assert "the run would proceed" in output and fake.started == []
    assert login.exists() and path.exists() and not job.state_dir.exists()


def test_every_failure_before_a_session_is_written_to_latest_json(stand):
    fake, job, _ = stand("light", token_code=401)
    assert execute(fake, job) == runner.EXIT_LOGIN
    assert latest(job)["ok"] is False and "HTTP 401" in latest(job)["failure"]
    fake, job, _ = stand("light", busy=True)
    assert execute(fake, job) == runner.EXIT_BUSY
    assert latest(job)["exit"] == runner.EXIT_BUSY and "already in progress" in latest(job)["failure"]
    fake, job, _ = stand("light", outcomes={"fenix": "Failed"})
    assert execute(fake, job) == runner.EXIT_FAILED
    assert latest(job)["failure"] == "fenix: Failed at Sales" and latest(job)["exit"] == runner.EXIT_FAILED


def test_nightly_timer_fires_at_two_kyiv_and_never_catches_up():
    timer = (Path(__file__).resolve().parents[1] / "systemd/gba-sync-nightly.timer").read_text().splitlines()
    assert "OnCalendar=*-*-* 02:00:00 Europe/Kyiv" in timer
    assert "Persistent=false" in timer and "Unit=gba-sync@light.service" in timer
    assert not any(line.startswith(("OnBootSec", "OnStartupSec", "OnUnitActiveSec")) for line in timer)


def test_next_base_waits_until_the_server_is_idle_after_its_run_is_terminal(stand):
    fake, job, _ = stand("light")
    real = fake.__call__
    lingering = {}
    def slow_release(url, **kwargs):
        code, raw = real(url, **kwargs)
        if url.endswith("/data/sync/status"):
            body = json.loads(raw)
            if fake.active is None and fake.started and lingering.get(fake.started[-1], 2) > 0 \
                    and body["Body"]["LastTerminalRun"]["PipelineRunId"] != "0ld0ld":
                lingering[fake.started[-1]] = lingering.get(fake.started[-1], 2) - 1
                body["Body"]["IsInProgress"] = True
                raw = json.dumps(body).encode()
        if "/start/session" in url and "forAmg=false" in url:
            assert lingering["amg"] == 0
        return code, raw
    with patch.object(runner, "http_request", side_effect=slow_release):
        assert job.run() == 0
    assert lingering["amg"] == 0 and lingering["fenix"] == 0
    assert fake.started == ["amg", "fenix"]
