# DEV console payment drawer — 2026-09-10

Deployed console only using the existing running Compose file chain plus `dev-console-payments-20260910.compose.yml`, with `up -d --no-deps --no-build gba-console`.

- Remote change: `24a94e939a9967b4e89f28832b91e23f91cbb5bb`.
- Frozen merged source: `1928dcdb8152d353a9304b22827f950a0e906615`.
- Build: `2026.09.10.1516`.
- Image: `gba-console:payments-1928dcdb8-20260910-r2`.
- Image ID: `sha256:b2447adbbbc25d7ad77fc029cb3ac4193bf86047d9128fdc1bbd51f1ce1aaa5a`.

Verified healthy, 29 targeted tests passing, React Doctor diff 100/100, public/local static assets matching frozen hashes, and read-only payment detail drawer smoke test. All 52 non-target pre-existing containers remained unchanged; API, analytics, database and PROD were not redeployed.

The first packaging attempt caused HTTP 403 for static assets and was rolled back. The final image explicitly sets public static directories to 0755 and files to 0644 and passed an isolated HTTP asset probe before deployment. Prior image retained: `gba-console:redeploy-4b23e03f2-20260910`.

Concurrent local constructor-draft commit `b525aa127cad2660db40a298bb73239811db1004` is not included. No git push performed.

Full evidence and guarded rollback helper: `/root/evidence/dev-console-payments-20260910/REPORT.md`.
