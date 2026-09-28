# Dashboard account evidence on a small VPS

The standard dashboard Dockerfile reinstalls every engine dependency and
needs several gigabytes of temporary disk space. This overlay pins the image
ID of the running dashboard, then adds two Python files. No package install
or Docker volume change occurs. Build only when the engine is stable and the
host has free space. The account evidence module opens SQLite read-only.

From `/root/trading-analysis-platform`, after fetching this branch:

```sh
base_image=$(docker inspect -f '{{.Image}}' trading-analysis-platform-dashboard-1)
docker image inspect "$base_image" >/dev/null
docker image tag "$base_image" trading-analysis-platform-dashboard:nexuss-pre-overlay
docker build --pull=false \
  --build-arg BASE_IMAGE=trading-analysis-platform-dashboard:nexuss-pre-overlay \
  -f services/nexuss_bridge/Dockerfile.dashboard-overlay \
  -t trading-analysis-platform-dashboard:nexuss-account-overlay .
docker compose -f docker-compose.yml \
  -f services/nexuss_bridge/dashboard-overlay.compose.yaml \
  up -d --no-deps --no-build dashboard
curl -fsS -o /dev/null -w 'Account evidence HTTP: %{http_code}\n' \
  http://127.0.0.1:8000/api/agent/account-evidence
```

If the dashboard does not become ready, roll back without rebuilding:

```sh
docker compose -f docker-compose.yml \
  -f services/nexuss_bridge/dashboard-rollback.compose.yaml \
  up -d --no-deps --no-build dashboard
```

The image tag `nexuss-pre-overlay` refers to the exact image ID of the
dashboard container before the change. This overlay is intentionally
specific to the account evidence route; a future full image upgrade still
needs a larger disk or a slimmer base image. The host maintenance executor
is deployed separately after the bridge is verified.
