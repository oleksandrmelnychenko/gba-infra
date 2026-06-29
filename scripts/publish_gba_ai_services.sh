#!/usr/bin/env bash
# Assemble the gba-ai-services monorepo from the 3 clean services and push to GitHub.
# Excludes bi-server-concord (prototype with prod creds — do NOT publish to a public repo).
# Run:  bash /root/projects/publish_gba_ai_services.sh
set -euo pipefail

ROOT=/root/projects
DEST="$ROOT/gba-ai-services"
REMOTE="git@github.com:oleksandrmelnychenko/gba-ai-services.git"
SERVICES=(gba-nba gba-reco gba-procure gba-solvency gba-pricing gba-products gba-forecast)

echo ">> safety: refuse to include any prod creds"
if grep -rIl -E "Grimm_jow92|78\.152\.175\.67|ef_migrator|_dev_internal_[0-9a-f]|Ro_2026_dev|NbaRo_2026|Nba_dev_2026" "${SERVICES[@]/#/$ROOT/}" \
     --exclude-dir=.venv --exclude-dir=.git --exclude='.env' --exclude='*.env' --exclude-dir=backup_* 2>/dev/null | grep .; then
  echo "ABORT: credential string found in a service to be published"; exit 1
fi

echo ">> assembling $DEST"
rm -rf "$DEST"; mkdir -p "$DEST"
for d in "${SERVICES[@]}"; do
  rsync -a \
    --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='.ruff_cache' \
    --exclude='.pytest_cache' --exclude='.env' --exclude='*.egg-info' --exclude='*.pyc' \
    "$ROOT/$d/" "$DEST/$d/"
done

cat > "$DEST/.gitignore" <<'EOF'
.venv/
__pycache__/
*.pyc
.env
.ruff_cache/
.pytest_cache/
*.egg-info/
.DS_Store
node_modules/
EOF

cat > "$DEST/README.md" <<'EOF'
# GBA AI Services

AI/ML microservices for the GBA (Concord) ecosystem. Each is a self-contained FastAPI service
(Python 3.12, Pydantic v2, read-only SQLAlchemy over ConcordDb_V5, env-only secrets, Docker).

## Services
- **gba-nba** — AI Sales Cockpit / Next-Best-Action engine. A prioritized daily task queue per sales
  manager (debt follow-up, reorder, churn win-back, cross-sell), stateful in MongoDB, with a run-rate
  sales-target engine, a daily scheduler (09:00 Europe/Kyiv), and a head-of-sales dashboard.
- **gba-reco** — client product recommendations (V3.2 hybrid: repurchase + co-purchase discovery),
  with an offline leave-last-basket eval harness and Redis caching.
- **gba-procure** — per-producer procurement / reorder-point purchase plans.
- **gba-solvency** — supervised credit-risk scoring (WOE scorecard + GBM challenger, SEV180 label) with a
  6-month forward early-warning, calibrated PD bands, drift monitoring and a gated retrain harness.
- **gba-pricing** — per-product price/discount recommendations from peer/segment price bands.
- **gba-products** — per-SKU assortment & inventory-health intelligence (lifecycle, ABC/XYZ, margin,
  returns, dead-stock, regional demand lens).
- **gba-forecast** — per-client/product sales demand forecasting (rolling-origin backtest, per-segment
  method selection: EWMA / SBA / moving-average).

Each service has its own README, pyproject.toml, Dockerfile, app/, tests/, docs/.
Secrets come from the environment only (see each service's `.env.example`); never commit `.env`.

## Integration
Orchestrated by gba-server (.NET), which proxies these services, injects the authenticated user from
the session, and surfaces them in the GBA Console (React).
EOF

echo ">> git init + commit + push"
cd "$DEST"
git init -b main -q
git config user.name "Oleksandr Melnychenko"
git config user.email "oleksandr.melnychenko23@gmail.com"
git add -A
git commit -q -m "Publish gba AI services: nba, reco, procure, solvency, pricing, products, forecast

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git remote add origin "$REMOTE"
git push -u origin main --force

echo ">> done. pushed $(git rev-list --count HEAD) commit to $REMOTE"
