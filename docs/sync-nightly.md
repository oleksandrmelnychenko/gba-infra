# Nightly 1C to GBA synchronization on DEV

Every night at 02:00 Europe/Kyiv `gba-sync-nightly.timer` starts
`gba-sync@light.service`: AMG, then Fenix, daily sessions with the server's own
lookback. It takes about two hours. Nothing downstream is refreshed by this run;
the shop index rebuilds at 03:00 and the AI fleet refreshes at 05:00 on their
own schedules.

The timer has `Persistent=false`: a night that was missed (host down, timer
disabled) is not made up during the day. Start it by hand when that is wanted.

## Morning check

```bash
cat /var/lib/gba-sync/latest.json; echo                      # runner: ok, failure, exit, result per base
systemctl show gba-sync@light.service -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp
grep '"check": "sync"' /var/lib/gba-sync/run.log | tail -3   # what the server said at 02:00 (inMemory, globalLockHeld, activeRun)
python3 /root/projects/gba-infra/scripts/sync_step_times.py --runs 3        # step table, last night against the nights before
python3 -c "import json;s=json.load(open('/var/lib/gba-ai-fleet/latest.json'));print(s['cycle_date'],s['status'],s.get('failure'),s.get('sync_wait'),{k:v['status'] for k,v in s['stages'].items()})"
systemctl list-timers 'gba-sync*' 'gba-sql-statistics*' 'gba-ai-fleet*' 'gba-e2e*' --no-pager
journalctl -u 'gba-sql-statistics@dev.service' --since today --no-pager | grep -E 'statistics |ok in|failed|skipped'
python3 -c "import sqlite3;c=sqlite3.connect('file:/var/lib/docker/volumes/gba-bug-host_qa-data/_data/gba-qa.sqlite?mode=ro',uri=True);[print(*r) for r in c.execute(\"select id,status,created_at,substr(title,1,110) from tasks where notes like '%[sentinel%' and created_at>=strftime('%Y-%m-%dT%H:%M:%S','now','-1 day') order by created_at\")]"
cat /root/.cache/claude-stocklock/night-summary.txt          # stock-lock trial of 14.11 only
```

Line 1 `"ok": true` and line 5 `complete` with `sync_wait` absent or
`still_running: false` is a good night. Line 8 lists the tasks the desk sentinel
opened in the last 24 hours; a failed run or cycle appears there with its
`failure` text as the title.

## Service account

| | |
|---|---|
| Login | `svc-sync-dev` (shown as «Службовий: синхронізація 1С») |
| Role | `Service: 1C sync`, role type 13, no dashboard pages |
| Permission | `administration.online_shop_seo.synchronization.run`, the only one |
| Credential | `/etc/gba-sync/credential.json`, root, mode 0600, directory 0700 |
| Stand | DEV only (`http://127.0.0.1:35981`) |

The permission opens the synchronization status and every synchronization start
route of the server. The role name is not one of the manager roles, so the
account is absent from manager pickers, and imported documents take their
responsible user from `DataSync__ImportedDocument*ResponsibleUserNetUid`, not
from the account that started the run.

Until the server change on branch `claude/sync-role` is deployed, the account
can also call the routes that require only a signed-in internal user. After it,
a role whose name starts with `Service:` passes only permission-gated routes.

The password exists only in the credential file. The runner refuses the file
unless it is a regular file owned by root with mode 0600, and never deletes it.
When the file is absent the runner falls back to the one-time
`/var/lib/gba-sync/login.private.json`, which it deletes at start as before.

## Rotate the password

```bash
python3 /root/projects/gba-infra/scripts/sync_service_account.py rotate \
  --admin-login-file <JSON with Username and Password of an administrator>
```

It sets a new random password through the server, rewrites the credential file
and logs in with it. The server takes a new password in the query string and its
request log keeps query strings, so the script overwrites the password in the
container log (`gba-dev-data-concord-1`) and fails if it cannot. The
administrator needs `administration.users.user.password_reset`; the file with
the administrator's login is read and left as it is.

`provision` recreates the role, the grant and the user when they are missing;
`verify` logs in with the stored credential and checks the grants.

## Run by hand

```bash
systemctl start gba-sync@light        # sessions only
systemctl start gba-sync@full         # sessions, shop index rebuild, AI refresh
set -a; . /etc/gba-sync.env; set +a
python3 /root/projects/gba-infra/scripts/gba_sync_run.py --mode light --dry-run \
  --credential-file /etc/gba-sync/credential.json --cutover-id "$GBA_SYNC_CUTOVER_ID"
```

The dry run logs in, reads the status, prints the plan and starts nothing.

## Result and failures

`/var/lib/gba-sync/latest.json` holds the last run: `ok`, `failure` (one line,
`null` on success), `exit`, a result per base. `/var/lib/gba-sync/run.log` has
every step. A failed run exits non-zero, so `systemctl is-failed
gba-sync@light.service` and `journalctl -u gba-sync@light.service` show it.
Exit codes: 1 a session failed, 2 the credential is unusable, 3 a
synchronization was already running, 4 pre-flight failed, 5 the server's
in-memory flag is stale.

The desk sentinel reads `/var/lib/gba-sync/latest.json` and
`/var/lib/gba-ai-fleet/latest.json` and opens a task for a failed run with the
`failure` text as its title. The architecture tab shows a unit only when it is
listed in `server/topology.js` (a one-shot service is "down" there between
runs, so list the timer).

### Busy at the start

When the status says `IsInProgress` the runner polls it for `--busy-wait`
seconds (900) and logs `inMemory`, `globalLockHeld`, `globalLockStatusAvailable`
and `activeRun` on every poll. Three different things make the server busy:

| Poll shows | Cause | Runner |
|---|---|---|
| `globalLockHeld: true`, no `activeRun`, `inMemory: false` | another holder of `GBA_DATASYNC_GLOBAL_LOCK`. Every day at 00:00 UTC (02:00 in winter, 03:00 in summer, Kyiv) and every 6 hours after it the server's stock snapshot (`CaptureStockSnapshotActor`) takes the lock in shared mode; the status probe cannot tell it from a synchronization. This is what stopped the run of 13.11 at 02:00:01. | waits, then starts |
| `activeRun` set | a synchronization started from the console or by an earlier run | waits; exit 3 after the limit |
| `inMemory: true`, `globalLockHeld: false`, no `activeRun` on every poll of the wait | stale in-memory flag in `DataSyncManagementActor`: no run exists and the flag does not clear by itself | exit 5, failure «сервер повідомляє про синхронізацію без активного запуску (завислий прапорець)» |

The runner never restarts the server. After exit 5 restart `data-concord` by
hand outside QA hours (the flag lives in the actor's memory) and start
`gba-sync@light` again; report the case as a server defect with the three flags
from `run.log`.

## The 05:00 AI cycle

`gba-ai-fleet-refresh.service` runs `refresh_ai_fleet.py --wait-sync 5400`. The
cycle waits, at most 90 minutes in total, while either is true:

- the synchronization runner holds `/var/lib/gba-sync/run.lock`;
- the server's `/api/v1/uk/data/sync/status` says `IsInProgress` (read with the
  service credential), which also covers a synchronization started from the
  console.

`sync_wait` in `/var/lib/gba-ai-fleet/<day>.json` records the wait:
`waited_seconds`, `still_running` (the cycle started on the data it had),
`waited_for` (`runner lock`, `server status`) and, when the status could not be
read (server down, credential missing or refused), `server_fallback` with the
reason; in that case only the lock decides, as before. `--sync-server ''`
switches the server check off; `--sync-credential` names another credential
file. The 00:00 UTC stock snapshot also reads as busy while it runs.

## Night schedule

| Kyiv time | Unit | Notes |
|---|---|---|
| 02:00 | `gba-sync-nightly.timer` → `gba-sync@light` | about two hours; each session ends with up to 15 minutes of batch-history retention |
| 04:15 | `gba-sql-statistics@dev.timer` | drop-in `gba-sql-statistics@dev.timer.d/after-sync.conf`; the service is ordered after `gba-sync@*`, so it queues until a running sync ends; no catch-up by day (`Persistent=false`) |
| 05:00 | `gba-ai-fleet-refresh.timer` | waits for a synchronization as described above |
| 06:15 | `gba-e2e-nightly.timer` | ordered after the sync, the statistics and the AI cycle |

Until 14.11 the statistics ran at 03:30 and the E2E suite at 02:30, inside the
synchronization window. The statistics job skips both databases when a
synchronization holds `GBA_DATASYNC_GLOBAL_LOCK` at its start and otherwise
runs FULLSCAN updates on `ConcordDb_V5` beside the Fenix session and its batch
refresh, so on a sync night it either did nothing or competed with the import.
The E2E suite brings up its own stack, reverts three database snapshots and runs
Playwright for up to three hours on the same SQL Server and host. The statistics
template shared with prod is unchanged; only the dev instance has the drop-in.

The new times take effect from the night of 15.11: a one-shot transient timer
`gba-claude-night-schedule` installs the units on 14.11 at 12:00 and restarts
the two timers (log `/root/.cache/claude-syncgaps/apply-night-schedule.log`). It
does not survive a reboot; apply by hand with the Install commands below.

## Disable

```bash
systemctl disable --now gba-sync-nightly.timer     # stop the schedule
systemctl stop gba-sync@light.service              # stop a run in progress (the server finishes the session it started)
```

To retire the account, delete the user `svc-sync-dev` in the console
(Administration, Users) and remove `/etc/gba-sync/credential.json`.

## Install

```bash
install -m 0644 systemd/gba-sync@.service systemd/gba-sync-nightly.timer \
  systemd/gba-ai-fleet-refresh.service systemd/gba-e2e-nightly.service \
  systemd/gba-e2e-nightly.timer /etc/systemd/system/
install -d /etc/systemd/system/gba-sql-statistics@dev.timer.d /etc/systemd/system/gba-sql-statistics@dev.service.d
install -m 0644 systemd/gba-sql-statistics@dev.timer.d/after-sync.conf /etc/systemd/system/gba-sql-statistics@dev.timer.d/
install -m 0644 systemd/gba-sql-statistics@dev.service.d/after-sync.conf /etc/systemd/system/gba-sql-statistics@dev.service.d/
systemctl daemon-reload
systemctl enable --now gba-sync-nightly.timer
systemctl restart gba-e2e-nightly.timer gba-sql-statistics@dev.timer
```
