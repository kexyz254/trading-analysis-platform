# BTC price-call tracker deployment

This adds a read-only endpoint to the existing dashboard and signed bridge. It
does not alter the price-call engine, orders, breaker, or strategy. Use this
procedure only after PR #13 tests pass. Keep the existing runtime config and
database mounts. Do not run a full dashboard image rebuild on the 38GB VPS.

Run on the VPS as the operator who owns the existing Docker Compose project:

```bash
set -e
cd /root/trading-analysis-platform
git fetch origin feature/p631-btc-price-call-results
git merge --ff-only origin/feature/p631-btc-price-call-results
df -h /
docker ps --filter name=trading-analysis-platform-dashboard-1
docker inspect -f '{{.Image}}' trading-analysis-platform-dashboard-1
```

Verify the dashboard is healthy, disk has adequate headroom, and the active
dashboard image is the account overlay before proceeding. Pin that exact image
for rollback, then build the thin overlay:

```bash
set -e
cd /root/trading-analysis-platform
base_image="$(docker inspect -f '{{.Image}}' trading-analysis-platform-dashboard-1)"
docker image tag "$base_image" trading-analysis-platform-dashboard:nexuss-tracker-pre-overlay
docker build -f services/nexuss_bridge/Dockerfile.price-results-overlay \
  --build-arg "BASE_IMAGE=$base_image" \
  -t trading-analysis-platform-dashboard:nexuss-price-results .
docker compose -f docker-compose.yml \
  -f services/nexuss_bridge/dashboard-price-results.compose.yaml \
  up -d --no-deps --no-build dashboard
```

After the dashboard becomes healthy, confirm that the ledger is present and
the endpoint reports `status: available` with horizons `5m` and `15m`:

```bash
docker ps --filter name=trading-analysis-platform-dashboard-1
curl --fail --silent --show-error http://127.0.0.1:8000/api/agent/btc-price-call-results \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["symbol"], list(d["horizons"]))'
```

Update the separate bridge worktree to the same commit. Rebuild only the
lightweight bridge; include the existing host operations override so the
maintenance socket remains available:

```bash
set -e
git -C /root/nexuss-bridge-p617 fetch origin feature/p631-btc-price-call-results
git -C /root/nexuss-bridge-p617 switch --detach origin/feature/p631-btc-price-call-results
docker compose -p nexuss-supervisor \
  -f /root/nexuss-bridge-p617/services/nexuss_bridge/compose.yaml \
  -f /root/nexuss-bridge-p617/services/nexuss_bridge/host-ops.compose.yaml \
  up -d --no-deps --build nexuss-bridge
```

If the dashboard fails readiness, restore the pinned image:

```bash
cd /root/trading-analysis-platform
docker compose -f docker-compose.yml \
  -f services/nexuss_bridge/dashboard-price-results-rollback.compose.yaml \
  up -d --no-deps --no-build dashboard
```

This procedure is an operator deployment. Nexuss currently has signed
dashboard diagnostics and conditional restart only; it cannot execute this
build/deploy workflow or authorize its own code changes yet.
