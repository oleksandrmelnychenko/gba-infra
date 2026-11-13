# Nightly 1C to GBA synchronization on DEV

Every night at 02:00 Europe/Kyiv `gba-sync-nightly.timer` starts
`gba-sync@light.service`: AMG, then Fenix, daily sessions with the server's own
lookback. It takes about two hours. Nothing downstream is refreshed by this run;
the shop index rebuilds at 03:00 and the AI fleet refreshes at 05:00 on their
own schedules.

The timer has `Persistent=false`: a night that was missed (host down, timer
disabled) is not made up during the day. Start it by hand when that is wanted.

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
synchronization was already running, 4 pre-flight failed.

The QA desk does not turn failed systemd units into tasks: its sentinel reads
container logs only, and its architecture tab shows a unit only when it is
listed in `server/topology.js` (a one-shot service is "down" there between
runs, so list the timer).

## The 05:00 AI cycle

`gba-ai-fleet-refresh.service` runs `refresh_ai_fleet.py --wait-sync 5400`:
while the synchronization runner holds `/var/lib/gba-sync/run.lock` the cycle
waits, at most 90 minutes, and then starts. `sync_wait` in
`/var/lib/gba-ai-fleet/<day>.json` records the wait and whether the
synchronization was still running when the cycle started. A synchronization
started from the console is not seen by this wait.

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
  systemd/gba-ai-fleet-refresh.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now gba-sync-nightly.timer
```
