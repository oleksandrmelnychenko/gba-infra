from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import refresh_ai_fleet as refresh


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
