# QA NBA isolation

QA serves the existing `ConcordDb_V5_QA` SQL database and a separate, initially empty `gba_nba_qa` Mongo namespace on port 8012. DEV retains port 8002 and its current task state. There is no database copy, full sync, task backfill or QA scheduler/retraining service.

QA generation and feedback are disabled because downstream recommendation services are not isolated. The API source response reports this explicitly; an empty task namespace does not pass generation readiness. Its sale-activity watermark is not proof of a completed source sync.

Roll out in this order after merging the NBA, server and console changes:

1. Run `scripts/qa/prepare-nba-qa.py` with the NBA virtual environment. This stages mode-0600 files under `/private/gba-qa/nba` and changes no runtime. Verify the existing read-only SQL login can read QA; use the staged grant script only if the QA database user is absent.
2. Install `scripts/qa/gba-nba-dev-environment.conf` as `/etc/systemd/system/gba-nba.service.d/environment.conf`, and `scripts/qa/gba-nba-qa.service` as `/etc/systemd/system/gba-nba-qa.service`. Reload systemd, restart the DEV API service and start the QA API service. Do not enable a QA worker or timer.
3. Run `verify-nba-isolation.py` using the NBA virtual environment. It checks actual SQL databases/read-only permissions, distinct web-process PIDs for module-level caches/locks, served provenance, and rejection of wrong-environment mutations before JSON parsing. It creates a unique `__isolation_probe_<uuid>` collection in each Mongo namespace, writes different fixtures with the same key, checks an independent QA update, and drops both collections. Actual tasks/contacts are untouched. QA requires `X-GBA-Source-Environment: qa`; DEV permits legacy requests without this header during the transition. QA generation readiness remains false.
4. Run `scripts/qa/route-nba-qa.py` to update private compose files, then deploy the new API image to DEV/QA. It sends the expected environment header on every NBA request. Deploy the console source-status component after the API routes are available.
5. Verify the console DEV/QA switch, factual source labels, independent task/contact namespaces and failure behavior. Use isolated fixtures for mutation tests; do not create test business records in these environments.

The QA compose generator keeps the isolated URL and separate key on refresh, refusing to silently inherit the DEV endpoint. Conversion flags `GBA_LIBREOFFICE_POOL_ENABLED` and `GBA_DOCUMENT_CONVERSION_TIMINGS` preserve explicit process environment, then the existing QA configuration, then the DEV template; their initial default is `false`.

The preparation and routing scripts never restart services, copy database records, generate tasks or alter PROD. NBA caches and locks are process-local; separate services isolate them in addition to SQL/Mongo namespaces. The shared code and model artifacts are read-only serving inputs, and QA does not write training caches or model artifacts.

The console badge is scoped to clients and the work queue. Product recommendations use another service and are explicitly not covered by this provenance check. Isolating NBA does not establish independent QA recommendation/financial model data.

Staged commands for the deployment owner, from the merged `gba-infra` checkout:

```sh
rtk proxy /root/projects/gba-nba/.venv/bin/python scripts/qa/prepare-nba-qa.py
rtk proxy install -d /etc/systemd/system/gba-nba.service.d
rtk proxy install -m 0644 scripts/qa/gba-nba-dev-environment.conf /etc/systemd/system/gba-nba.service.d/environment.conf
rtk proxy install -m 0644 scripts/qa/gba-nba-qa.service /etc/systemd/system/gba-nba-qa.service
rtk proxy systemctl daemon-reload
rtk proxy systemctl restart gba-nba.service
rtk proxy systemctl enable --now gba-nba-qa.service
rtk proxy /root/projects/gba-nba/.venv/bin/python scripts/qa/verify-nba-isolation.py --output /private/qa-nba-isolation-20261022/runtime-acceptance.json
rtk proxy python3 scripts/qa/route-nba-qa.py
```

Only reroute after verification passes. Then deploy the new server image (with expected-environment header support) to DEV/QA using the existing release process. The legacy API remains on DEV port 8002 until then; do not point a legacy image at QA, which requires the new header. Deploy the console last.
