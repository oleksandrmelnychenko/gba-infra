#!/usr/bin/env python3
"""Start GBA AI web services that are enabled but not running.

Runs every two minutes from gba-ai-watchdog.timer. A unit that is enabled and
neither active nor activating is started and one line is logged. A disabled or
masked unit is left alone. While the maintenance marker
/var/lib/gba-ai-fleet/maintenance exists nothing is started; its age is logged
on every run and a warning is logged once it is older than six hours, so a
forgotten marker stays visible. The /health body of every active unit is
recorded and an unhealthy one is logged; it is not restarted here.

The last check and the last fifty actions are kept in
/var/lib/gba-ai-fleet/watchdog.json. --dry-run reports what would be started
and changes nothing, the state file included.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_fleet_health as health

HISTORY = 50


def patrol(state_dir: Path, *, dry_run: bool = False, marker: Path | None = None) -> dict:
    marker = marker or state_dir / "maintenance"
    marker_state = health.maintenance(marker)
    checked_at = health.now_iso()
    services: dict[str, dict] = {}
    actions: list[dict] = []
    for name in health.SERVICES:
        state = health.unit_state(name)
        entry = dict(state)
        enabled = state["enabled"].startswith("enabled")
        running = state["active"] in health.RUNNING_STATES
        if running:
            entry["action"] = "none"
            if state["active"] == "active":
                verdict = health.probe(name)
                entry["healthy"] = verdict["healthy"]
                if not verdict["healthy"]:
                    entry["reason"] = verdict.get("reason")
                    print(f"WARNING gba-{name} is active but not healthy: {verdict.get('reason')}", flush=True)
        elif not enabled:
            entry["action"] = "none (unit is not enabled)"
        elif marker_state["present"]:
            entry["action"] = "not started: maintenance marker present"
            print(f"gba-{name} is {state['active']}; not started because of the maintenance marker", flush=True)
        elif dry_run:
            entry["action"] = "would start"
            print(f"DRY RUN gba-{name} is {state['active']} and enabled; it would be started", flush=True)
        else:
            code, output = health.systemctl("start", health.unit(name))
            entry["action"] = "started" if code == 0 else f"start failed (exit {code})"
            action = {"at": checked_at, "service": name, "was": state["active"], "action": entry["action"]}
            if code:
                action["error"] = output[:300]
            actions.append(action)
            print(f"gba-{name} was {state['active']} and enabled; systemctl start exit {code}"
                  + (f": {output[:300]}" if code else ""), flush=True)
        services[name] = entry
    if marker_state["present"]:
        hours = marker_state["age_seconds"] / 3600
        text = f"maintenance marker {marker} is present, age {hours:.1f} h; no unit is started while it exists"
        print(("WARNING forgotten? " if marker_state["stale"] else "") + text, flush=True)
    report = {"checked_at": checked_at, "dry_run": dry_run, "maintenance": marker_state,
              "services": services, "actions": actions}
    if not actions and not marker_state["present"] and all(
            entry["action"] == "none" and entry.get("healthy", True) for entry in services.values()):
        print(f"all {len(services)} units active and healthy; no action", flush=True)
    return report


def record(state_dir: Path, report: dict) -> None:
    path = state_dir / "watchdog.json"
    try:
        history = json.loads(path.read_text()).get("recent_actions", [])
    except (OSError, ValueError):
        history = []
    report = {**report, "recent_actions": (history + report["actions"])[-HISTORY:]}
    state_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/gba-ai-fleet"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    report = patrol(args.state_dir, dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    else:
        record(args.state_dir, report)
    return 1 if any(action["action"] != "started" for action in report["actions"]) else 0


if __name__ == "__main__":
    sys.exit(main())
