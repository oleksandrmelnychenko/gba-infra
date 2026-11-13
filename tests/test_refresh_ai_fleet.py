from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import fcntl

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_ai_fleet as refresh


def fleet_report(*, down=(), unhealthy=(), started=()):
    services = {}
    for name in refresh.health.SERVICES:
        bad = name in down or name in unhealthy
        services[name] = {"active": "inactive" if name in down else "active", "enabled": "enabled",
                          "up": name not in down, "healthy": not bad}
        if bad:
            services[name]["reason"] = "unit is inactive" if name in down else "HTTP 503, status 'degraded'"
    return {"checked_at": "2026-09-07T05:00:00+03:00", "maintenance": {"present": False},
            "started": list(started), "services": services, "down": list(down),
            "unhealthy": sorted(set(down) | set(unhealthy))}


@pytest.fixture(autouse=True)
def healthy_fleet():
    with patch.object(refresh.health, "check_fleet", return_value=fleet_report()) as fleet, \
            patch.object(refresh.health, "check", return_value={"up": True, "healthy": True}), \
            patch.object(refresh.health, "unit_runtime", return_value=None):
        yield fleet


def completing(calls):
    def invoke(command, **kwargs):
        calls.append((command[2], kwargs))
        Path(command[-1]).write_text(json.dumps({"status": "complete"}))
        return SimpleNamespace(returncode=0)
    return invoke


def test_daily_boundary_uses_kyiv():
    assert refresh.cycle_date(datetime(2026, 9, 7, 1, 59, tzinfo=ZoneInfo("UTC"))) == "2026-09-06"
    assert refresh.cycle_date(datetime(2026, 9, 7, 2, 0, tzinfo=ZoneInfo("UTC"))) == "2026-09-07"


def test_failure_resumes_only_unfinished_stages(tmp_path):
    calls = []
    fail = True

    def invoke(command, **kwargs):
        stage = command[2]
        calls.append(stage)
        if stage == "procure" and fail:
            return SimpleNamespace(returncode=1)
        Path(command[-1]).write_text(json.dumps({"status": "complete", "persisted": 3}))
        return SimpleNamespace(returncode=0)

    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), patch.object(refresh.subprocess, "run", side_effect=invoke):
        assert refresh.run(tmp_path, tmp_path / "state") == 1
        assert calls[0] == "nba_feedback"
        assert set(calls[1:]) == set(refresh.STAGES[1:-1])
        assert "nba" not in calls
        state = json.loads((tmp_path / "state/2026-09-07.json").read_text())
        assert state["status"] == "failed"
        assert state["stages"]["reco"]["status"] == "complete"
        fail = False
        calls.clear()
        assert refresh.run(tmp_path, tmp_path / "state") == 0
        assert calls == ["procure", "nba"]
        calls.clear()
        assert refresh.run(tmp_path, tmp_path / "state") == 0
        assert calls == []


def test_exit_zero_without_persistence_confirmation_fails(tmp_path):
    with patch.object(refresh.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
        assert refresh.run(tmp_path, tmp_path / "state") == 1


def test_operational_solvency_contract_and_broken_values():
    import verify_ai_fleet as gate
    from test_verify_ai_fleet import exact_solvency_health
    payload = exact_solvency_health("healthy")
    models = payload["model_readiness"]
    models.pop("forward_6m")
    models["operational_90d"] = {"ready": True, "kind": "deterministic_open_debt_control",
                                 "horizon_days": 90, "threshold_days": 90}
    assert gate.validate_health("solvency", payload)[0] == []
    for key, value in (("ready", False), ("kind", "unproven"), ("horizon_days", 180), ("threshold_days", 89)):
        original = models["operational_90d"][key]
        models["operational_90d"][key] = value
        assert gate.validate_health("solvency", payload)[0]
        models["operational_90d"][key] = original


def test_pricing_discount_reproduction_and_conflict_are_validated():
    import verify_ai_fleet as gate
    uid = "11111111-1111-1111-1111-111111111111"
    payload = {"product_id": 5, "product_net_uid": uid, "client_agreement_netuid": uid,
               "currency": "EUR", "as_of_date": "2026-09-07", "model_version": "pricing-ab-v3-coherent-discount",
               "baseline_price": 12.0, "recommended_price": 10.0, "price_floor": 9.0, "unit_cost_eur": 7.5,
               "discount_base_price": 12.0, "suggested_discount_pct": 16.67,
               "discount_band": {"min_pct": 10.0, "target_pct": 16.67, "max_pct": 20.0},
               "peer_band": {"p25": 9.0, "p50": 10.0, "p75": 11.0, "n": 20},
               "elastic_optimal_price": None, "rationale": "peer-median"}
    def validate():
        return gate.validate_pricing(payload, product_id=5, product_net_uid=uid,
                                     agreement_net_uid=uid, expected_as_of="2026-09-07")[0]
    assert validate() == []
    payload["discount_base_price"] = 13.0
    assert "suggested discount must reproduce recommended price" in validate()
    payload.update(recommended_price=None, suggested_discount_pct=None, discount_band=None,
                   rationale="constraints-conflict", confidence="low")
    assert validate() == []
    payload["recommended_price"] = 10.0
    assert validate()


def test_completed_cycle_is_not_retimestamped_and_stale_force_is_ignored(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "requests").mkdir()
    original = {"status": "complete", "run_id": "newer-run", "completed_at": "2026-09-07T05:40:00+03:00", "stages": {}}
    path = state_dir / "2026-09-07.json"
    path.write_text(json.dumps(original))
    (state_dir / "requests/refresh.json").write_text(json.dumps({"force": True, "expected_run_id": "older-run"}))
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), patch.object(refresh.subprocess, "run") as execute:
        assert refresh.run(tmp_path, state_dir) == 0
        execute.assert_not_called()
    assert json.loads(path.read_text()) == original
    assert not (state_dir / "requests/refresh.json").exists()


def test_current_manual_request_forces_a_new_verified_cycle(tmp_path):
    state_dir = tmp_path / "state"
    (state_dir / "requests").mkdir(parents=True)
    path = state_dir / "2026-09-07.json"
    path.write_text(json.dumps({"status": "complete", "run_id": "old-run", "stages": {}}))
    (state_dir / "requests/refresh.json").write_text(json.dumps({"force": True, "expected_run_id": "old-run"}))
    calls = []
    def invoke(command, **kwargs):
        calls.append(command[2])
        Path(command[-1]).write_text(json.dumps({"status": "complete"}))
        return SimpleNamespace(returncode=0)
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), patch.object(refresh.subprocess, "run", side_effect=invoke):
        assert refresh.run(tmp_path, state_dir) == 0
    state = json.loads(path.read_text())
    assert state["run_id"] != "old-run"
    assert set(calls) == set(refresh.STAGES)
    assert calls[0] == "nba_feedback" and calls[-1] == "nba"
    assert state["status"] == "complete"


def test_loss_floor_uses_minimum_price_attainable_with_cent_percent_discount():
    import verify_ai_fleet as gate
    uid = "11111111-1111-1111-1111-111111111111"
    payload = {"product_id": 5, "product_net_uid": uid, "client_agreement_netuid": uid,
               "currency": "EUR", "as_of_date": "2026-09-07", "model_version": "pricing-ab-v3-coherent-discount",
               "baseline_price": 85000.0, "recommended_price": 89609.10, "price_floor": 89600.0,
               "unit_cost_eur": 80000.0, "discount_base_price": 99999.0, "suggested_discount_pct": 10.39,
               "discount_band": {"min_pct": 0.0, "target_pct": 10.39, "max_pct": 10.39},
               "peer_band": {"p25": None, "p50": None, "p75": None, "n": 0},
               "elastic_optimal_price": None, "rationale": "below-margin-loss-flag"}
    def validate():
        return gate.validate_pricing(payload, product_id=5, product_net_uid=uid,
                                     agreement_net_uid=uid, expected_as_of="2026-09-07")[0]
    assert validate() == []
    payload["discount_band"]["max_pct"] = 10.40
    assert validate()  # Reject an arbitrary higher price when a lower feasible one exists.


def test_cycle_before_five_accepts_the_calendar_date_for_night_runs():
    night = datetime(2026, 9, 8, 3, 30, tzinfo=ZoneInfo("Europe/Kyiv"))
    assert refresh.allowed_cycle_dates(night) == ("2026-09-07", "2026-09-08")
    assert refresh.allowed_cycle_dates(night.replace(hour=6)) == ("2026-09-08",)
    assert refresh.main(["--cycle-date", "2020-01-01", "--check"]) == refresh.EXIT_ARGUMENTS


def test_night_cycle_for_the_calendar_date_is_recognised_by_the_morning_timer(tmp_path):
    calls = []
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing(calls)):
        assert refresh.run(tmp_path, tmp_path / "state", force=True, day="2026-09-08") == 0
    assert {stage for stage, _ in calls} == set(refresh.STAGES)
    assert calls[0][1]["cwd"] == str(tmp_path / "gba-nba")
    calls.clear()
    with patch.object(refresh, "cycle_date", return_value="2026-09-08"), \
            patch.object(refresh.subprocess, "run", side_effect=completing(calls)):
        assert refresh.run(tmp_path, tmp_path / "state") == 0
    assert calls == []


def test_unhealthy_service_after_the_stages_fails_the_cycle_and_names_it(tmp_path, healthy_fleet):
    calls = []
    healthy_fleet.side_effect = [fleet_report(), fleet_report(down=("pricing",))]
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing(calls)):
        assert refresh.run(tmp_path, tmp_path / "state") == refresh.EXIT_FAILED
        state = json.loads((tmp_path / "state/2026-09-07.json").read_text())
        assert state["status"] == "failed" and state["failed_services"] == ["pricing"]
        assert "gba-pricing" in state["failure"] and "completed_at" not in state
        assert state["health"]["post"]["down"] == ["pricing"]
        assert all(stage["status"] == "complete" for stage in state["stages"].values())
        calls.clear()
        healthy_fleet.side_effect = None
        assert refresh.run(tmp_path, tmp_path / "state") == 0
    assert calls == []
    state = json.loads((tmp_path / "state/latest.json").read_text())
    assert state["status"] == "complete" and "failure" not in state and "failed_services" not in state


def test_preflight_asks_for_a_start_and_the_result_is_recorded(tmp_path, healthy_fleet):
    healthy_fleet.side_effect = [fleet_report(started=("forecast",)), fleet_report()]
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing([])):
        assert refresh.run(tmp_path, tmp_path / "state") == 0
    assert healthy_fleet.call_args_list[0].kwargs == {"start_inactive": True}
    assert healthy_fleet.call_args_list[1].kwargs == {"start_inactive": False}
    state = json.loads((tmp_path / "state/2026-09-07.json").read_text())
    assert state["health"]["pre"]["started"] == ["forecast"]


def test_reco_web_down_fails_the_stage_that_calls_it(tmp_path):
    calls = []
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.health, "check", return_value={"up": False, "healthy": False}), \
            patch.object(refresh.subprocess, "run", side_effect=completing(calls)):
        assert refresh.run(tmp_path, tmp_path / "state") == refresh.EXIT_FAILED
    assert calls == []
    state = json.loads((tmp_path / "state/2026-09-07.json").read_text())
    assert "gba-reco" in state["stages"]["nba_feedback"]["error"]
    assert state["failed_stages"][0] == "nba_feedback"


def test_stage_uses_the_tree_interpreter_and_environment_of_the_web_unit(tmp_path):
    calls = []
    def runtime(service):
        if service != "products":
            return None
        return {"cwd": "/srv/monorepo/gba-products", "python": "/srv/venv/bin/python",
                "environment": {"SOURCE_ENVIRONMENT": "dev"}, "environment_files": ["/srv/.env"],
                "environment_names": ["SOURCE_ENVIRONMENT"]}
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.health, "unit_runtime", side_effect=runtime), \
            patch.object(refresh.subprocess, "run", side_effect=completing(calls)):
        assert refresh.run(tmp_path, tmp_path / "state") == 0
    products = next(kwargs for stage, kwargs in calls if stage == "products")
    assert products["cwd"] == "/srv/monorepo/gba-products"
    assert products["env"]["SOURCE_ENVIRONMENT"] == "dev"
    state = json.loads((tmp_path / "state/2026-09-07.json").read_text())
    assert state["stages"]["products"]["python"] == "/srv/venv/bin/python"
    assert state["stages"]["products"]["runtime_source"] == "unit"
    assert state["stages"]["reco"]["runtime_source"] == "fallback"


def test_each_stage_is_published_to_latest_while_the_cycle_runs(tmp_path):
    seen = {}
    def invoke(command, **kwargs):
        if command[2] == "nba":
            seen.update(json.loads((tmp_path / "state/latest.json").read_text()))
        Path(command[-1]).write_text(json.dumps({"status": "complete"}))
        return SimpleNamespace(returncode=0)
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=invoke):
        assert refresh.run(tmp_path, tmp_path / "state") == 0
    assert seen["status"] == "running"
    assert seen["stages"]["products"]["status"] == "complete" and seen["stages"]["products"]["completed_at"]
    assert seen["stages"]["nba"]["status"] == "running"


def test_forced_run_keeps_the_earlier_run_record_and_marks_the_logs(tmp_path):
    state_dir = tmp_path / "state"
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing([])):
        assert refresh.run(tmp_path, state_dir) == 0
        first = json.loads((state_dir / "2026-09-07.json").read_text())
        assert refresh.run(tmp_path, state_dir, force=True) == 0
    second = json.loads((state_dir / "2026-09-07.json").read_text())
    assert second["run_id"] != first["run_id"]
    assert json.loads((state_dir / f"2026-09-07-{first['run_id']}.json").read_text()) == first
    assert json.loads((state_dir / f"2026-09-07-{second['run_id']}.json").read_text()) == second
    headers = [line for line in (state_dir / "2026-09-07-reco.log").read_text().splitlines()
               if line.startswith("=====")]
    assert len(headers) == 2 and first["run_id"] in headers[0] and second["run_id"] in headers[1]


def test_legacy_day_file_without_a_run_record_is_archived_before_a_forced_run(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    legacy = {"cycle_date": "2026-09-07", "status": "complete", "run_id": "legacy", "stages": {},
              "started_at": "2026-09-07T05:00:00+03:00"}
    (state_dir / "2026-09-07.json").write_text(json.dumps(legacy))
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing([])):
        assert refresh.run(tmp_path, state_dir, force=True) == 0
    assert json.loads((state_dir / "2026-09-07-legacy.json").read_text()) == legacy


def test_force_is_dropped_when_the_cycle_started_after_the_requested_instant(tmp_path):
    state_dir = tmp_path / "state"
    calls = []
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing(calls)):
        assert refresh.run(tmp_path, state_dir) == 0
        first = json.loads((state_dir / "2026-09-07.json").read_text())
        started = datetime.fromisoformat(first["started_at"])
        calls.clear()
        assert refresh.run(tmp_path, state_dir, force=True, fresh_since=started.replace(year=2020)) == 0
        assert calls == []
        assert refresh.run(tmp_path, state_dir, force=True, fresh_since=started.replace(year=2090)) == 0
    assert {stage for stage, _ in calls} == set(refresh.STAGES)


def test_held_lock_is_a_distinct_exit_code_and_can_be_waited_for(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    with (state_dir / "refresh.lock").open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with patch.object(refresh.subprocess, "run") as execute:
            assert refresh.run(tmp_path, state_dir, force=True) == refresh.EXIT_LOCK_BUSY == 75
            execute.assert_not_called()
        assert not list(state_dir.glob("*.json"))
        naps = []
        def release(seconds):
            naps.append(seconds)
            fcntl.flock(holder, fcntl.LOCK_UN)
        with patch.object(refresh.time, "sleep", side_effect=release), \
                patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
                patch.object(refresh.subprocess, "run", side_effect=completing([])):
            assert refresh.run(tmp_path, state_dir, wait_lock=60) == 0
        assert len(naps) == 1


def test_retention_removes_only_dated_files_older_than_sixty_days(tmp_path):
    for name in ("2026-07-08.json", "2026-07-08-reco.log", "2026-07-08-abc.json", "2026-07-09.json",
                 "2026-09-06-nba.log", "latest.json", "refresh.lock", "watchdog.json", "maintenance"):
        (tmp_path / name).write_text("x")
    (tmp_path / "requests").mkdir()
    assert refresh.prune(tmp_path, "2026-09-07") == 3
    assert sorted(item.name for item in tmp_path.iterdir()) == [
        "2026-07-09.json", "2026-09-06-nba.log", "latest.json", "maintenance", "refresh.lock",
        "requests", "watchdog.json"]


def test_check_mode_reports_and_starts_nothing(tmp_path, healthy_fleet, capsys):
    with patch.object(refresh.subprocess, "run") as execute:
        assert refresh.check(tmp_path, tmp_path / "state", day="2026-09-07", imports=False) == refresh.EXIT_FAILED
        execute.assert_not_called()
    report = json.loads(capsys.readouterr().out)
    assert report["a_run_now_would"].startswith("start a new cycle")
    assert healthy_fleet.call_args.kwargs == {"start_inactive": False}
    assert not (tmp_path / "state").exists()


def test_cycle_waits_for_a_running_synchronization_and_records_the_wait(tmp_path, capsys):
    state_dir = tmp_path / "state"
    sync_lock = tmp_path / "run.lock"
    with sync_lock.open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        naps = []
        def release(seconds):
            naps.append(seconds)
            if len(naps) == 2:
                fcntl.flock(holder, fcntl.LOCK_UN)
        with patch.object(refresh.time, "sleep", side_effect=release), \
                patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
                patch.object(refresh.subprocess, "run", side_effect=completing([])):
            assert refresh.run(tmp_path, state_dir, wait_sync=5400, sync_lock=sync_lock) == 0
    assert naps == [30, 30]
    state = json.loads((state_dir / "2026-09-07.json").read_text())
    assert state["status"] == "complete" and state["sync_wait"]["still_running"] is False
    assert "The synchronization run ended" in capsys.readouterr().out


def test_wait_for_a_synchronization_is_bounded_and_the_cycle_says_it_ran_early(tmp_path, capsys):
    state_dir = tmp_path / "state"
    sync_lock = tmp_path / "run.lock"
    clock = [1000.0]
    def nap(seconds):
        clock[0] += seconds
    with sync_lock.open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with patch.object(refresh.time, "sleep", side_effect=nap), \
                patch.object(refresh.time, "time", side_effect=lambda: clock[0]), \
                patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
                patch.object(refresh.subprocess, "run", side_effect=completing([])):
            assert refresh.run(tmp_path, state_dir, wait_sync=5400, sync_lock=sync_lock) == 0
    state = json.loads((state_dir / "2026-09-07.json").read_text())
    assert state["sync_wait"] == {"waited_seconds": 5400, "still_running": True}
    assert state["status"] == "complete"
    assert "still running after 5400 s (runner lock)" in capsys.readouterr().out


def test_no_wait_without_the_option_without_a_lock_file_or_without_a_holder(tmp_path):
    sync_lock = tmp_path / "run.lock"
    with patch.object(refresh.time, "sleep") as nap:
        assert refresh.wait_for_sync(sync_lock, 5400) is None
        sync_lock.touch()
        assert refresh.wait_for_sync(sync_lock, 5400) is None
        with sync_lock.open("a") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            assert refresh.wait_for_sync(sync_lock, 0) is None
            assert refresh.sync_running(sync_lock)
        nap.assert_not_called()
    assert not (tmp_path / "absent.lock").exists() and not refresh.sync_running(tmp_path / "absent.lock")


def test_wait_for_the_synchronization_happens_before_the_fleet_lock_is_taken(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    sync_lock = tmp_path / "run.lock"
    seen = []
    def waiting(path, limit):
        with (state_dir / "refresh.lock").open("a") as probe:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append((path, limit))
        return None
    with patch.object(refresh, "wait_for_sync", side_effect=waiting), \
            patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing([])):
        assert refresh.run(tmp_path, state_dir, wait_sync=5400, sync_lock=sync_lock) == 0
    assert seen == [(sync_lock, 5400)]


def test_timer_unit_waits_for_the_synchronization_and_the_runner_does_not_wait_for_itself():
    root = Path(__file__).resolve().parents[1]
    unit = (root / "systemd/gba-ai-fleet-refresh.service").read_text()
    assert "refresh_ai_fleet.py --wait-sync 5400 --wait-lock 21600" in unit
    assert "--wait-sync" not in (root / "scripts/gba_sync_run.py").read_text()


def tokens(calls):
    return {stage: kwargs["command"][kwargs["command"].index("--token") + 1] for stage, kwargs in calls}


def recording(calls, failing=()):
    def invoke(command, **kwargs):
        calls.append((command[2], {**kwargs, "command": command}))
        if command[2] in failing:
            return SimpleNamespace(returncode=1)
        Path(command[-1]).write_text(json.dumps({"status": "complete"}))
        return SimpleNamespace(returncode=0)
    return invoke


def test_only_reruns_the_named_stage_of_a_complete_cycle_and_leaves_the_rest(tmp_path, healthy_fleet, capsys):
    calls = []
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=recording(calls)):
        assert refresh.run(tmp_path, tmp_path / "state") == 0
        before = json.loads((tmp_path / "state/2026-09-07.json").read_text())
        full_tokens = tokens(calls)
        calls.clear()
        healthy_fleet.reset_mock()
        capsys.readouterr()
        assert refresh.run(tmp_path, tmp_path / "state", only=("products",)) == 0
    assert [stage for stage, _ in calls] == ["products"]
    assert tokens(calls)["products"] != full_tokens["products"]
    after = json.loads((tmp_path / "state/2026-09-07.json").read_text())
    assert after["status"] == "complete"
    assert after["run_id"] == before["run_id"]
    assert after["completed_at"] == before["completed_at"]
    for stage in refresh.STAGES:
        if stage != "products":
            assert after["stages"][stage] == before["stages"][stage]
    assert after["stages"]["products"]["attempt"] == 2
    assert after["stages"]["products"]["only_run_id"] == tokens(calls)["products"]
    only_run = after["only_runs"][-1]
    assert only_run["status"] == "complete" and only_run["stages"] == ["products"]
    assert "pricing" in only_run["not_requested"] and "products" not in only_run["not_requested"]
    assert json.loads((tmp_path / "state/latest.json").read_text()) == after
    assert [call.kwargs for call in healthy_fleet.call_args_list] == [{"start_inactive": True}, {"start_inactive": False}]
    output = capsys.readouterr().out
    assert "Stage pricing: not requested" in output
    assert "Stage products: complete" in output


def test_only_uses_the_unit_tree_interpreter_and_environment(tmp_path):
    calls = []

    def runtime(service):
        if service != "products":
            return None
        return {"cwd": "/srv/monorepo/gba-products", "python": "/srv/venv/bin/python",
                "environment": {"DB_HOST": "from-the-unit-file"}, "environment_files": ["/srv/products/.env"],
                "environment_names": []}
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.health, "unit_runtime", side_effect=runtime), \
            patch.object(refresh.subprocess, "run", side_effect=recording(calls)):
        assert refresh.run(tmp_path, tmp_path / "state", only=("products",)) == 0
    (stage, kwargs), = calls
    assert stage == "products"
    assert kwargs["cwd"] == "/srv/monorepo/gba-products"
    assert kwargs["command"][0] == "/srv/venv/bin/python"
    assert kwargs["env"]["DB_HOST"] == "from-the-unit-file"


def test_only_on_a_day_without_a_cycle_is_partial_and_the_full_run_finishes_it(tmp_path):
    calls = []
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=recording(calls)):
        assert refresh.run(tmp_path, tmp_path / "state", only=("pricing", "products")) == 0
        state = json.loads((tmp_path / "state/2026-09-07.json").read_text())
        assert state["status"] == "partial"
        assert set(state["stages"]) == {"pricing", "products"}
        assert "completed_at" not in state
        calls.clear()
        assert refresh.run(tmp_path, tmp_path / "state") == 0
    assert "products" not in [stage for stage, _ in calls] and "pricing" not in [stage for stage, _ in calls]
    assert {stage for stage, _ in calls} == set(refresh.STAGES) - {"pricing", "products"}
    assert json.loads((tmp_path / "state/2026-09-07.json").read_text())["status"] == "complete"


def test_only_failure_marks_the_day_failed_and_the_next_run_resumes_that_stage(tmp_path):
    calls = []
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"):
        with patch.object(refresh.subprocess, "run", side_effect=recording(calls)):
            assert refresh.run(tmp_path, tmp_path / "state") == 0
        with patch.object(refresh.subprocess, "run", side_effect=recording(calls, failing=("pricing",))):
            assert refresh.run(tmp_path, tmp_path / "state", only=("pricing",)) == refresh.EXIT_FAILED
        state = json.loads((tmp_path / "state/2026-09-07.json").read_text())
        assert state["status"] == "failed"
        assert state["failed_stages"] == ["pricing"]
        assert state["only_runs"][-1]["status"] == "failed"
        assert state["stages"]["reco"]["status"] == "complete"
        calls.clear()
        with patch.object(refresh.subprocess, "run", side_effect=recording(calls)):
            assert refresh.run(tmp_path, tmp_path / "state") == 0
    assert [stage for stage, _ in calls] == ["pricing"]


def test_only_fails_when_a_service_is_unhealthy_afterwards(tmp_path, healthy_fleet):
    calls = []
    healthy_fleet.side_effect = [fleet_report(), fleet_report(unhealthy=("products",))]
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=recording(calls)):
        assert refresh.run(tmp_path, tmp_path / "state", only=("products",)) == refresh.EXIT_FAILED
    state = json.loads((tmp_path / "state/2026-09-07.json").read_text())
    assert state["failed_services"] == ["products"]
    assert state["only_runs"][-1]["status"] == "failed"


def test_only_takes_the_same_lock_and_leaves_a_manual_request_alone(tmp_path):
    state_dir = tmp_path / "state"
    (state_dir / "requests").mkdir(parents=True)
    (state_dir / "requests/refresh.json").write_text(json.dumps({"force": True, "expected_run_id": "x"}))
    with (state_dir / "refresh.lock").open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with patch.object(refresh.subprocess, "run") as execute:
            assert refresh.run(tmp_path, state_dir, only=("products",)) == refresh.EXIT_LOCK_BUSY
            execute.assert_not_called()
    calls = []
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=recording(calls)):
        assert refresh.run(tmp_path, state_dir, only=("products",)) == 0
    assert (state_dir / "requests/refresh.json").exists()


def test_only_nba_needs_reco_and_keeps_stage_order(tmp_path):
    calls = []
    with patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=recording(calls)):
        assert refresh.run(tmp_path, tmp_path / "state", only=refresh.parse_only("nba,reco,nba_feedback")) == 0
        assert [stage for stage, _ in calls] == ["nba_feedback", "reco", "nba"]
        calls.clear()
        with patch.object(refresh.health, "check", return_value={"up": False, "healthy": False}):
            assert refresh.run(tmp_path, tmp_path / "state", only=("nba",)) == refresh.EXIT_FAILED
    assert calls == []


def test_only_argument_validation(capsys):
    assert refresh.parse_only("products, pricing") == ("pricing", "products")
    assert refresh.main(["--only", "products,billing"]) == refresh.EXIT_ARGUMENTS
    assert "unknown stage: 'billing'" in capsys.readouterr().out


def test_check_with_only_reports_other_stages_as_not_requested(tmp_path, capsys):
    with patch.object(refresh.subprocess, "run") as execute:
        refresh.check(tmp_path, tmp_path / "state", day="2026-09-07", imports=False, only=("products",))
        execute.assert_not_called()
    report = json.loads(capsys.readouterr().out)
    assert report["stages"]["pricing"] == "not requested"
    assert report["stages"]["products"]["source"] == "fallback"


class FakeSyncServer:
    def __init__(self, statuses, *, token_code=200, status_code=200, expire_first_token=False):
        self.statuses, self.token_code, self.status_code = list(statuses), token_code, status_code
        self.expire_first_token = expire_first_token
        self.tokens, self.status_calls, self.seen = 0, 0, []

    def __call__(self, url, *, data=None, headers=None, method=None, timeout=60):
        self.seen.append((url, data, headers))
        if url.endswith("/usermanagement/token"):
            self.tokens += 1
            assert json.loads(data) == {"Username": "svc", "Password": "s3cret-marker"}
            if self.token_code != 200:
                return self.token_code, b"{}"
            return 200, json.dumps({"Body": {"AccessToken": f"t0ken-{self.tokens}"}}).encode()
        assert url == "http://server/api/v1/uk/data/sync/status"
        self.status_calls += 1
        if self.expire_first_token and headers["Authorization"] == "Bearer t0ken-1":
            return 401, b"{}"
        if self.status_code != 200:
            return self.status_code, b"{}"
        busy = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(busy, Exception):
            raise busy
        return 200, json.dumps({"Body": {"IsInProgress": busy, "InMemorySynchronizationInProgress": busy}}).encode()


@pytest.fixture
def sync_credential(tmp_path):
    path = tmp_path / "credential.json"
    path.write_text(json.dumps({"Username": "svc", "Password": "s3cret-marker"}))
    path.chmod(0o600)
    with patch.object(refresh.sync_runner, "CREDENTIAL_OWNER", path.stat().st_uid):
        yield path


def ticking():
    clock = [1000.0]
    def nap(seconds):
        clock[0] += seconds
    return patch.object(refresh.time, "sleep", side_effect=nap), \
        patch.object(refresh.time, "time", side_effect=lambda: clock[0])


def test_cycle_waits_for_a_synchronization_started_from_the_console(tmp_path, sync_credential, capsys):
    state_dir, sync_lock = tmp_path / "state", tmp_path / "run.lock"
    server = FakeSyncServer([True, True, True, False])
    nap, clock = ticking()
    with nap, clock, patch.object(refresh.sync_runner, "http_request", side_effect=server), \
            patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing([])):
        assert refresh.run(tmp_path, state_dir, wait_sync=5400, sync_lock=sync_lock,
                           sync_server="http://server", sync_credential=sync_credential) == 0
    state = json.loads((state_dir / "2026-09-07.json").read_text())
    assert state["status"] == "complete"
    assert state["sync_wait"] == {"waited_seconds": 60, "still_running": False, "waited_for": ["server status"]}
    assert server.tokens == 1 and server.status_calls == 4
    output = capsys.readouterr().out
    assert "A synchronization is running (server status); waiting up to 5400 s" in output
    assert "s3cret-marker" not in output and "t0ken" not in output
    assert "s3cret-marker" not in json.dumps(state) and "t0ken" not in json.dumps(state)


def test_server_wait_has_the_same_bound_as_the_lock_wait(tmp_path, sync_credential, capsys):
    server = FakeSyncServer([True])
    nap, clock = ticking()
    with nap, clock, patch.object(refresh.sync_runner, "http_request", side_effect=server):
        result = refresh.wait_for_sync(tmp_path / "run.lock", 5400,
                                       probe=refresh.ServerSyncProbe("http://server/", sync_credential))
    assert result == {"waited_seconds": 5400, "still_running": True, "waited_for": ["server status"]}
    assert "still running after 5400 s (server status)" in capsys.readouterr().out


def test_idle_server_and_free_lock_do_not_wait_and_leave_no_note(tmp_path, sync_credential):
    server = FakeSyncServer([False])
    with patch.object(refresh.time, "sleep") as nap, \
            patch.object(refresh.sync_runner, "http_request", side_effect=server):
        probe = refresh.ServerSyncProbe("http://server", sync_credential)
        assert refresh.wait_for_sync(tmp_path / "run.lock", 5400, probe=probe) is None
        assert refresh.wait_for_sync(tmp_path / "run.lock", 0, probe=probe) is None
    nap.assert_not_called()
    assert server.status_calls == 1 and server.tokens == 1


@pytest.mark.parametrize("server, reason", [
    (FakeSyncServer([OSError("connection refused")]), "OSError: connection refused"),
    (FakeSyncServer([False], token_code=401), "the token call answered HTTP 401"),
    (FakeSyncServer([False], token_code=503), "the token call answered HTTP 503"),
    (FakeSyncServer([False], status_code=403), "the status call answered HTTP 403 without IsInProgress"),
    (FakeSyncServer([False], status_code=502), "the status call answered HTTP 502 without IsInProgress"),
])
def test_unavailable_or_unauthorised_server_falls_back_to_the_lock_and_says_why(
        tmp_path, sync_credential, capsys, server, reason):
    state_dir, sync_lock = tmp_path / "state", tmp_path / "run.lock"
    with patch.object(refresh.time, "sleep") as nap, \
            patch.object(refresh.sync_runner, "http_request", side_effect=server), \
            patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing([])):
        assert refresh.run(tmp_path, state_dir, wait_sync=5400, sync_lock=sync_lock,
                           sync_server="http://server", sync_credential=sync_credential) == 0
    nap.assert_not_called()
    state = json.loads((state_dir / "2026-09-07.json").read_text())
    assert state["status"] == "complete"
    assert state["sync_wait"]["waited_seconds"] == 0 and state["sync_wait"]["still_running"] is False
    assert state["sync_wait"]["waited_for"] == [] and reason in state["sync_wait"]["server_fallback"]
    assert "only the runner lock" in capsys.readouterr().out


def test_missing_or_unsafe_credential_falls_back_without_calling_the_server(tmp_path, sync_credential):
    with patch.object(refresh.sync_runner, "http_request") as http:
        absent = refresh.wait_for_sync(tmp_path / "run.lock", 5400,
                                       probe=refresh.ServerSyncProbe("http://server", tmp_path / "absent.json"))
        sync_credential.chmod(0o644)
        unsafe = refresh.wait_for_sync(tmp_path / "run.lock", 5400,
                                       probe=refresh.ServerSyncProbe("http://server", sync_credential))
    http.assert_not_called()
    assert "cannot be read" in absent["server_fallback"] and "is refused" in unsafe["server_fallback"]
    assert absent["still_running"] is False and unsafe["waited_for"] == []


def test_server_down_while_the_runner_holds_the_lock_keeps_the_lock_rule(tmp_path, sync_credential):
    sync_lock = tmp_path / "run.lock"
    server = FakeSyncServer([OSError("connection refused")])
    with sync_lock.open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        naps = []
        def release(seconds):
            naps.append(seconds)
            if len(naps) == 2:
                fcntl.flock(holder, fcntl.LOCK_UN)
        with patch.object(refresh.time, "sleep", side_effect=release), \
                patch.object(refresh.sync_runner, "http_request", side_effect=server):
            result = refresh.wait_for_sync(sync_lock, 5400,
                                           probe=refresh.ServerSyncProbe("http://server", sync_credential))
    assert naps == [30, 30]
    assert result["still_running"] is False and result["waited_for"] == ["runner lock"]
    assert "connection refused" in result["server_fallback"]


def test_expired_token_is_renewed_once_and_both_causes_are_named(tmp_path, sync_credential):
    sync_lock = tmp_path / "run.lock"
    server = FakeSyncServer([True, False], expire_first_token=True)
    with sync_lock.open("a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        def release(seconds):
            fcntl.flock(holder, fcntl.LOCK_UN)
        with patch.object(refresh.time, "sleep", side_effect=release), \
                patch.object(refresh.sync_runner, "http_request", side_effect=server):
            result = refresh.wait_for_sync(sync_lock, 5400,
                                           probe=refresh.ServerSyncProbe("http://server", sync_credential))
    assert server.tokens == 2
    assert result["waited_for"] == ["runner lock", "server status"] and "server_fallback" not in result


def test_timer_entry_point_checks_the_server_by_default_and_can_be_told_not_to(tmp_path):
    with patch.object(refresh, "run", return_value=0) as run:
        assert refresh.main(["--wait-sync", "5400", "--state-dir", str(tmp_path)]) == 0
        assert run.call_args.kwargs["sync_server"] == "http://127.0.0.1:35981"
        assert run.call_args.kwargs["sync_credential"] == Path("/etc/gba-sync/credential.json")
        assert refresh.main(["--wait-sync", "5400", "--sync-server", "", "--state-dir", str(tmp_path)]) == 0
        assert run.call_args.kwargs["sync_server"] == ""
    with patch.object(refresh, "wait_for_sync", return_value=None) as wait, \
            patch.object(refresh, "cycle_date", return_value="2026-09-07"), \
            patch.object(refresh.subprocess, "run", side_effect=completing([])):
        assert refresh.run(tmp_path, tmp_path / "state", sync_server="http://server",
                           sync_credential=tmp_path / "c.json") == 0
        assert wait.call_args.kwargs == {}
