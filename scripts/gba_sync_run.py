#!/usr/bin/env python3
"""Run a 1C to GBA synchronization on one stand in one of three modes.

light    AMG and Fenix daily sessions only. Nothing downstream is refreshed.
full     The same sessions, then a full rebuild of the shop search index and a
         forced AI fleet refresh, each confirmed before the run is reported done.
refresh  Only the search rebuild and the AI fleet refresh, without sessions.

The same entry point serves the timer and manual runs. flock prevents overlap.

Decisions that are deliberate, not accidental:

Bases. AMG runs first and Fenix last, because only Fenix publishes the batch
snapshot. Fenix runs even when AMG failed: the bases are independent, a failed
AMG does not make Fenix wrong, and skipping Fenix would leave the stand without
either base and without a snapshot. Every base is reported on its own; the run
is successful only when both finished.

Session result. The terminal state is taken only from the run this runner
started: the pipeline run id returned by the start call must equal
LastTerminalRun (or LastSuccessfulRun). When the ids cannot be matched (the
server returned none, or reports another one), a terminal run is accepted only
if it differs from the one seen before the start, started after the start call
and the server stayed idle for two polls. A stale terminal run is never trusted.
The next base starts only after the server is idle again.

Downstream (full, refresh). It runs when at least one base finished, because
the data changed. The search rebuild and the AI refresh are independent: a
failed search rebuild is reported and the AI refresh still runs.

AI refresh. The fleet cycle is asked for the Kyiv calendar date, so a night
run produces the cycle the 05:00 timer recognises as today's. The runner waits
for a cycle that is already running (it does not report that as success) and
forces a new one only when the existing cycle started before the sessions
ended. The result is read from that day's fleet file, not from the exit code
alone.

Pre-flight. Before any session: the credential yields a token, the status
call answers and the server is idle (its /health text is logged, not gating). For full and refresh also the seven AI services (unit
active, /health body healthy; an enabled-but-inactive unit is started once
unless the maintenance marker exists) and the shop API /health body. A failed
pre-flight stops the run before an hour is spent.

Waits are bounded: status calls, the start of a run, a whole session and the
fleet lock each have a limit and a message that says which one expired.

Credential. --credential-file names the persistent service credential: it is
used when the file exists, is never deleted, and is refused unless it is a
regular file owned by root with mode 0600. Without it the one-time
--login-file is used; --consume-login deletes that file only after pre-flight
passed and the run is about to start. A busy server is polled for --busy-wait
seconds first. Neither the credential nor the token is ever logged.
--dry-run performs every read-only check, prints the plan and starts
nothing; it never deletes the login file and writes no state.

Failure. Every failed run leaves "ok": false and a one-line "failure" in
latest.json and exits non-zero, so the unit is failed as well. Only a run that
found another run holding the lock leaves latest.json to that run.

Exit codes: 0 done, 1 failed, 2 the credential is unusable, 3 busy,
4 pre-flight failed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_fleet_health as health

EXIT_FAILED, EXIT_LOGIN, EXIT_BUSY, EXIT_PREFLIGHT = 1, 2, 3, 4
CREDENTIAL_OWNER, CREDENTIAL_MODE = 0, 0o600
FLEET_LOCK_BUSY = 75
KYIV = ZoneInfo("Europe/Kyiv")


class RunnerError(RuntimeError):
    pass


class LoginError(RunnerError):
    pass


def http_request(url: str, *, data: bytes | None = None, headers: dict | None = None,
                 method: str | None = None, timeout: float = 60) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def parse_json(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def run_key(value: object) -> str:
    return str(value or "").replace("-", "").lower()


def started_after(run: dict, instant: datetime) -> bool:
    try:
        started = datetime.fromisoformat(str(run.get("StartedAtUtc") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (instant - started).total_seconds() <= 30


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_credential(path: Path) -> bytes:
    """Return the login body of a persistent credential file that only root can read."""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise LoginError(f"credential file {path} cannot be read: {exc.strerror or exc}") from None
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != CREDENTIAL_OWNER or mode != CREDENTIAL_MODE:
        raise LoginError(
            f"credential file {path} is refused: it must be a regular file owned by uid {CREDENTIAL_OWNER} "
            f"with mode {CREDENTIAL_MODE:04o} (found uid {info.st_uid}, mode {mode:04o}"
            f"{'' if stat.S_ISREG(info.st_mode) else ', not a regular file'})")
    try:
        value = parse_json(path.read_bytes())
    except OSError as exc:
        raise LoginError(f"credential file {path} cannot be read: {exc.strerror or exc}") from None
    username, password = value.get("Username"), value.get("Password")
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        raise LoginError(f"credential file {path} has no Username and Password")
    return json.dumps({"Username": username, "Password": password}).encode()


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.dry = bool(args.dry_run)
        self.state_dir: Path = args.state_dir
        self.log_path = self.state_dir / "run.log"
        self.run_id = uuid.uuid4().hex
        self.token_value: tuple[str, float] | None = None
        self.login = b""
        self.persistent = False
        self.source = "login file"
        self.started_at = datetime.now().astimezone()

    def log(self, **event: object) -> None:
        line = {"at": iso_now(), "run": self.run_id, "mode": self.args.mode, **event}
        if self.dry:
            line["dry_run"] = True
        text = json.dumps(line, ensure_ascii=False)[:4000]
        if not self.dry:
            with self.log_path.open("a") as output:
                output.write(text + "\n")
        print(text, flush=True)

    def read_login(self) -> None:
        """Read the credential and prove it works before the file may be deleted."""
        credential: Path | None = self.args.credential_file
        if credential is not None and os.path.lexists(credential):
            self.login = read_credential(credential)
            self.persistent = True
            self.source = "credential file"
        else:
            path: Path | None = self.args.login_file
            if path is None:
                raise LoginError(f"credential file {credential} does not exist and no --login-file was given"
                                 if credential is not None else "neither --credential-file nor --login-file was given")
            try:
                self.login = path.read_bytes()
            except OSError as exc:
                raise LoginError(f"login file {path} cannot be read: {exc.strerror or exc}") from None
            if not parse_json(self.login):
                raise LoginError(f"login file {path} is not a JSON object; it was left in place")
        deadline = time.time() + self.args.login_timeout
        while True:
            try:
                self.fetch_token()
                break
            except LoginError:
                raise
            except Exception as exc:
                if time.time() >= deadline:
                    raise LoginError(
                        f"no token within {self.args.login_timeout} s ({str(exc)[:200]}); "
                        f"the {self.source} was left in place") from None
                time.sleep(self.args.poll)

    def consume_login(self) -> None:
        if self.args.consume_login and not self.dry and not self.persistent and self.args.login_file is not None:
            self.args.login_file.unlink(missing_ok=True)

    def fetch_token(self) -> str:
        code, raw = http_request(
            self.args.server + "/api/v1/uk/usermanagement/token", data=self.login,
            headers={"Content-Type": "application/json"}, timeout=60)
        body = parse_json(raw).get("Body")
        if 400 <= code < 500:
            raise LoginError(f"the server refused the credential (HTTP {code}); the {self.source} was left in place")
        if code != 200 or not isinstance(body, dict) or not body.get("AccessToken"):
            raise RunnerError(f"token endpoint answered HTTP {code} without a token")
        self.token_value = (body["AccessToken"], time.time() + float(body.get("AccessTokenExpiresIn", 600)))
        return self.token_value[0]

    def token(self) -> str:
        if self.token_value and self.token_value[1] > time.time() + 120:
            return self.token_value[0]
        return self.fetch_token()

    def call(self, base: str, path: str, payload: dict | None = None, *,
             operation: str | None = None, method: str | None = None,
             timeout: float = 120, authorized: bool = True) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json", "X-Time-Zone": "Europe/Kyiv"}
        if authorized:
            headers["Authorization"] = "Bearer " + self.token()
        if self.args.cutover_id:
            headers["X-GBA-Cutover-Id"] = self.args.cutover_id
        if operation:
            headers["X-GBA-Sync-Operation-Id"] = operation
        code, raw = http_request(
            base + path, data=None if payload is None else json.dumps(payload).encode(),
            headers=headers, method=method, timeout=timeout)
        return code, parse_json(raw)

    def status(self) -> dict:
        deadline = time.time() + self.args.status_timeout
        last_error = ""
        while True:
            try:
                code, answer = self.call(self.args.server, "/api/v1/uk/data/sync/status")
                if code == 200 and isinstance(answer.get("Body"), dict):
                    return answer["Body"]
                last_error = f"HTTP {code}: {str(answer.get('Message') or '')[:200]}"
            except Exception as exc:
                last_error = str(exc)[:200]
            if time.time() >= deadline:
                raise RunnerError(f"synchronization status was unavailable for "
                                  f"{self.args.status_timeout} s: {last_error}")
            time.sleep(self.args.poll)

    def wait_run(self, expected: str, before: str) -> tuple[dict, str]:
        """Wait for the terminal state of the run this runner started and for the server to be idle."""
        started = time.time()
        called_at = datetime.now(timezone.utc)
        deadline = started + self.args.session_timeout
        grace = started + self.args.start_grace
        idle_polls = 0
        found: tuple[dict, str] | None = None
        while True:
            status = self.status()
            last = status.get("LastTerminalRun") or {}
            last_key = run_key(last.get("PipelineRunId"))
            success = status.get("LastSuccessfulRun") or {}
            if expected and last_key == expected:
                found = (last, "pipeline run id")
            elif expected and run_key(success.get("PipelineRunId")) == expected:
                found = (success, "pipeline run id (last successful run)")
            if found and not status.get("IsInProgress"):
                return found
            if status.get("IsInProgress"):
                grace = time.time() + self.args.start_grace
                idle_polls = 0
            else:
                idle_polls += 1
                if not found and last_key and last_key != before and idle_polls >= 2 \
                        and started_after(last, called_at):
                    return last, "terminal run is new and started after the start call" + (
                        "; its id differs from the accepted one" if expected else "; the server returned no run id")
                if time.time() >= grace:
                    raise RunnerError(
                        f"the server is idle but reported no terminal state for the started run within "
                        f"{self.args.start_grace} s (expected {expected or 'a new run'}, "
                        f"last terminal {last_key or 'none'} {last.get('Status')})")
            if time.time() >= deadline:
                raise RunnerError(f"the session did not end within {self.args.session_timeout} s")
            time.sleep(self.args.poll)

    def session(self, for_amg: bool) -> dict:
        name = "amg" if for_amg else "fenix"
        query = [("forAmg", str(for_amg).lower()), ("mode", "1")]
        if self.args.date_from:
            query += [("from", self.args.date_from), ("to", self.args.date_to or self.args.date_from)]
        clock = time.time()
        report: dict = {"ok": False}
        try:
            before = run_key((self.status().get("LastTerminalRun") or {}).get("PipelineRunId"))
            code, accepted = self.call(
                self.args.server, "/api/v1/uk/data/sync/start/session?" + urllib.parse.urlencode(query),
                {}, operation=uuid.uuid4().hex)
            if code != 200:
                report.update(reason=f"the start call answered HTTP {code}",
                              body=json.dumps(accepted, ensure_ascii=False)[:1500])
                self.log(step="session", base=name, **report)
                return report
            body = accepted.get("Body") if isinstance(accepted.get("Body"), dict) else {}
            expected = run_key(body.get("PipelineRunId"))
            self.log(step="session", base=name, accepted=accepted.get("StatusCode"),
                     pipelineRun=expected or None)
            last, matched = self.wait_run(expected, before)
            report.update(ok=last.get("Status") == "Finished", status=last.get("Status"),
                          failedStep=last.get("FailedStep"), pipelineRun=run_key(last.get("PipelineRunId")),
                          matchedBy=matched)
        except RunnerError as exc:
            report.update(reason=str(exc))
        except Exception as exc:
            report.update(reason=f"{type(exc).__name__}: {str(exc)[:300]}")
        report["seconds"] = round(time.time() - clock)
        self.log(step="session", base=name, **report)
        return report

    def search_health(self) -> dict:
        try:
            _, answer = self.call(self.args.shop_api, "/api/v1/uk/elasticsearch/health")
            body = answer.get("Body")
            return body if isinstance(body, dict) else {"error": "no Body in the health answer"}
        except Exception as exc:
            return {"error": str(exc)[:200]}

    def rebuild_search(self) -> bool:
        started = datetime.now(timezone.utc)
        clock = time.time()
        try:
            code, answer = self.call(self.args.shop_api, "/api/v1/uk/elasticsearch/sync/full", {},
                                     method="POST", timeout=self.args.search_timeout)
        except Exception as exc:
            self.log(step="search", ok=False, error=str(exc)[:300], seconds=round(time.time() - clock))
            return False
        if code != 200:
            self.log(step="search", ok=False, http=code, body=json.dumps(answer, ensure_ascii=False)[:1500],
                     seconds=round(time.time() - clock))
            return False
        result = answer.get("Body") if isinstance(answer.get("Body"), dict) else {}
        health_body: dict = {}
        current = False
        deadline = time.time() + self.args.search_settle
        while True:
            health_body = self.search_health()
            raw = str(health_body.get("lastSyncUtc") or "")
            try:
                synced = datetime.fromisoformat(raw[:19]).replace(tzinfo=timezone.utc) if raw else None
            except ValueError:
                synced = None
            current = bool(health_body.get("healthy")) and not health_body.get("stale") \
                and synced is not None and synced >= started.replace(microsecond=0)
            if current or time.time() >= deadline:
                break
            time.sleep(self.args.poll)
        ok = bool(result.get("success", result.get("Success"))) and current
        self.log(step="search", ok=ok, seconds=round(time.time() - clock), result=result, health=health_body,
                 **({} if current else {"reason": f"the index was not current within {self.args.search_settle} s"}))
        return ok

    def fleet_command(self, since: datetime) -> tuple[list[str], str]:
        day = datetime.now(KYIV).date().isoformat()
        return [sys.executable, str(Path(__file__).with_name("refresh_ai_fleet.py")), "--force",
                "--cycle-date", day, "--fresh-since", since.isoformat(timespec="seconds"),
                "--wait-lock", str(self.args.fleet_lock_wait),
                "--state-dir", str(self.args.ai_state_dir)], day

    def refresh_ai(self, since: datetime) -> bool:
        clock = time.time()
        command, day = self.fleet_command(since)
        try:
            exit_code = subprocess.run(command, timeout=self.args.fleet_timeout).returncode
        except subprocess.TimeoutExpired:
            self.log(step="ai", ok=False, reason=f"the fleet refresh did not end within {self.args.fleet_timeout} s")
            return False
        path = self.args.ai_state_dir / f"{day}.json"
        state = parse_json(path.read_bytes()) if path.exists() else {}
        stages = {name: value.get("status") for name, value in (state.get("stages") or {}).items()}
        try:
            started = datetime.fromisoformat(state.get("started_at") or "")
        except ValueError:
            started = None
        fleet = health.check_fleet(start_inactive=False)
        services = {name: ("healthy" if value["healthy"] else value.get("reason"))
                    for name, value in fleet["services"].items()}
        reason = None
        if exit_code == FLEET_LOCK_BUSY:
            reason = (f"another fleet refresh held the lock for more than {self.args.fleet_lock_wait} s; "
                      f"this run did not refresh anything")
        elif exit_code:
            reason = f"the fleet refresh exited {exit_code}: {state.get('failure') or 'see its journal'}"
        elif state.get("status") != "complete":
            reason = f"cycle {day} is {state.get('status') or 'missing'}, not complete"
        elif started is None or started < since.replace(microsecond=0):
            reason = f"cycle {day} started at {state.get('started_at')}, before the data changed at {since.isoformat()}"
        elif fleet["unhealthy"]:
            reason = "services not healthy after the refresh: " + ", ".join(fleet["unhealthy"])
        self.log(step="ai", ok=reason is None, seconds=round(time.time() - clock), exit=exit_code, cycle=day,
                 fleetRun=state.get("run_id"), startedAt=state.get("started_at"), stages=stages,
                 services=services, **({"reason": reason} if reason else {}))
        return reason is None

    def preflight(self) -> list[str]:
        problems: list[str] = []
        downstream = self.args.mode in ("full", "refresh")
        try:
            code, raw = http_request(self.args.server + "/health", timeout=20)
            text = raw.decode(errors="replace").strip()[:80]
            self.log(step="preflight", check="server", http=code, body=text,
                     ok=code == 200 and text == "Healthy")
        except Exception as exc:
            self.log(step="preflight", check="server", ok=False, error=str(exc)[:160])
        if downstream:
            try:
                code, raw = http_request(self.args.shop_api + "/health", timeout=30)
                body = parse_json(raw)
                bad = [item.get("name") for item in body.get("checks") or [] if item.get("status") != "Healthy"]
                if code != 200 or body.get("status") != "Healthy" or bad:
                    problems.append(f"shop API /health answered HTTP {code} status {body.get('status')!r}"
                                    + (f", failing checks: {bad}" if bad else ""))
                self.log(step="preflight", check="shop", http=code, status=body.get("status"), failing=bad)
            except Exception as exc:
                problems.append(f"shop API /health did not answer: {str(exc)[:160]}")
            search = self.search_health()
            if search.get("error") or not search.get("indexExists"):
                problems.append(f"shop search health is unusable: {json.dumps(search, ensure_ascii=False)[:200]}")
            self.log(step="preflight", check="search", health=search)
            fleet = health.check_fleet(start_inactive=not self.dry)
            for name in fleet["unhealthy"]:
                problems.append(f"AI service gba-{name} is not healthy: {fleet['services'][name].get('reason')}")
            self.log(step="preflight", check="ai", started=fleet["started"],
                     maintenance=fleet["maintenance"],
                     services={name: ("healthy" if value["healthy"] else value.get("reason"))
                               for name, value in fleet["services"].items()})
        return problems

    def plan(self) -> list[str]:
        window = f"{self.args.date_from}..{self.args.date_to or self.args.date_from}" if self.args.date_from \
            else "server daily lookback"
        steps = []
        if self.args.mode != "refresh":
            steps += [f"session AMG (mode=1, window {window}); wait for its own pipeline run, "
                      f"at most {self.args.session_timeout} s",
                      "session Fenix (same window), also when AMG failed; it publishes the batch snapshot"]
        if self.args.mode in ("full", "refresh"):
            command, day = self.fleet_command(datetime.now().astimezone())
            steps += [f"shop search full rebuild: POST {self.args.shop_api}/api/v1/uk/elasticsearch/sync/full, "
                      f"then wait up to {self.args.search_settle} s for a current index",
                      "AI fleet refresh for cycle " + day + " (runs even when the search rebuild failed): "
                      + " ".join(command[1:]).replace(command[6], "<when the sessions ended>")]
            if self.args.mode == "full":
                steps[-2] = "only when at least one base finished: " + steps[-2]
        steps.append(f"write {self.state_dir / 'latest.json'} with a result per base and per downstream step")
        if self.persistent:
            steps.insert(0, f"log in with the persistent credential {self.args.credential_file} (never deleted)")
        elif self.args.consume_login:
            steps.insert(0, f"delete {self.args.login_file} (it already produced a token)")
        return steps

    def run(self) -> int:
        if not self.dry:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_dir / "run.lock"
        try:
            lock = lock_path.open("a") if (not self.dry or lock_path.exists()) else None
        except OSError:
            lock = None
        try:
            if lock is not None:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    self.log(step="start", ok=False, reason="another run holds the lock")
                    return EXIT_BUSY
            try:
                self.read_login()
            except LoginError as exc:
                return self.fail(EXIT_LOGIN, str(exc))
            self.log(step="login", ok=True, source=self.source)
            busy_deadline = time.time() + (0 if self.dry else self.args.busy_wait)
            while True:
                try:
                    status = self.status()
                except RunnerError as exc:
                    return self.fail(EXIT_PREFLIGHT, str(exc))
                last = status.get("LastTerminalRun") or {}
                active = status.get("ActiveRun") or {}
                busy = bool(status.get("IsInProgress"))
                self.log(step="preflight", check="sync", inProgress=busy,
                         inMemory=status.get("InMemorySynchronizationInProgress"),
                         globalLockHeld=status.get("IsGlobalLockHeld"),
                         globalLockStatusAvailable=status.get("IsGlobalLockStatusAvailable"),
                         activeRun=run_key(active.get("PipelineRunId")) or None,
                         activeType=status.get("ActiveSynchronizationType"), startedBy=status.get("StartedBy"),
                         lastTerminalRun=run_key(last.get("PipelineRunId")) or None,
                         lastTerminalStatus=last.get("Status"), lastCompletedAt=last.get("CompletedAtUtc"))
                if not busy or time.time() >= busy_deadline:
                    break
                time.sleep(max(self.args.poll, 15))
            problems = self.preflight()
            if self.dry:
                if busy:
                    problems.insert(0, "a synchronization is already in progress")
                for number, step in enumerate(self.plan(), 1):
                    print(f"PLAN {number}. {step}", flush=True)
                for problem in problems:
                    print(f"PROBLEM {problem}", flush=True)
                print(f"DRY RUN: nothing was started, the {self.source} was not deleted, no state was written; "
                      + ("the run would stop at pre-flight" if problems else "the run would proceed"), flush=True)
                return EXIT_PREFLIGHT if problems else 0
            if busy:
                return self.fail(EXIT_BUSY, f"a synchronization was already in progress for more than "
                                            f"{self.args.busy_wait:g} s; this run started nothing")
            if problems:
                self.log(step="start", ok=False, reason="pre-flight failed", problems=problems)
                self.finish(False, {}, {}, problems)
                return EXIT_PREFLIGHT
            self.consume_login()
            self.log(step="start", window=[self.args.date_from, self.args.date_to] if self.args.date_from
                     else "server daily lookback")
            bases: dict[str, dict] = {}
            if self.args.mode != "refresh":
                for for_amg in (True, False):
                    bases["amg" if for_amg else "fenix"] = self.session(for_amg)
            sessions_ok = all(item["ok"] for item in bases.values())
            data_changed = self.args.mode == "refresh" or any(item["ok"] for item in bases.values())
            since = datetime.now().astimezone() if bases else self.started_at
            downstream: dict[str, bool] = {}
            if self.args.mode in ("full", "refresh"):
                if data_changed:
                    downstream["search"] = self.rebuild_search()
                    if not downstream["search"]:
                        self.log(step="search", ok=False,
                                 note="the search rebuild failed; the AI refresh is independent and runs anyway")
                    downstream["ai"] = self.refresh_ai(since)
                else:
                    self.log(step="downstream", ok=False,
                             reason="no base finished, so the data did not change; search and AI were not refreshed")
            done = sessions_ok and all(downstream.values()) and (data_changed or self.args.mode == "light")
            self.finish(done, bases, downstream, [])
            return 0 if done else EXIT_FAILED
        finally:
            if lock is not None:
                lock.close()

    def fail(self, code: int, reason: str) -> int:
        """Record a run that ended before any session and return its exit code."""
        self.log(step="start", ok=False, reason=reason)
        if not self.dry:
            self.write_latest({"ok": False, "failure": reason, "exit": code, "sessions": False, "bases": {},
                               "downstream": {}, "preflight_problems": []})
        return code

    def write_latest(self, result: dict) -> None:
        (self.state_dir / "latest.json").write_text(json.dumps(
            {"run": self.run_id, "mode": self.args.mode, **result, "finished_at": iso_now()},
            ensure_ascii=False) + "\n")

    def finish(self, done: bool, bases: dict, downstream: dict, problems: list[str]) -> None:
        sessions = all(item["ok"] for item in bases.values())
        self.log(step="done", ok=done, sessions=sessions, bases={name: item["ok"] for name, item in bases.items()},
                 downstream=downstream)
        failure = None
        if not done:
            parts = [f"pre-flight: {problem}" for problem in problems]
            parts += [f"{name}: {item.get('reason') or item.get('status') or 'failed'}"
                      + (f" at {item['failedStep']}" if item.get("failedStep") else "")
                      for name, item in bases.items() if not item["ok"]]
            parts += [f"{name} was not refreshed" for name, ok in downstream.items() if not ok]
            failure = "; ".join(parts)[:1500] or "the run did not complete"
        self.write_latest({"ok": done, "failure": failure, "exit": 0 if done else
                           (EXIT_PREFLIGHT if problems else EXIT_FAILED), "sessions": sessions, "bases": bases,
                           "downstream": downstream, "preflight_problems": problems})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("light", "full", "refresh"), required=True)
    parser.add_argument("--from", dest="date_from", default="")
    parser.add_argument("--to", dest="date_to", default="")
    parser.add_argument("--server", default="http://127.0.0.1:35981")
    parser.add_argument("--shop-api", default="http://127.0.0.1:62506")
    parser.add_argument("--cutover-id", default="")
    parser.add_argument("--credential-file", type=Path,
                        help="persistent service credential (root, 0600); used when it exists, never deleted")
    parser.add_argument("--login-file", type=Path, help="one-time login, used when no credential file exists")
    parser.add_argument("--consume-login", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/gba-sync"))
    parser.add_argument("--ai-state-dir", type=Path, default=Path("/var/lib/gba-ai-fleet"))
    parser.add_argument("--poll", type=float, default=20, help="seconds between status polls")
    parser.add_argument("--busy-wait", type=float, default=900,
                        help="seconds to wait when the server reports a synchronization in progress")
    parser.add_argument("--login-timeout", type=int, default=300)
    parser.add_argument("--status-timeout", type=int, default=900)
    parser.add_argument("--start-grace", type=int, default=300)
    parser.add_argument("--session-timeout", type=int, default=10 * 3600)
    parser.add_argument("--search-timeout", type=int, default=3600)
    parser.add_argument("--search-settle", type=int, default=300)
    parser.add_argument("--fleet-lock-wait", type=int, default=3 * 3600)
    parser.add_argument("--fleet-timeout", type=int, default=12 * 3600)
    return parser


if __name__ == "__main__":
    raise SystemExit(Runner(build_parser().parse_args()).run())
