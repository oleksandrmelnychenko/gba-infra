#!/usr/bin/env python3
"""Durable daily fleet refresh. Same entry point for the timer and manual runs.

Graph: nba_feedback -> pool(reco, procure, solvency, pricing, products, forecast) -> nba.
Each stage runs in the working directory, interpreter and environment of the
service's own web unit (read from systemd, not hard-coded), so the cache is
written by the same code that reads it. Success is recorded only after the
adapter confirms persisted results. A retry resumes incomplete stages.

Health gate. Before the stages and after them every web service is checked:
unit active and GET /health with the body inspected. A pre-flight failure tries
`systemctl start` once for an enabled-but-inactive unit (never while the
maintenance marker exists). Rule: stages are in-process and write to the stores
the web services read, so a down web service does not skip its own stage; only
nba_feedback and nba, which call reco over HTTP, are failed up front when reco
is down. The cycle is "complete" only when every stage is complete and the
post-flight check finds all seven services healthy; otherwise it is "failed"
with the services named in `failure` and `failed_services`, and the unit's
restart policy retries (finished stages are not repeated).

Publication. Every stage transition is written at once to <day>.json (what the
server reads), <day>-<run_id>.json (history of that run; a forced run does not
overwrite the earlier run's record) and latest.json. Stage logs are appended
with a header per run and attempt. State and log files older than 60 days are
removed after a successful cycle.

Cycle date. The cycle of a moment before 05:00 Kyiv is the previous day. A
caller that refreshes at night after a synchronization passes --cycle-date with
the Kyiv calendar date, so the 05:00 timer finds that day's cycle complete and
does not repeat it. --fresh-since keeps --force from repeating a cycle that
started after the given instant. The lock is flock; --wait-lock waits for a
running cycle instead of giving up, so a forced run spanning 05:00 does not
swallow the day's cycle. Exit codes: 0 done or nothing to do, 1 failed,
2 bad arguments, 75 the lock is held by another run.

Synchronization. --wait-sync waits, before the fleet lock is taken, while the
1C synchronization runner holds its own lock (the nightly run starts at 02:00
and normally ends before 05:00) or while the server's synchronization status
says IsInProgress, which also covers a synchronization started from the console.
The status is read with the service credential (--sync-credential) from
--sync-server. The wait is bounded by the same limit for both: when a
synchronization is still running after it the cycle starts on the data it has,
and `sync_wait` in the cycle file says so and names what it waited for. When the
status cannot be read (server down, credential missing or refused) only the
lock rule applies and `sync_wait.server_fallback` records why. The credential
and the token are never printed. The runner's own forced refresh does not pass
--wait-sync, so it never waits for itself.

Single stages. --only <stage>[,<stage>] refreshes just the named stages again,
under the same lock, health gate, unit runtime and persistence confirmation as
the full cycle, with a refresh token of its own so nothing cached earlier is
taken as done. The other stages are not run and are reported as "not requested";
their entries in the day's state are left as they are. The day keeps its status
unless a requested stage or the post-flight check fails, which marks it failed;
a day with no cycle yet gets the status "partial" and the next full run
finishes it. Each such run is listed under `only_runs`.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import fcntl
import json
from pathlib import Path
import re
import subprocess
import sys
from threading import RLock
import time
import uuid
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_fleet_health as health
import gba_sync_run as sync_runner

STAGES = ("nba_feedback", "reco", "procure", "solvency", "pricing", "products", "forecast", "nba")
NEEDS_RECO_WEB = ("nba_feedback", "nba")
EXIT_FAILED = 1
EXIT_ARGUMENTS = 2
EXIT_LOCK_BUSY = 75
RETENTION_DAYS = 60
KYIV = ZoneInfo("Europe/Kyiv")
DATED_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})[-.]")


def stage_service(stage: str) -> str:
    return "nba" if stage == "nba_feedback" else stage


def cycle_date(now: datetime | None = None) -> str:
    now = (now or datetime.now(KYIV)).astimezone(KYIV)
    return (now.date() if now.hour >= 5 else now.date() - timedelta(days=1)).isoformat()


def calendar_date(now: datetime | None = None) -> str:
    return (now or datetime.now(KYIV)).astimezone(KYIV).date().isoformat()


def allowed_cycle_dates(now: datetime | None = None) -> tuple[str, ...]:
    return tuple(dict.fromkeys((cycle_date(now), calendar_date(now))))


def parse_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    instant = datetime.fromisoformat(value)
    return instant if instant.tzinfo else instant.astimezone()


def save(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def publish(state_dir: Path, state: dict) -> None:
    day, run_id = state["cycle_date"], state["run_id"]
    for name in (f"{day}-{run_id}.json", f"{day}.json", "latest.json"):
        save(state_dir / name, state)


def acquire(lock, wait_seconds: float) -> bool:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    announced = False
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            if not announced:
                print(f"A fleet refresh is running; waiting up to {int(wait_seconds)} s for it", flush=True)
                announced = True
            time.sleep(min(5.0, max(0.1, deadline - time.monotonic())))


def sync_running(lock_path: Path) -> bool:
    try:
        with lock_path.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
    except OSError:
        return False


class ServerSyncProbe:
    """Ask the server whether a synchronization is in progress; None with a reason when it cannot say."""

    def __init__(self, server: str, credential: Path, timeout: float = 20) -> None:
        self.server, self.credential, self.timeout = server.rstrip("/"), credential, timeout
        self.token: str | None = None
        self.reason: str | None = None

    def fetch_token(self) -> str:
        login = sync_runner.read_credential(self.credential)
        code, raw = sync_runner.http_request(
            self.server + "/api/v1/uk/usermanagement/token", data=login,
            headers={"Content-Type": "application/json"}, timeout=self.timeout)
        body = sync_runner.parse_json(raw).get("Body")
        if code != 200 or not isinstance(body, dict) or not body.get("AccessToken"):
            raise RuntimeError(f"the token call answered HTTP {code}")
        return str(body["AccessToken"])

    def read_status(self) -> tuple[int, dict]:
        if self.token is None:
            self.token = self.fetch_token()
        code, raw = sync_runner.http_request(
            self.server + "/api/v1/uk/data/sync/status",
            headers={"Authorization": "Bearer " + self.token, "X-Time-Zone": "Europe/Kyiv"}, timeout=self.timeout)
        return code, sync_runner.parse_json(raw)

    def in_progress(self) -> bool | None:
        try:
            code, answer = self.read_status()
            if code in (401, 403):
                self.token = None
                code, answer = self.read_status()
            body = answer.get("Body")
            if code != 200 or not isinstance(body, dict) or not isinstance(body.get("IsInProgress"), bool):
                raise RuntimeError(f"the status call answered HTTP {code} without IsInProgress")
        except Exception as exc:
            self.token = None
            self.reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            return None
        self.reason = None
        return body["IsInProgress"]


def wait_for_sync(lock_path: Path, limit: float, poll: float = 30, *,
                  probe: ServerSyncProbe | None = None) -> dict | None:
    if limit <= 0:
        return None
    waited_for: list[str] = []
    fallback: str | None = None

    def running() -> bool:
        nonlocal fallback
        lock = sync_running(lock_path)
        server = probe.in_progress() if probe is not None else False
        if probe is not None and server is None and fallback != probe.reason:
            fallback = probe.reason
            print(f"The server synchronization status is unavailable ({fallback}); "
                  f"only the runner lock {lock_path} decides", flush=True)
        for name, busy in (("runner lock", lock), ("server status", server)):
            if busy and name not in waited_for:
                waited_for.append(name)
        return lock or bool(server)

    def report(result: dict | None) -> dict | None:
        if probe is None:
            return result
        result = result or ({"waited_seconds": 0, "still_running": False} if fallback else None)
        if result is not None:
            result["waited_for"] = list(waited_for)
            if fallback:
                result["server_fallback"] = fallback
        return result

    if not running():
        return report(None)
    started = time.time()
    print(f"A synchronization is running ({', '.join(waited_for)}); waiting up to {limit:g} s for it", flush=True)
    while True:
        waited = round(time.time() - started)
        if not running():
            print(f"The synchronization run ended after {waited} s of waiting", flush=True)
            return report({"waited_seconds": waited, "still_running": False})
        if waited >= limit:
            print(f"The synchronization is still running after {waited} s ({', '.join(waited_for)}); "
                  f"the cycle starts on the data it has", flush=True)
            return report({"waited_seconds": waited, "still_running": True})
        time.sleep(poll)


def stage_runtime(root: Path, service: str) -> dict:
    runtime = health.unit_runtime(service)
    if runtime is None:
        home = root / f"gba-{service}"
        return {"cwd": str(home), "python": str(home / ".venv/bin/python"), "environment": {},
                "environment_files": [], "environment_names": [], "source": "fallback"}
    return {**runtime, "source": "unit"}


def prune(state_dir: Path, day: str, keep_days: int = RETENTION_DAYS) -> int:
    cutoff = date.fromisoformat(day) - timedelta(days=keep_days)
    removed = 0
    for item in state_dir.iterdir():
        match = DATED_FILE.match(item.name)
        if not match or not item.is_file():
            continue
        try:
            stamp = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if stamp < cutoff:
            item.unlink(missing_ok=True)
            removed += 1
    return removed


def describe(report: dict) -> str:
    parts = []
    for name, result in report["services"].items():
        text = "healthy" if result["healthy"] else "up, " + str(result.get("reason")) if result["up"] \
            else "DOWN, " + str(result.get("reason"))
        if result.get("start_attempted"):
            text += " (start attempted)"
        if result.get("start_skipped"):
            text += f" (not started: {result['start_skipped']})"
        parts.append(f"{name}: {text}")
    return "; ".join(parts)


TOP_LEVEL_OUTCOME = ("status", "completed_at", "failure", "failed_services", "failed_stages", "failed_at")


def parse_only(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    names = [name.strip() for name in value.split(",")]
    unknown = [name for name in names if name not in STAGES]
    if unknown or not names:
        raise ValueError("unknown stage: " + ", ".join(repr(name) for name in unknown or names))
    return tuple(stage for stage in STAGES if stage in names)


def run(root: Path, state_dir: Path, *, force: bool = False, day: str | None = None,
        fresh_since: datetime | None = None, wait_lock: float = 0, wait_sync: float = 0,
        sync_lock: Path = Path("/var/lib/gba-sync/run.lock"), only: tuple[str, ...] = (),
        sync_server: str = "", sync_credential: Path | None = None) -> int:
    state_dir.mkdir(parents=True, exist_ok=True)
    if sync_server and sync_credential is not None and wait_sync > 0:
        sync_wait = wait_for_sync(sync_lock, wait_sync, probe=ServerSyncProbe(sync_server, sync_credential))
    else:
        sync_wait = wait_for_sync(sync_lock, wait_sync)
    with (state_dir / "refresh.lock").open("a") as lock:
        if not acquire(lock, wait_lock):
            print("A fleet refresh is already running; the lock could not be taken", flush=True)
            return EXIT_LOCK_BUSY
        day = day or cycle_date()
        path = state_dir / f"{day}.json"
        previous_state = json.loads(path.read_text()) if path.exists() else None
        request_path = state_dir / "requests/refresh.json"
        if only:
            force = False
        elif request_path.exists():
            request = json.loads(request_path.read_text())
            force = force or (request.get("force") is True and previous_state is not None
                              and request.get("expected_run_id") == previous_state.get("run_id"))
            request_path.unlink()
        if force and fresh_since and previous_state:
            started = parse_instant(previous_state.get("started_at"))
            if started and started >= fresh_since:
                print(f"Cycle {day} run {previous_state.get('run_id')} started at {started.isoformat()}, "
                      f"after {fresh_since.isoformat()}; not forcing another one", flush=True)
                force = False
        if previous_state and previous_state.get("status") == "complete" and not force and not only:
            print("This cycle is already complete; timestamps unchanged", flush=True)
            return 0
        if previous_state and force and previous_state.get("run_id"):
            archive = state_dir / f"{day}-{previous_state['run_id']}.json"
            if not archive.exists():
                save(archive, previous_state)
        state = previous_state if previous_state and not force else {
            "cycle_date": day, "run_id": uuid.uuid4().hex, "timezone": "Europe/Kyiv",
            "started_at": datetime.now().astimezone().isoformat(), "stages": {},
        }
        state.setdefault("cycle_date", day)
        outcome_before = {name: state[name] for name in TOP_LEVEL_OUTCOME if name in state} \
            if previous_state else {"status": "partial"}
        token = uuid.uuid4().hex if only else state["run_id"]
        only_run = None
        if only:
            only_run = {"run_id": token, "stages": list(only),
                        "not_requested": [stage for stage in STAGES if stage not in only],
                        "status": "running", "started_at": datetime.now().astimezone().isoformat()}
            state.setdefault("only_runs", []).append(only_run)
        state["status"] = "running"
        if sync_wait:
            state["sync_wait"] = sync_wait
        if not only:
            for stale in ("completed_at", "failure", "failed_services", "failed_stages"):
                state.pop(stale, None)
        state_lock = RLock()
        publish(state_dir, state)
        print(f"Cycle {day} run {state['run_id']}", flush=True)
        if only:
            print(f"Only {', '.join(only)} (refresh {token}); not requested: "
                  f"{', '.join(only_run['not_requested']) or 'none'}", flush=True)

        preflight = health.check_fleet(start_inactive=True)
        with state_lock:
            state["health"] = {"pre": preflight}
            publish(state_dir, state)
        print("Pre-flight: " + describe(preflight), flush=True)

        def execute(stage: str) -> bool:
            previous = state["stages"].get(stage, {})
            if only and stage not in only:
                return True
            if previous.get("status") == "complete" and not only:
                return True
            service = stage_service(stage)
            runtime = stage_runtime(root, service)
            result_file = state_dir / f"{day}-{stage}-result.json"
            result_file.unlink(missing_ok=True)
            log_file = state_dir / f"{day}-{stage}.log"
            entry = {"status": "running", "attempt": previous.get("attempt", 0) + 1,
                     "started_at": datetime.now().astimezone().isoformat(),
                     "cwd": runtime["cwd"], "python": runtime["python"], "runtime_source": runtime["source"]}
            if only:
                entry["only_run_id"] = token
            with state_lock:
                state["stages"][stage] = entry
                publish(state_dir, state)
            print(f"Starting {stage}, attempt {entry['attempt']}", flush=True)
            with log_file.open("a") as output:
                output.write(f"===== cycle {day} run {state['run_id']} stage {stage} attempt {entry['attempt']} "
                             f"started {entry['started_at']} cwd {runtime['cwd']} =====\n")
                output.flush()
                try:
                    if stage in NEEDS_RECO_WEB and not health.check("reco")["up"]:
                        raise RuntimeError("gba-reco web service is down and this stage calls it over HTTP")
                    result = subprocess.run(
                        [runtime["python"], str(Path(__file__).with_name("refresh_ai_service.py")),
                         stage, "--as-of", day, "--token", token, "--result", str(result_file)],
                        cwd=runtime["cwd"], env=health.runtime_environment(runtime),
                        stdout=output, stderr=subprocess.STDOUT, timeout=6 * 3600,
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
                        entry.update(status="failed", error=str(exc),
                                     failed_at=datetime.now().astimezone().isoformat())
                        publish(state_dir, state)
                    print(f"Failed {stage}: {exc}", flush=True)
                    return False
            with state_lock:
                entry.update(status="complete", completed_at=datetime.now().astimezone().isoformat())
                publish(state_dir, state)
            print(f"Completed {stage}", flush=True)
            return True

        success = execute("nba_feedback")
        if success:
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(execute, STAGES[1:-1]))
            success = all(results)
        if success:
            success = execute("nba")

        postflight = health.check_fleet(start_inactive=False)
        state["health"]["post"] = postflight
        print("Post-flight: " + describe(postflight), flush=True)
        failed_stages = [stage for stage in (only or STAGES)
                         if state["stages"].get(stage, {}).get("status") != "complete"]
        failed_services = sorted(set(postflight["unhealthy"]))
        if only:
            only_run["finished_at"] = datetime.now().astimezone().isoformat()
            for stage in STAGES:
                print(f"Stage {stage}: " + (state["stages"].get(stage, {}).get("status", "not run")
                                            if stage in only else "not requested"), flush=True)
        if not success or failed_services:
            reasons = []
            if failed_stages:
                reasons.append("stages not complete: " + ", ".join(failed_stages))
            if failed_services:
                reasons.append("services not healthy: " + ", ".join(
                    f"gba-{name} ({postflight['services'][name].get('reason')})" for name in failed_services))
            state.update(status="failed", failure="; ".join(reasons), failed_services=failed_services,
                         failed_stages=failed_stages, failed_at=datetime.now().astimezone().isoformat())
            if only:
                only_run.update(status="failed", failure=state["failure"])
            publish(state_dir, state)
            print("Cycle failed: " + state["failure"], flush=True)
            return EXIT_FAILED
        if only:
            for name in TOP_LEVEL_OUTCOME:
                state.pop(name, None)
            state.update(outcome_before)
            if state["status"] == "running":
                state["status"] = "partial"
            only_run["status"] = "complete"
            publish(state_dir, state)
            print(f"Refreshed {', '.join(only)}; cycle {day} status stays {state['status']!r}", flush=True)
            return 0
        state.pop("failed_at", None)
        state.update(status="complete", completed_at=datetime.now().astimezone().isoformat())
        publish(state_dir, state)
        removed = prune(state_dir, day)
        print(f"Cycle {day} complete; pruned {removed} files older than {RETENTION_DAYS} days", flush=True)
        return 0


def check(root: Path, state_dir: Path, *, day: str | None = None, imports: bool = True,
          only: tuple[str, ...] = ()) -> int:
    """Read-only report: health, the runtime each stage would use, state and lock. Starts nothing."""
    day = day or cycle_date()
    report: dict = {"mode": "check", "cycle_date": day, "allowed_cycle_dates": list(allowed_cycle_dates()),
                    "state_dir": str(state_dir)}
    lock_path = state_dir / "refresh.lock"
    if lock_path.exists():
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
                report["lock"] = "free"
            except BlockingIOError:
                report["lock"] = "held by a running refresh"
    else:
        report["lock"] = "free (no lock file yet)"
    path = state_dir / f"{day}.json"
    if path.exists():
        current = json.loads(path.read_text())
        report["day_state"] = {"status": current.get("status"), "run_id": current.get("run_id"),
                               "started_at": current.get("started_at"),
                               "completed_at": current.get("completed_at"),
                               "stages": {name: value.get("status")
                                          for name, value in (current.get("stages") or {}).items()}}
        report["a_run_now_would"] = "do nothing (cycle complete; --force starts a new run)" \
            if current.get("status") == "complete" else "resume the unfinished stages"
    else:
        report["day_state"] = None
        report["a_run_now_would"] = "start a new cycle with all stages"
    fleet = health.check_fleet(start_inactive=False)
    report["health"] = fleet
    stages = {}
    ok = not fleet["unhealthy"]
    for stage in STAGES:
        if only and stage not in only:
            stages[stage] = "not requested"
            continue
        runtime = stage_runtime(root, stage_service(stage))
        entry = {"cwd": runtime["cwd"], "python": runtime["python"], "source": runtime["source"],
                 "environment_files": runtime["environment_files"],
                 "environment_names": runtime["environment_names"],
                 "python_exists": Path(runtime["python"]).exists(), "cwd_exists": Path(runtime["cwd"]).is_dir()}
        if imports and entry["python_exists"] and entry["cwd_exists"]:
            try:
                done = subprocess.run(
                    [runtime["python"], str(Path(__file__).with_name("refresh_ai_service.py")), stage, "--check"],
                    cwd=runtime["cwd"], env=health.runtime_environment(runtime),
                    capture_output=True, text=True, timeout=180)
                entry["adapter_check"] = "ok" if done.returncode == 0 else \
                    f"exit {done.returncode}: {(done.stderr or done.stdout).strip()[-400:]}"
            except subprocess.TimeoutExpired:
                entry["adapter_check"] = "timed out after 180 s"
        ok = ok and entry["python_exists"] and entry["cwd_exists"] and entry.get("adapter_check", "ok") == "ok"
        stages[stage] = entry
    report["stages"] = stages
    report["ok"] = ok
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    return 0 if ok else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path("/root/projects"),
                        help="fallback location of gba-<service> when a unit cannot be read")
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/gba-ai-fleet"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--cycle-date", default="",
                        help="the cycle to run: the current cycle date or today's Kyiv calendar date")
    parser.add_argument("--fresh-since", default="",
                        help="with --force: do not repeat a cycle that started at or after this ISO instant")
    parser.add_argument("--wait-lock", type=float, default=0, metavar="SECONDS")
    parser.add_argument("--wait-sync", type=float, default=0, metavar="SECONDS",
                        help="wait this long for a running 1C synchronization before the cycle")
    parser.add_argument("--sync-lock", type=Path, default=Path("/var/lib/gba-sync/run.lock"))
    parser.add_argument("--sync-server", default="http://127.0.0.1:35981",
                        help="with --wait-sync: also wait while this server reports a synchronization; '' disables")
    parser.add_argument("--sync-credential", type=Path, default=Path("/etc/gba-sync/credential.json"),
                        help="service credential (root, 0600) used to read the synchronization status")
    parser.add_argument("--check", action="store_true", help="read-only report; starts nothing")
    parser.add_argument("--only", default="", metavar="STAGE[,STAGE]",
                        help="refresh only these stages again (" + ", ".join(STAGES) + "); the others are left alone")
    args = parser.parse_args(argv)
    try:
        only = parse_only(args.only)
    except ValueError as exc:
        print(f"--only: {exc}; stages are {', '.join(STAGES)}", flush=True)
        return EXIT_ARGUMENTS
    day = args.cycle_date or None
    if day and day not in allowed_cycle_dates():
        print(f"--cycle-date {day} is neither the current cycle nor today in Kyiv "
              f"({', '.join(allowed_cycle_dates())})", flush=True)
        return EXIT_ARGUMENTS
    try:
        fresh_since = parse_instant(args.fresh_since)
    except ValueError:
        print(f"--fresh-since {args.fresh_since!r} is not an ISO instant", flush=True)
        return EXIT_ARGUMENTS
    if args.check:
        return check(args.root, args.state_dir, day=day, only=only)
    return run(args.root, args.state_dir, force=args.force, day=day,
               fresh_since=fresh_since, wait_lock=args.wait_lock, wait_sync=args.wait_sync,
               sync_lock=args.sync_lock, only=only,
               sync_server=args.sync_server, sync_credential=args.sync_credential)


if __name__ == "__main__":
    sys.exit(main())
