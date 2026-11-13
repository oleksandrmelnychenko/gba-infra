#!/usr/bin/env python3
"""Per-step durations of the last 1C synchronization sessions, side by side. Read-only.

Sessions are taken from the runner log (/var/lib/gba-sync/run.log): each base of
each run is one column, from the accepted start call to the reported end. A
session the runner did not start is added with
--session LABEL,BASE,START_UTC,END_UTC (ISO instants, BASE is amg or fenix).

Durations come from the server's data_sync_log.txt: every event that carries
DurationMs (STEP_END, RUN_END, *_COMPLETED, ...) and whose UTC "Triggered at"
line falls inside the session. Values are minutes. Three summary rows explain
the wall time: the session itself, the sum of the worker runs (RUN_END) and what
is left outside any worker run (waits between stages, batch refresh, retention).

  python3 scripts/sync_step_times.py              the last 3 runner runs
  python3 scripts/sync_step_times.py --runs 5 --min-seconds 5
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

SERVER_LOG = Path("/root/projects/gba-server/src/Global.Business.Assistant.Api/Logs/data_sync_log.txt")
RUNNER_LOG = Path("/var/lib/gba-sync/run.log")
BASES = ("amg", "fenix")
SLACK = timedelta(seconds=5)


def utc(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def runner_sessions(path: Path, runs: int) -> list[dict]:
    """Sessions of the last runs that started at least one session, oldest first."""
    opened: dict[tuple[str, str], dict] = {}
    sessions: list[dict] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("step") != "session" or event.get("base") not in BASES:
            continue
        key = (str(event.get("run")), event["base"])
        try:
            at = utc(str(event.get("at")))
        except ValueError:
            continue
        if "seconds" not in event:
            opened[key] = {"run": key[0], "base": key[1], "start": at, "pipeline": event.get("pipelineRun")}
        elif key in opened:
            session = opened.pop(key)
            session.update(end=at, ok=bool(event.get("ok")), status=event.get("status") or event.get("reason"))
            sessions.append(session)
    keep = list(dict.fromkeys(session["run"] for session in sessions))[-max(runs, 0):] if runs > 0 else []
    chosen = [session for session in sessions if session["run"] in keep]
    for session in chosen:
        session["label"] = session["start"].astimezone().strftime("%m-%d %H:%M")
    return chosen


def manual_session(text: str) -> dict:
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4 or parts[1] not in BASES:
        raise ValueError(f"--session {text!r}: expected LABEL,BASE,START_UTC,END_UTC with BASE amg or fenix")
    start, end = utc(parts[2]), utc(parts[3])
    if end <= start:
        raise ValueError(f"--session {text!r}: the end is not after the start")
    return {"run": parts[0], "label": parts[0], "base": parts[1], "start": start, "end": end, "ok": None,
            "status": None, "pipeline": None}


def server_events(path: Path, since: datetime, until: datetime):
    """Yield (utc instant, event) for events with a duration inside [since, until]."""
    pending: dict | None = None
    with path.open(errors="replace") as source:
        for line in source:
            text = line.strip()
            if text.startswith('{"Severity"'):
                try:
                    pending = json.loads(text)
                except ValueError:
                    pending = None
                continue
            if pending is None or not text.startswith("Triggered at:"):
                continue
            event, pending = pending, None
            if not isinstance(event.get("DurationMs"), (int, float)):
                continue
            try:
                at = datetime.strptime(text[13:].strip().removesuffix("UTC").strip(),
                                       "%m/%d/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if since <= at <= until:
                yield at, event


def row_key(event: dict) -> tuple[int, str]:
    name, step = str(event.get("Event")), event.get("Step")
    operation = str(event.get("OperationType") or "")
    if name == "RUN_END":
        return 1, f"RUN {operation} ({str(event.get('Actor') or '').removesuffix('Actor')})"
    if name == "STEP_END":
        return 2, f"{operation}: {step}"
    return 3, f"{name}" + (f" [{step}]" if step else "")


def collect(sessions: list[dict], log: Path) -> None:
    for session in sessions:
        session["rows"] = {}
        session["order"] = {}
    if not sessions:
        return
    since = min(session["start"] for session in sessions) - SLACK
    until = max(session["end"] for session in sessions) + SLACK
    for at, event in server_events(log, since, until):
        for session in sessions:
            if session["start"] - SLACK <= at <= session["end"] + SLACK \
                    and bool(event.get("ForAmg")) == (session["base"] == "amg"):
                key = row_key(event)
                session["rows"][key] = session["rows"].get(key, 0.0) + float(event["DurationMs"]) / 1000
                session["order"].setdefault(key, at)
                break


def minutes(seconds: float | None) -> str:
    return "" if seconds is None else f"{seconds / 60:.1f}"


def table(base: str, sessions: list[dict], min_seconds: float) -> list[str]:
    columns = [session for session in sessions if session["base"] == base]
    if not columns:
        return []
    first_seen: dict[tuple[int, str], datetime] = {}
    for session in columns:
        for key, at in session["order"].items():
            offset = at - session["start"]
            if key not in first_seen or offset < first_seen[key]:
                first_seen[key] = offset
    keys = [key for key in first_seen
            if max(session["rows"].get(key, 0.0) for session in columns) >= min_seconds]
    lines: list[list[str]] = [["step (minutes)"] + [session["label"] for session in columns]
                              + (["last-prev"] if len(columns) > 1 else [])]

    def add(label: str, values: list[float | None]) -> None:
        cells = [label] + [minutes(value) for value in values]
        if len(columns) > 1:
            last, previous = values[-1], values[-2]
            cells.append("" if last is None or previous is None else f"{(last - previous) / 60:+.1f}")
        lines.append(cells)

    wall = [(session["end"] - session["start"]).total_seconds() for session in columns]
    runs = [sum(value for key, value in session["rows"].items() if key[0] == 1) for session in columns]
    add("SESSION wall time", wall)
    add("  in worker runs (sum of RUN)", runs)
    add("  outside worker runs", [total - inside for total, inside in zip(wall, runs)])
    for group in (1, 2, 3):
        for key in sorted((key for key in keys if key[0] == group), key=lambda key: first_seen[key]):
            add(key[1], [session["rows"].get(key) for session in columns])
    widths = [max(len(line[index]) for line in lines) for index in range(len(lines[0]))]
    out = [f"== {base.upper()}  " + "  ".join(
        f"{session['label']}: {'ok' if session['ok'] else 'FAILED ' + str(session['status']) if session['ok'] is False else 'manual'}"
        for session in columns)]
    for number, line in enumerate(lines):
        out.append("  ".join(cell.ljust(widths[index]) if index == 0 else cell.rjust(widths[index])
                             for index, cell in enumerate(line)))
        if number == 0:
            out.append("  ".join("-" * width for width in widths))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3, help="how many of the last runner runs to show")
    parser.add_argument("--min-seconds", type=float, default=20,
                        help="hide a step that never took this long in any shown session")
    parser.add_argument("--log", type=Path, default=SERVER_LOG, help="the server's data_sync_log.txt")
    parser.add_argument("--runner-log", type=Path, default=RUNNER_LOG)
    parser.add_argument("--session", action="append", default=[], metavar="LABEL,BASE,START_UTC,END_UTC",
                        help="add a session the runner did not start; repeatable")
    args = parser.parse_args(argv)
    try:
        sessions = [manual_session(text) for text in args.session]
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    sessions += runner_sessions(args.runner_log, args.runs)
    sessions.sort(key=lambda session: session["start"])
    if not sessions:
        print(f"no sessions found in {args.runner_log}; pass --session", file=sys.stderr)
        return 1
    try:
        collect(sessions, args.log)
    except OSError as exc:
        print(f"{args.log} cannot be read: {exc.strerror or exc}", file=sys.stderr)
        return 1
    for base in BASES:
        lines = table(base, sessions, args.min_seconds)
        if lines:
            print("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
