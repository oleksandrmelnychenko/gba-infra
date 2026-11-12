#!/usr/bin/env python3
"""Shared view of the seven GBA AI web services: unit state, /health body, runtime.

A service is "up" when its unit is active and /health answers with a JSON object.
It is "healthy" when it is up, the answer is HTTP 200 and the body says
status == "healthy" (solvency may say "degraded" while serving_ready is true:
its forward model is allowed to be unsupported). Anything else is reported with
the reason so the caller can name the service.

The maintenance marker (/var/lib/gba-ai-fleet/maintenance) means somebody stopped
services on purpose: nothing here starts a unit while it exists.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import urllib.error
import urllib.request

SERVICES = {"reco": 8000, "procure": 8001, "nba": 8002, "solvency": 8003,
            "pricing": 8004, "products": 8005, "forecast": 8006}
MAINTENANCE_MARKER = Path("/var/lib/gba-ai-fleet/maintenance")
MAINTENANCE_STALE_SECONDS = 6 * 3600
RUNNING_STATES = ("active", "activating", "reloading")


def unit(service: str) -> str:
    return f"gba-{service}.service"


def systemctl(*args: str, timeout: int = 90) -> tuple[int, str]:
    try:
        done = subprocess.run(["/usr/bin/systemctl", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 255, str(exc)
    return done.returncode, (done.stdout or done.stderr).strip()


def http_get(url: str, timeout: int = 20) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def maintenance(marker: Path | None = None) -> dict:
    marker = marker or MAINTENANCE_MARKER
    try:
        age = max(0, int(time.time() - marker.stat().st_mtime))
    except OSError:
        return {"present": False}
    return {"present": True, "path": str(marker), "age_seconds": age,
            "stale": age > MAINTENANCE_STALE_SECONDS}


def body_verdict(service: str, code: int, raw: bytes) -> dict:
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return {"http": code, "up": False, "healthy": False, "reason": "health answer is not a JSON object"}
    status = body.get("status")
    accepted = status == "healthy" or (
        service == "solvency" and status == "degraded" and body.get("serving_ready") is True)
    verdict = {"http": code, "up": True, "status": status, "healthy": code == 200 and accepted}
    for flag in ("business_ready", "db_connected"):
        if flag in body:
            verdict[flag] = body[flag]
    if not verdict["healthy"]:
        detail = body.get("reasons") or body.get("business_reason") or body.get("detail")
        verdict["reason"] = f"HTTP {code}, status {status!r}" + (f": {detail}" if detail else "")
    return verdict


def probe(service: str, timeout: int = 20) -> dict:
    try:
        code, raw = http_get(f"http://127.0.0.1:{SERVICES[service]}/health", timeout)
    except Exception as exc:
        return {"up": False, "healthy": False, "reason": f"no answer: {str(exc)[:160]}"}
    return body_verdict(service, code, raw)


def first_word(text: str) -> str:
    words = text.split()
    return words[0] if words else "unknown"


def unit_state(service: str) -> dict:
    return {"active": first_word(systemctl("is-active", unit(service))[1]),
            "enabled": first_word(systemctl("is-enabled", unit(service))[1])}


def check(service: str) -> dict:
    state = unit_state(service)
    result = {**state, **probe(service)}
    if state["active"] != "active":
        result.update(up=False, healthy=False,
                      reason=f"unit is {state['active']}" + (f"; {result['reason']}" if result.get("reason") else ""))
    return result


def check_fleet(*, start_inactive: bool = False, settle_seconds: int = 120,
                marker: Path | None = None, services: tuple[str, ...] | None = None) -> dict:
    """Check every service; optionally start an enabled-but-inactive unit once and wait for it."""
    names = services or tuple(SERVICES)
    results = {name: check(name) for name in names}
    marker_state = maintenance(marker)
    started = []
    for name, result in results.items():
        if result["active"] in RUNNING_STATES or not result["enabled"].startswith("enabled"):
            continue
        if not start_inactive:
            continue
        if marker_state["present"]:
            result["start_skipped"] = "maintenance marker present"
            continue
        code, output = systemctl("start", unit(name))
        result["start_attempted"] = True
        result["start_exit"] = code
        if code:
            result["start_error"] = output[:300]
        started.append(name)
    deadline = time.time() + settle_seconds
    pending = [name for name in names if results[name].get("start_attempted")
               or (start_inactive and results[name]["active"] == "activating")]
    while pending:
        for name in list(pending):
            fresh = check(name)
            keep = {key: results[name][key] for key in ("start_attempted", "start_exit", "start_error")
                    if key in results[name]}
            results[name] = {**fresh, **keep}
            if fresh["up"]:
                pending.remove(name)
        if not pending or time.time() >= deadline:
            break
        time.sleep(5)
    return {"checked_at": now_iso(), "maintenance": marker_state, "started": started,
            "services": results,
            "down": [name for name in names if not results[name]["up"]],
            "unhealthy": [name for name in names if not results[name]["healthy"]]}


def parse_environment(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def unit_runtime(service: str) -> dict | None:
    """Working directory, interpreter and environment the web unit really runs with."""
    code, output = systemctl("show", unit(service), "-p", "LoadState", "-p", "WorkingDirectory",
                             "-p", "ExecStart", "-p", "EnvironmentFiles", "-p", "Environment")
    if code:
        return None
    properties: dict[str, list[str]] = {}
    for line in output.splitlines():
        key, _, value = line.partition("=")
        properties.setdefault(key, []).append(value)
    if properties.get("LoadState", [""])[0] != "loaded":
        return None
    working = (properties.get("WorkingDirectory") or [""])[0].lstrip("!-")
    execution = (properties.get("ExecStart") or [""])[0]
    interpreter = ""
    for part in execution.strip("{} ").split(" ; "):
        key, _, value = part.strip().partition("=")
        if key == "path":
            interpreter = value.strip()
    if not working or not interpreter:
        return None
    environment: dict[str, str] = {}
    files = []
    for entry in properties.get("EnvironmentFiles", []):
        path = entry.split(" (")[0].strip().lstrip("-")
        if not path:
            continue
        files.append(path)
        try:
            environment.update(parse_environment(Path(path).read_text()))
        except OSError:
            if "ignore_errors=yes" not in entry:
                return None
    names = []
    for entry in properties.get("Environment", []):
        for item in shlex.split(entry):
            key, separator, value = item.partition("=")
            if separator:
                environment[key] = value
                names.append(key)
    return {"cwd": working, "python": interpreter, "environment": environment,
            "environment_files": files, "environment_names": sorted(names)}


def runtime_environment(runtime: dict) -> dict:
    return {**os.environ, **runtime.get("environment", {})}
