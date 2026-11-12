#!/usr/bin/env python3
"""Run a 1C to GBA synchronization on one stand in one of two modes.

light  AMG and Fenix daily sessions only. Nothing downstream is refreshed.
full   The same sessions, then a full rebuild of the shop search index and a
       forced AI fleet refresh, each confirmed before the run is reported done.

The same entry point serves the timer and manual runs. flock prevents overlap.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

AI_PORTS = {"reco": 8000, "procure": 8001, "nba": 8002, "solvency": 8003,
            "pricing": 8004, "products": 8005, "forecast": 8006}


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state_dir: Path = args.state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.state_dir / "run.log"
        self.run_id = uuid.uuid4().hex
        self.token_value: tuple[str, float] | None = None
        self.login = args.login_file.read_bytes()
        if args.consume_login:
            args.login_file.unlink()

    def log(self, **event: object) -> None:
        line = {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "run": self.run_id, "mode": self.args.mode, **event}
        text = json.dumps(line, ensure_ascii=False)[:4000]
        with self.log_path.open("a") as output:
            output.write(text + "\n")
        print(text, flush=True)

    def token(self) -> str:
        if self.token_value and self.token_value[1] > time.time() + 120:
            return self.token_value[0]
        request = urllib.request.Request(
            self.args.server + "/api/v1/uk/usermanagement/token", data=self.login,
            headers={"Content-Type": "application/json"})
        body = json.loads(urllib.request.urlopen(request, timeout=60).read())["Body"]
        self.token_value = (body["AccessToken"],
                            time.time() + float(body.get("AccessTokenExpiresIn", 600)))
        return self.token_value[0]

    def call(self, base: str, path: str, payload: dict | None = None, *,
             operation: str | None = None, method: str | None = None, timeout: int = 120) -> dict:
        headers = {"Content-Type": "application/json", "X-Time-Zone": "Europe/Kyiv",
                   "Authorization": "Bearer " + self.token()}
        if self.args.cutover_id:
            headers["X-GBA-Cutover-Id"] = self.args.cutover_id
        if operation:
            headers["X-GBA-Sync-Operation-Id"] = operation
        request = urllib.request.Request(
            base + path, data=None if payload is None else json.dumps(payload).encode(),
            headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def status(self) -> dict:
        last_error = ""
        for _ in range(240):
            try:
                return self.call(self.args.server, "/api/v1/uk/data/sync/status").get("Body") or {}
            except Exception as exc:
                last_error = str(exc)
                time.sleep(15)
        raise RuntimeError("synchronization status is unavailable: " + last_error)

    def wait_idle(self) -> None:
        while self.status().get("IsInProgress"):
            time.sleep(20)

    def session(self, for_amg: bool) -> bool:
        name = "amg" if for_amg else "fenix"
        query = [("forAmg", str(for_amg).lower()), ("mode", "1")]
        if self.args.date_from:
            query += [("from", self.args.date_from), ("to", self.args.date_to or self.args.date_from)]
        started = time.time()
        try:
            accepted = self.call(
                self.args.server, "/api/v1/uk/data/sync/start/session?" + urllib.parse.urlencode(query),
                {}, operation=uuid.uuid4().hex)
        except urllib.error.HTTPError as exc:
            self.log(step="session", base=name, ok=False, http=exc.code,
                     body=exc.read().decode(errors="replace")[:1500])
            return False
        self.log(step="session", base=name, accepted=accepted.get("StatusCode"))
        time.sleep(8)
        self.wait_idle()
        last = self.status().get("LastTerminalRun") or {}
        ok = last.get("Status") == "Finished"
        self.log(step="session", base=name, ok=ok, seconds=round(time.time() - started),
                 status=last.get("Status"), failedStep=last.get("FailedStep"),
                 pipelineRun=last.get("PipelineRunId"))
        return ok

    def rebuild_search(self) -> bool:
        started = datetime.now(timezone.utc)
        clock = time.time()
        try:
            result = self.call(self.args.shop_api, "/api/v1/uk/elasticsearch/sync/full", {},
                               method="POST", timeout=3600).get("Body") or {}
            health = self.call(self.args.shop_api, "/api/v1/uk/elasticsearch/health").get("Body") or {}
        except urllib.error.HTTPError as exc:
            self.log(step="search", ok=False, http=exc.code,
                     body=exc.read().decode(errors="replace")[:1500])
            return False
        except Exception as exc:
            self.log(step="search", ok=False, error=str(exc))
            return False
        raw = str(health.get("lastSyncUtc") or "")
        synced = datetime.fromisoformat(raw[:19]).replace(tzinfo=timezone.utc) if raw else None
        ok = bool(result.get("success", result.get("Success"))) and bool(health.get("healthy")) \
            and not health.get("stale") and synced is not None and synced >= started.replace(microsecond=0)
        self.log(step="search", ok=ok, seconds=round(time.time() - clock), result=result, health=health)
        return ok

    def refresh_ai(self) -> bool:
        clock = time.time()
        script = Path(__file__).with_name("refresh_ai_fleet.py")
        process = subprocess.run([sys.executable, str(script), "--force"], timeout=20 * 3600)
        services = {}
        for name, port in AI_PORTS.items():
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=15) as response:
                    services[name] = response.status
            except Exception as exc:
                services[name] = str(exc)[:80]
        latest = self.args.ai_state_dir / "latest.json"
        state = json.loads(latest.read_text()) if latest.exists() else {}
        stages = {k: v.get("status") for k, v in (state.get("stages") or {}).items()}
        ok = process.returncode == 0 and state.get("status") == "complete" \
            and all(value == 200 for value in services.values())
        self.log(step="ai", ok=ok, seconds=round(time.time() - clock), exit=process.returncode,
                 cycle=state.get("cycle_date"), stages=stages, services=services)
        return ok

    def run(self) -> int:
        with (self.state_dir / "run.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.log(step="start", ok=False, reason="another run holds the lock")
                return 3
            if self.status().get("IsInProgress"):
                self.log(step="start", ok=False, reason="a synchronization is already in progress")
                return 3
            self.log(step="start", window=[self.args.date_from, self.args.date_to] if self.args.date_from
                     else "server daily lookback")
            ok = all(self.session(for_amg) for for_amg in (True, False))
            downstream: dict[str, bool] = {}
            if ok and self.args.mode == "full":
                downstream["search"] = self.rebuild_search()
                downstream["ai"] = self.refresh_ai()
            done = ok and all(downstream.values())
            self.log(step="done", ok=done, sessions=ok, downstream=downstream)
            (self.state_dir / "latest.json").write_text(json.dumps(
                {"run": self.run_id, "mode": self.args.mode, "ok": done, "sessions": ok,
                 "downstream": downstream,
                 "finished_at": datetime.now().astimezone().isoformat(timespec="seconds")}) + "\n")
            return 0 if done else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("light", "full"), required=True)
    parser.add_argument("--from", dest="date_from", default="")
    parser.add_argument("--to", dest="date_to", default="")
    parser.add_argument("--server", default="http://127.0.0.1:35981")
    parser.add_argument("--shop-api", default="http://127.0.0.1:62506")
    parser.add_argument("--cutover-id", default="")
    parser.add_argument("--login-file", type=Path, required=True)
    parser.add_argument("--consume-login", action="store_true")
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/gba-sync"))
    parser.add_argument("--ai-state-dir", type=Path, default=Path("/var/lib/gba-ai-fleet"))
    raise SystemExit(Runner(parser.parse_args()).run())
