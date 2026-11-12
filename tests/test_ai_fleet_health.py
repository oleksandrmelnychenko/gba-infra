import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_fleet_health as health
import ai_fleet_watchdog as watchdog


class FakeSystemd:
    def __init__(self, active=None, enabled=None, start_works=True):
        self.active = {name: "active" for name in health.SERVICES} | (active or {})
        self.enabled = {name: "enabled" for name in health.SERVICES} | (enabled or {})
        self.start_works = start_works
        self.commands = []

    def __call__(self, *args, timeout=90):
        self.commands.append(args)
        name = args[1].removeprefix("gba-").removesuffix(".service")
        if args[0] == "is-active":
            return (0 if self.active[name] == "active" else 3), self.active[name]
        if args[0] == "is-enabled":
            return (0 if self.enabled[name] == "enabled" else 1), self.enabled[name]
        if args[0] == "start":
            if self.start_works:
                self.active[name] = "active"
                return 0, ""
            return 1, "Job for unit failed"
        raise AssertionError(args)

    def started(self):
        return [args[1] for args in self.commands if args[0] == "start"]

    def http(self, url, timeout=20):
        port = int(url.split(":")[2].split("/")[0])
        name = next(key for key, value in health.SERVICES.items() if value == port)
        if self.active[name] != "active":
            raise ConnectionRefusedError("connection refused")
        return 200, json.dumps({"status": "healthy", "business_ready": True}).encode()


def fake(systemd):
    return patch.multiple(health, systemctl=systemd, http_get=systemd.http)


def test_health_body_decides_not_the_status_code():
    ok = health.body_verdict("reco", 200, b'{"status":"healthy","business_ready":true}')
    assert ok["healthy"] and ok["up"] and ok["business_ready"] is True
    degraded = health.body_verdict("reco", 200, b'{"status":"degraded","reasons":["stale source"]}')
    assert degraded["up"] and not degraded["healthy"] and "stale source" in degraded["reason"]
    assert not health.body_verdict("reco", 503, b'{"status":"healthy"}')["healthy"]
    html = health.body_verdict("reco", 200, b"<html>ok</html>")
    assert not html["up"] and not html["healthy"]
    assert health.body_verdict("solvency", 200, b'{"status":"degraded","serving_ready":true}')["healthy"]
    assert not health.body_verdict("solvency", 200, b'{"status":"degraded","serving_ready":false}')["healthy"]
    assert not health.body_verdict("pricing", 200, b'{"status":"degraded","serving_ready":true}')["healthy"]


def test_inactive_unit_is_down_even_if_something_answers_on_the_port():
    systemd = FakeSystemd(active={"nba": "inactive"})
    with patch.multiple(health, systemctl=systemd, http_get=lambda url, timeout=20: (200, b'{"status":"healthy"}')):
        result = health.check("nba")
    assert not result["up"] and not result["healthy"] and "inactive" in result["reason"]


def test_preflight_starts_an_enabled_inactive_unit_once_and_rechecks(tmp_path):
    systemd = FakeSystemd(active={"pricing": "inactive", "forecast": "failed", "nba": "inactive"},
                          enabled={"nba": "disabled"})
    with fake(systemd):
        report = health.check_fleet(start_inactive=True, marker=tmp_path / "maintenance", settle_seconds=0)
    assert systemd.started() == ["gba-pricing.service", "gba-forecast.service"]
    assert report["started"] == ["pricing", "forecast"]
    assert report["down"] == ["nba"] and report["unhealthy"] == ["nba"]
    assert report["services"]["pricing"]["healthy"] and report["services"]["pricing"]["start_attempted"]


def test_failed_start_leaves_the_service_named_as_down(tmp_path):
    systemd = FakeSystemd(active={"reco": "failed"}, start_works=False)
    with fake(systemd), patch.object(health.time, "sleep"):
        report = health.check_fleet(start_inactive=True, marker=tmp_path / "maintenance", settle_seconds=0)
    assert report["down"] == ["reco"] and report["services"]["reco"]["start_exit"] == 1
    assert systemd.started() == ["gba-reco.service"]


def test_nothing_is_started_without_permission_or_during_maintenance(tmp_path):
    systemd = FakeSystemd(active={"pricing": "inactive"})
    with fake(systemd):
        assert health.check_fleet(start_inactive=False, marker=tmp_path / "maintenance")["down"] == ["pricing"]
        (tmp_path / "maintenance").write_text("")
        report = health.check_fleet(start_inactive=True, marker=tmp_path / "maintenance")
    assert systemd.started() == []
    assert report["services"]["pricing"]["start_skipped"] == "maintenance marker present"
    assert report["maintenance"]["present"] and not report["maintenance"]["stale"]


def test_unit_runtime_reads_the_drop_in_tree_interpreter_and_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nDB_NAME=ConcordDb_V5\nDB_PASSWORD=\"s3 cret\"\n\nREDIS_DB=2\n")
    shown = "\n".join([
        "LoadState=loaded",
        "WorkingDirectory=/root/projects/gba-ai-services/gba-products",
        "ExecStart={ path=/root/projects/gba-products/.venv/bin/python ; argv[]=/root/projects/gba-products/"
        ".venv/bin/python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8005 ; ignore_errors=no ; pid=0 }",
        f"EnvironmentFiles={env_file} (ignore_errors=no)",
        "Environment=SOURCE_ENVIRONMENT=dev \"NOTE=two words\"",
    ])
    with patch.object(health, "systemctl", return_value=(0, shown)):
        runtime = health.unit_runtime("products")
    assert runtime["cwd"] == "/root/projects/gba-ai-services/gba-products"
    assert runtime["python"] == "/root/projects/gba-products/.venv/bin/python"
    assert runtime["environment"] == {"DB_NAME": "ConcordDb_V5", "DB_PASSWORD": "s3 cret", "REDIS_DB": "2",
                                      "SOURCE_ENVIRONMENT": "dev", "NOTE": "two words"}
    assert runtime["environment_names"] == ["NOTE", "SOURCE_ENVIRONMENT"]
    merged = health.runtime_environment(runtime)
    assert merged["SOURCE_ENVIRONMENT"] == "dev" and merged["PATH"] == os.environ["PATH"]
    with patch.object(health, "systemctl", return_value=(0, "LoadState=not-found\nWorkingDirectory=\nExecStart=")):
        assert health.unit_runtime("products") is None
    with patch.object(health, "systemctl", return_value=(1, "boom")):
        assert health.unit_runtime("products") is None


def test_watchdog_starts_only_enabled_units_that_are_down_and_records_it(tmp_path):
    systemd = FakeSystemd(active={"pricing": "inactive", "nba": "failed", "reco": "activating"},
                          enabled={"nba": "disabled"})
    with fake(systemd):
        report = watchdog.patrol(tmp_path)
        watchdog.record(tmp_path, report)
    assert systemd.started() == ["gba-pricing.service"]
    assert report["services"]["pricing"]["action"] == "started"
    assert report["services"]["nba"]["action"] == "none (unit is not enabled)"
    assert report["services"]["reco"]["action"] == "none"
    saved = json.loads((tmp_path / "watchdog.json").read_text())
    assert saved["actions"] == report["actions"] and saved["recent_actions"][0]["service"] == "pricing"
    with fake(systemd):
        watchdog.record(tmp_path, watchdog.patrol(tmp_path))
    saved = json.loads((tmp_path / "watchdog.json").read_text())
    assert saved["actions"] == [] and len(saved["recent_actions"]) == 1
    assert systemd.started() == ["gba-pricing.service"]


def test_watchdog_all_active_takes_no_action(tmp_path, capsys):
    systemd = FakeSystemd()
    with fake(systemd):
        report = watchdog.patrol(tmp_path)
    assert report["actions"] == [] and systemd.started() == []
    assert all(entry["action"] == "none" and entry["healthy"] for entry in report["services"].values())
    assert "no action" in capsys.readouterr().out


def test_watchdog_dry_run_and_maintenance_marker_start_nothing(tmp_path, capsys):
    systemd = FakeSystemd(active={"forecast": "inactive"})
    with fake(systemd):
        report = watchdog.patrol(tmp_path, dry_run=True)
        assert report["services"]["forecast"]["action"] == "would start"
        marker = tmp_path / "maintenance"
        marker.write_text("")
        fresh = watchdog.patrol(tmp_path)
        assert fresh["services"]["forecast"]["action"] == "not started: maintenance marker present"
        assert "WARNING" not in capsys.readouterr().out
        old = marker.stat().st_mtime - 7 * 3600
        os.utime(marker, (old, old))
        stale = watchdog.patrol(tmp_path)
    assert stale["maintenance"]["stale"] and stale["maintenance"]["age_seconds"] >= 7 * 3600
    assert "WARNING forgotten?" in capsys.readouterr().out
    assert systemd.started() == []
    assert not (tmp_path / "watchdog.json").exists()


def test_watchdog_reports_a_failed_start(tmp_path):
    systemd = FakeSystemd(active={"procure": "failed"}, start_works=False)
    with fake(systemd):
        report = watchdog.patrol(tmp_path)
    assert report["actions"][0]["action"] == "start failed (exit 1)"
    assert "Job for unit failed" in report["actions"][0]["error"]
