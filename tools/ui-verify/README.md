# ui-verify

Read-only UI checks against the GBA console (DEV or QA data) for the QA Desk Codex pipeline.

The runner logs in as the **QA Verifier** user, opens one console URL, runs a few UI steps, evaluates a JSON spec, and saves evidence: a screenshot, a JSON result with every check, the console errors and the API calls the page made.

The verifier is read-only in two layers:

1. **Role.** The `QA Verifier` role holds only view, open and read-only document permissions (see [Verifier identity](#verifier-identity)). Every create, update, delete, approve, sync, import and payment permission is withheld.
2. **Network guard.** Inside the browser, the runner aborts every non-GET request to `/api/**` and `/hubs/**`. Three kinds of request get through: login and token refresh, SignalR negotiation, and a short list of read-only POST endpoints (batch lookups, report previews and choices, print and export). Blocked requests are listed in the evidence. The guard exists because gba-server still has non-GET endpoints without an event-permission gate, so the role alone cannot stop them.

## Verifier identity

| What | Where |
|---|---|
| Credentials (mode 600, root) | `/private/qa-desk-verifier/credentials.private.json`: `Login`, `Password`, `UserNetUid`, `Role`, `RoleNetUid`, console URLs |
| Login | `POST {console}/api/v1/uk/usermanagement/token` with `{"Username": Login, "Password": Password}`. The response body carries `AccessToken` (RS256, 60 min), and the `__Host-gba_*` cookies are set. A GET-only proxy can send `Authorization: Bearer <AccessToken>`. Each login adds a refresh-token row, so reuse the token for about 55 minutes instead of logging in per request. For QA data, send the request through the console origin with the cookie `gba_env=qa`. |
| Role | `QA Verifier`, `UserRoleType` 13 (the console's default type for custom roles). It is not one of the types the migrator auto-upgrades (Administrator, GBA, Head*, FinanceDirector), and its name is not in `InternalMutationRolesPolicy`. |
| Reviewed grants | `scripts/qa-verifier/permissions.json`: 185 granted, 310 withheld, each with a reason (catalog 2026.08.28.69) |
| Provisioning | `scripts/qa-verifier/qa-verifier.py` |

```bash
python3 scripts/qa-verifier/qa-verifier.py provision            # DEV through the user-management API, then mirror to QA
python3 scripts/qa-verifier/qa-verifier.py provision --rotate-password
python3 scripts/qa-verifier/qa-verifier.py prove --out /tmp/prove.json   # GET 200 / mutation 403 matrix on DEV, QA via console, QA direct
python3 scripts/qa-verifier/qa-verifier.py status
python3 scripts/qa-verifier/qa-verifier.py remove --dry-run     # shows what would go, then rolls back
python3 scripts/qa-verifier/qa-verifier.py remove               # removes the user, role, grants and identity rows on DEV and QA
```

- **DEV** is provisioned through the API with the operator token: `profiles/roles/create`, `profiles/roles/{netUid}/event-permissions` (PUT), and `users/create`. The create call carries a throwaway password, because the API takes the password in the query string and the backend logs query strings. `set-password.sql` then writes the real hash (ASP.NET Identity v3, PBKDF2-SHA512, 100000 iterations), so the real password never reaches a log.
- **QA** cannot use the API: logins always go to DEV, so the QA rows must carry the same `NetUID`s. `qa-mirror.sql` idempotently copies the role, grants, revision and user from `ConcordDb_V5` into `ConcordDb_V5_QA`. It is fenced on `@@SERVERNAME` and on the `GbaQaStandDb` marker. A `qa-refresh.sh` brings the verifier along anyway. After a `qa-clone`, run `provision --env qa` only if the clone predates the verifier.
- `provision` is idempotent. A new catalog key that `permissions.json` has not reviewed stays withheld, and provisioning prints a warning. To review it, add the key to `grant` or `withhold` and run `provision` again.
- `remove.sql` is fenced to the DEV pair (`ConcordDb_V5` + `ConcordIdentityDb`) and the QA pair. It deletes the user's preference rows, the grants, the revision, the role, the identity user (claims, roles and refresh tokens cascade), the `UserToken` rows and the identity role. If any business row references the user, it soft-deletes the user instead.

### What the verifier cannot see

Some read paths in gba-server are gated by a mutation key, so the verifier gets 403 on them by design:

| Read | Gated by | Where it shows |
|---|---|---|
| `GET clients/sales-ukraine/create/search` and the rest of the «Новий продаж» wizard | `sales.ukraine.sale.create` | The wizard button is disabled and the wizard cannot be opened |
| `GET payments/registers/search` | `payments.available_payments.outcome_order.create` | `/accounting/advanced-reports` shows one 403 |
| `GET storages/get/all` | `warehouse_accounting.transfers.transfer.create` | `/products/consignments/availabilities` shows one 403 |
| `GET data/sync/status` | `administration.online_shop_seo.synchronization.run` | sync status |

All 58 menu pages open; only the two pages above show a 403. Some keys look like reads but are withheld because they also gate writes, for example `warehouses.ukraine.shipment.print`, whose GET `sales/shipments/document/create/export` creates a shipment list. `permissions.json` lists them.

## Install

Host (Node ≥ 20):

```bash
cd tools/ui-verify
npm ci
npx playwright install chromium        # add --with-deps on a machine without the Chromium system libraries
```

Container image requirements:

- Base image `node:22-bookworm` or any Debian/Ubuntu Node ≥ 20 image. Alpine is not supported by Playwright's Chromium.
- Browsers: `npx playwright install --with-deps chromium` after `npm ci`. This needs root, or a prior root layer, for the apt packages.
- Network: the container must reach the console origin. From a bridge network use the public `https://gba-console-dev.85.17.167.167.nip.io`. The `__Host-` session cookies are `Secure`, so a plain-http origin works only as `http://localhost:8083` on the host itself.
- Credentials: mount `/private/qa-desk-verifier` read-only and set `QA_VERIFIER_CREDENTIALS`, or pass `QA_VERIFIER_LOGIN` and `QA_VERIFIER_PASSWORD`.

```bash
docker run --rm \
  -v /root/projects/gba-infra/tools/ui-verify:/src:ro \
  -v /private/qa-desk-verifier:/run/qa-verifier:ro \
  -e QA_VERIFIER_CREDENTIALS=/run/qa-verifier/credentials.private.json \
  -v /path/to/evidence:/evidence \
  node:22-bookworm bash -lc 'cp -r /src /work && cd /work && npm ci && npx playwright install --with-deps chromium \
    && node ui-verify.mjs examples/product-card-receipts-2018.json --out /evidence'
```

## Usage

```
node ui-verify.mjs <spec.json|-> [more specs...] [--base URL] [--env dev|qa] [--out DIR]
                   [--credentials FILE] [--timeout MS] [--trace] [--headed]
```

| Option | Default |
|---|---|
| `--base` | `UI_VERIFY_BASE_URL`, else `https://gba-console-dev.85.17.167.167.nip.io` |
| `--env` | the spec's `env`, else `dev`. `qa` sets the `gba_env=qa` cookie and fails unless `/gba-env.json` reports `active: qa`. |
| `--out` | `UI_VERIFY_OUT`, else `./evidence` |
| `--credentials` | `QA_VERIFIER_CREDENTIALS`, else `/private/qa-desk-verifier/credentials.private.json` |
| `--timeout` | 20000 ms per step and check (navigation gets 3×) |
| `--trace` | also save `trace.zip` (open it with `npx playwright show-trace`) |

`-` reads one spec from stdin. Several specs share one browser, and each one logs in with its own context.

## Spec format

```json
{
  "name": "product-card-receipts-2018",
  "description": "free text, copied into the result",
  "url": "/products?netId=47b16aa5-fc36-4b56-ae75-17b607f4afcf",
  "env": "dev",
  "viewport": { "width": 1600, "height": 1000 },
  "settleMs": 0,
  "timeout": 20000,
  "fullPage": true,
  "allowNonGet": ["/some/read-only/post$"],
  "steps": [
    { "click": { "role": "button", "name": "Прихід", "exact": true } },
    { "waitForResponse": { "urlIncludes": "/consignments/info/assortment/income/filtered", "status": 200 } }
  ],
  "checks": [
    { "type": "rowCount", "rowMatches": "\\d{2}\\.\\d{2}\\.2018", "min": 1, "description": "a 2018 receipt row" },
    { "type": "noConsoleErrors" }
  ]
}
```

- `name` (required): `[A-Za-z0-9._-]{1,80}`. It becomes the evidence folder name.
- `url` (required): a console path starting with `/`. The runner waits for `load`, then up to 15 s for network idle.
- `allowNonGet`: regexes matched against the URL pathname. They let extra read-only POSTs through the guard. Do not use them for writes.

### Locators

A locator is either a Playwright selector string (`"css=…"`, `"text=…"`, `"role=button[name=\"Прихід\"]"`) or an object:

| Key | Meaning |
|---|---|
| `role` + `name` (+ `exact`) | `getByRole` |
| `text` (+ `exact`) | `getByText` |
| `label`, `placeholder`, `testId` | `getByLabel`, `getByPlaceholder`, `getByTestId` |
| `css` | CSS selector |
| `within` | a parent locator to search inside |
| `hasText` | keep matches containing this text |
| `nth` | pick the n-th match (0-based) |

Steps act on the first match.

### Steps

Run in order. The first failing step stops the run, and the status becomes `fail`.

| Step | Value |
|---|---|
| `goto` | console path or absolute URL |
| `click`, `hover` | locator |
| `fill` | `{ "locator": …, "value": "KD02801" }` |
| `press` | `{ "locator": …, "key": "Enter" }` |
| `select` | `{ "locator": …, "value": "…" }` |
| `waitFor` | locator, or `{ "locator": …, "state": "visible|hidden|attached|detached", "timeout": ms }` |
| `waitForResponse` | `{ "urlIncludes": "…", "status": 200 }`. It also matches a response that arrived during the previous step. |
| `wait` | milliseconds |
| `screenshot` | file name (without `.png`) inside the evidence folder |

After every action the runner waits up to 5 s for network idle. Set `"settle": false` on a step to skip that wait.

### Checks

All checks are evaluated, even after one fails.

| `type` | Fields | Passes when |
|---|---|---|
| `visible` | `locator`, optional `enabled: true/false` | the first match becomes visible (and has that enabled state) |
| `absent` | `locator`, optional `waitMs` | no match is visible |
| `text` | `text`, optional `within`, `exact`, `present: false` | the text is (or is not) on the page or inside `within` |
| `count` | `locator`, `equals` / `min` / `max` | the match count fits |
| `rowCount` | optional `table` (default: the first visible `table`), `rowSelector` (default `tbody tr`), `rowHasText`, `rowMatches` (regex), `equals` / `min` / `max` | the row count fits. The first 5 matching rows are saved as `sample`. |
| `noConsoleErrors` | optional `ignore` (regexes) | no console `error` or page error. 404 resource errors and favicon errors are ignored by default. |
| `noFailedRequests` | optional `statuses` (default `[400, 599]`), `ignore` (regexes) | no same-origin API response in that status range |
| `noBlockedRequests` | none | the guard blocked no request |
| `response` | `urlIncludes`, optional `status` (default 200), `method` | at least one matching API response was seen, and all of them had that status |
| `url` | `matches` (regex) | the final URL matches |

Every check accepts `description`, which is shown in the summary and returned in `failedChecks`.

## Exit codes and output

| Exit | `status` | Meaning |
|---|---|---|
| 0 | `pass` | every step and check passed |
| 1 | `fail` | a step or a check failed (a real UI finding, or a wrong spec locator) |
| 2 | `error` | usage or spec error (bad JSON, missing fields, unknown step or check) |
| 3 | `error` | environment error: credentials unreadable, login not 200 (a backend restart gives 502 for a minute), browser launch failed, navigation failed, QA switch not active |

With several specs, the highest code wins.

stdout has one human-readable block per spec. The **last line** is always machine-readable:

```
UI_VERIFY_RESULT {"status":"pass","exitCode":0,"results":[{"spec":"…","status":"pass","exitCode":0,"resultFile":"…/result.json","screenshot":"…/screenshot.png","failedChecks":[],"error":null}]}
```

Evidence for each spec goes to `<out>/<spec name>/<UTC timestamp>/`:

- `result.json`: the spec name and description, `env`, `base`, `url`, `finalUrl`, `status`, `exitCode`, `steps[]` (ok, error, ms), `checks[]` (pass plus details such as `count`, `sample`, `found`, `errors`, `failed`, `seen`), `console[]` (errors and warnings), `responses[]` (every same-origin `/api/` response: status, method, path), `blockedRequests[]`, `activeEnv` (QA only), `startedAt`, `finishedAt`.
- `screenshot.png`: a full-page screenshot of the final state. It is taken on failure too.
- `<name>.png` for each `screenshot` step, and `trace.zip` with `--trace`.

## Calling it from the QA Desk worker

1. Write the spec for the bug (URL and the UI facts the fix should produce) to the job folder, for example `job/ui-verify/spec.json`.
2. Run it against DEV, and against QA when the fix was exercised there:

   ```bash
   node /root/projects/gba-infra/tools/ui-verify/ui-verify.mjs job/ui-verify/spec.json --out job/ui-verify/evidence [--env qa]
   ```

3. Parse the `UI_VERIFY_RESULT` line:
   - **0**: attach `screenshot` and `resultFile` to the task as evidence that the fix is verified.
   - **1**: read `failedChecks` and `result.json`.
     - If `responses` contain 403s, the screen needs a permission the verifier does not hold. Report that the verifier cannot see the screen; do not report a failed fix.
     - If `blockedRequests` is not empty, the flow needed a write. UI verification of writes is out of scope; verify those in QA by other means.
   - **2**: fix the spec.
   - **3**: retry after a minute, because DEV backends restart on deploy. If it persists, report an environment problem.
4. Never add credentials to specs or evidence. The runner reads them only from the credentials file or the environment.

Example specs in `examples/`:

- `product-card-receipts-2018.json`: the product card «Прихід» lists the 26.02.2018 receipt.
- `new-sale-wizard-gated.json`: «Новий продаж» is disabled for the verifier.
- `client-kd02801-agreements.json`: client KD02801 is found and its structure card shows 2 agreements.
