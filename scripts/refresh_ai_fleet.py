#!/usr/bin/env python3
"""Durable daily fleet refresh. Same entry point for the timer and manual runs.

Each service runs with its own interpreter, configuration and model artifacts.
Success is recorded only after its adapter confirms persisted results. A retry
resumes incomplete stages. flock prevents timer/manual/retry overlap.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from datetime import datetime, timedelta
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo

STAGES = ("nba_feedback", "reco", "procure", "solvency", "pricing", "products", "forecast", "nba")


def cycle_date(now: datetime | None = None) -> str:
    now = (now or datetime.now(ZoneInfo("Europe/Kyiv"))).astimezone(ZoneInfo("Europe/Kyiv"))
    return (now.date() if now.hour >= 5 else now.date() - timedelta(days=1)).isoformat()


def save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def run(root: Path, state_dir: Path, *, force: bool = False) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "refresh.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A fleet refresh is already running", flush=True)
            return 0
        day = cycle_date()
        path = state_dir / f"{day}.json"
        previous_state = json.loads(path.read_text()) if path.exists() else None
        request_path = state_dir / "requests/refresh.json"
        if request_path.exists():
            request = json.loads(request_path.read_text())
            # A duplicate request referring to a previous run cannot force a
            # second refresh after the first request has already started one.
            force = force or (request.get("force") is True and previous_state is not None
                              and request.get("expected_run_id") == previous_state.get("run_id"))
            request_path.unlink()
        if previous_state and previous_state.get("status") == "complete" and not force:
            print("This cycle is already complete; timestamps unchanged", flush=True)
            return 0
        state = previous_state if previous_state and not force else {
            "cycle_date": day, "run_id": uuid.uuid4().hex, "timezone": "Europe/Kyiv",
            "started_at": datetime.now().astimezone().isoformat(), "stages": {},
        }
        state["status"] = "running"
        save(path, state)
        state_lock = RLock()

        def execute(stage: str) -> bool:
            previous = state["stages"].get(stage, {})
            if previous.get("status") == "complete":
                return True
            service = "nba" if stage == "nba_feedback" else stage
            home = root / f"gba-{service}"
            result_file = state_dir / f"{day}-{stage}-result.json"
            result_file.unlink(missing_ok=True)
            log_file = state_dir / f"{day}-{stage}.log"
            entry = {"status": "running", "attempt": previous.get("attempt", 0) + 1,
                     "started_at": datetime.now().astimezone().isoformat()}
            with state_lock:
                state["stages"][stage] = entry
                save(path, state)
            print(f"Starting {stage}, attempt {entry['attempt']}", flush=True)
            with log_file.open("a") as output:
                try:
                    result = subprocess.run(
                        [str(home / ".venv/bin/python"), str(Path(__file__).with_name("refresh_ai_service.py")),
                         stage, "--as-of", day, "--token", state["run_id"], "--result", str(result_file)],
                        cwd=home, stdout=output, stderr=subprocess.STDOUT, timeout=6 * 3600,
                    )
                    if result.returncode:
                        raise RuntimeError(f"service exited {result.returncode}; see {log_file}")
                    detail = json.loads(result_file.read_text())
                    if detail.get("status") != "complete":
                        raise RuntimeError("service did not confirm completion")
                    with state_lock:
                        entry.update(result=detail)
                except Exception as exc:
                    with state_lock:
                        entry.update(status="failed", error=str(exc))
                        save(path, state)
                    print(f"Failed {stage}: {exc}", flush=True)
                    return False
            with state_lock:
                entry.update(status="complete", completed_at=datetime.now().astimezone().isoformat())
                save(path, state)
            print(f"Completed {stage}", flush=True)
            return True

        success = execute("nba_feedback")
        if success:
            # These data/cache services have independent inputs. Start them in the
            # same morning cycle, then generate NBA only after all dependencies.
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(execute, STAGES[1:-1]))
            success = all(results)
        if success:
            success = execute("nba")
        if not success:
            state["status"] = "failed"
            save(path, state)
            return 1
        state.update(status="complete", completed_at=datetime.now().astimezone().isoformat())
        save(path, state)
        save(state_dir / "latest.json", state)
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/root/projects"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/gba-ai-fleet"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    sys.exit(run(args.root, args.state_dir, force=args.force))
