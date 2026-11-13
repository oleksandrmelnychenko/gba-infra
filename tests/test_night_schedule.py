from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_step_times as steps

SYSTEMD = Path(__file__).resolve().parents[1] / "systemd"
SYNC_UNITS = "gba-sync@light.service gba-sync@full.service gba-sync@refresh.service"


def calendar(text):
    return [line.split("=", 1)[1] for line in text.splitlines() if line.startswith("OnCalendar=")]


def test_night_order_is_sync_then_statistics_then_ai_then_e2e():
    sync = calendar((SYSTEMD / "gba-sync-nightly.timer").read_text())
    statistics = calendar((SYSTEMD / "gba-sql-statistics@dev.timer.d/after-sync.conf").read_text())
    fleet = calendar((SYSTEMD / "gba-ai-fleet-refresh.timer").read_text())
    e2e = calendar((SYSTEMD / "gba-e2e-nightly.timer").read_text())
    assert sync == ["*-*-* 02:00:00 Europe/Kyiv"]
    assert statistics == ["", "*-*-* 04:15:00 Europe/Kyiv"]
    assert fleet == ["*-*-* 05:00:00 Europe/Kyiv"]
    assert e2e == ["*-*-* 06:15:00 Europe/Kyiv"]


def test_statistics_and_e2e_queue_behind_a_running_sync_and_never_catch_up_by_day():
    statistics = (SYSTEMD / "gba-sql-statistics@dev.service.d/after-sync.conf").read_text()
    e2e = (SYSTEMD / "gba-e2e-nightly.service").read_text()
    assert f"After={SYNC_UNITS}" in statistics.splitlines()
    assert f"After={SYNC_UNITS}" in e2e.splitlines()
    assert "After=gba-sql-statistics@dev.service gba-ai-fleet-refresh.service" in e2e.splitlines()
    assert "Persistent=false" in (SYSTEMD / "gba-sql-statistics@dev.timer.d/after-sync.conf").read_text()
    assert "Persistent=false" in (SYSTEMD / "gba-e2e-nightly.timer").read_text()


def test_statistics_template_shared_with_prod_is_left_as_it_was():
    template = (SYSTEMD / "gba-sql-statistics@.timer").read_text()
    assert "OnCalendar=*-*-* 03:30:00 Europe/Kyiv" in template and "Persistent=true" in template
    assert not (SYSTEMD / "gba-sql-statistics@prod.timer.d").exists()


def block(event, at):
    return (f"  Pipeline run ID: {event['RunId']} \n  Sync started by: x \n  Log message:\n"
            f"{json.dumps(event)} \n  Triggered at: {at} UTC \n\n")


def event(name, amg, duration, step=None, operation="Products", actor="SyncProductsWorkerActor"):
    return {"Severity": "INFO", "Event": name, "RunId": "r1", "Actor": actor, "OperationType": operation,
            "ForAmg": amg, "Step": step, "DurationMs": duration, "TriggeredAtUtc": "local time, not used"}


def test_step_table_reads_runner_sessions_and_server_durations(tmp_path, capsys):
    runner_log = tmp_path / "run.log"
    lines = []
    for run, day in (("a", 12), ("b", 13)):
        lines += [
            {"at": f"2026-11-{day}T02:00:10+02:00", "run": run, "step": "session", "base": "amg", "accepted": 200},
            {"at": f"2026-11-{day}T02:12:10+02:00", "run": run, "step": "session", "base": "amg", "ok": True,
             "status": "Finished", "seconds": 720},
            {"at": f"2026-11-{day}T02:12:20+02:00", "run": run, "step": "session", "base": "fenix", "accepted": 200},
            {"at": f"2026-11-{day}T03:12:20+02:00", "run": run, "step": "session", "base": "fenix", "ok": day == 12,
             "status": "Failed", "seconds": 3600}]
    runner_log.write_text("not json\n" + "".join(json.dumps(line) + "\n" for line in lines))
    log = tmp_path / "data_sync_log.txt"
    log.write_text(
        block(event("STEP_END", True, 120000, "Products.Prices"), "11/12/2026 00:05:00")
        + block(event("RUN_END", True, 300000), "11/12/2026 00:06:00")
        + block(event("STEP_END", True, 5000, "Products.Slugs"), "11/12/2026 00:06:30")
        + block(event("STEP_START", True, None, "Products.Prices"), "11/13/2026 00:01:00")
        + block(event("STEP_END", True, 240000, "Products.Prices"), "11/13/2026 00:05:00")
        + block(event("RUN_END", True, 360000), "11/13/2026 00:06:00")
        + block(event("RETENTION_COMPLETED", True, 900000, "Refresh.Retention", "Daily", "RefreshActor"),
                "11/13/2026 00:12:00")
        + block(event("RUN_END", False, 600000, operation="Daily", actor="DailyWorkerActor"),
                "11/13/2026 00:40:00")
        + block(event("RUN_END", False, 999000), "11/13/2026 05:00:00"))
    assert steps.main(["--runs", "2", "--log", str(log), "--runner-log", str(runner_log)]) == 0
    output = capsys.readouterr().out
    amg, fenix = output.split("== FENIX")
    rows = {line.strip().split("  ")[0]: line.split() for line in amg.splitlines()}
    assert rows["SESSION wall time"][-3:] == ["12.0", "12.0", "+0.0"]
    assert rows["outside worker runs"][-3:] == ["7.0", "6.0", "-1.0"]
    assert rows["Products: Products.Prices"][-3:] == ["2.0", "4.0", "+2.0"]
    assert rows["RETENTION_COMPLETED [Refresh.Retention]"][-1] == "15.0"
    assert "Products.Slugs" not in amg
    assert "FAILED Failed" in fenix and "RUN Daily (DailyWorker)" in fenix and "16.6" not in fenix
    assert steps.main(["--runs", "1", "--log", str(log), "--runner-log", str(runner_log)]) == 0
    assert "last-prev" not in capsys.readouterr().out


def test_step_table_accepts_sessions_the_runner_did_not_start_and_rejects_bad_ones(tmp_path, capsys):
    log = tmp_path / "data_sync_log.txt"
    log.write_text(block(event("RUN_END", False, 60000), "11/11/2026 06:10:00"))
    assert steps.main(["--runs", "0", "--log", str(log), "--runner-log", str(tmp_path / "absent.log"),
                       "--session", "evening,fenix,2026-11-11T06:05:39,2026-11-11T07:10:27"]) == 0
    assert "evening: manual" in capsys.readouterr().out
    assert steps.main(["--log", str(log), "--session", "x,other,2026-11-11T06:00:00,2026-11-11T07:00:00"]) == 2
    assert steps.main(["--log", str(log), "--runner-log", str(tmp_path / "absent.log")]) == 1
    session = steps.manual_session("x,amg,2026-11-11T06:00:00,2026-11-11T07:00:00+02:00".replace("06", "04"))
    assert session["start"] == datetime(2026, 11, 11, 4, tzinfo=timezone.utc)
