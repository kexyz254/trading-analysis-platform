# P6.32: approved TAS dashboard release

The release executor is a separate systemd service from dashboard maintenance.
It reads two fixed dashboard files from an exact Git commit already present in
the VPS repository. It compiles the source without executing it, uses a fixed
Dockerfile and an already installed local dashboard base image, and builds with
`--network=none --pull=false`. It replaces only the dashboard container. After
replacement it requires `/api/ready` and live BTC 5m/15m tracker evidence. On
failed verification it attempts to restore the pinned previous image and
reports the actual outcome. The host stores idempotent receipts; a reserved
command cannot be silently retried after an interruption.

The bridge can authenticate and forward preparation requests. Deployment also
requires a separate approval HMAC held by the owner's Windows Nexuss runtime
and the host release service; **the bridge container cannot read that key**.
The owner must approve the exact release ID and manifest digest in an
authenticated Nexuss chat. Both CI and the host's pinned base/source identity
are rechecked before applying. Neither the engine nor its breaker is touched.

## Provision and install

After the exact source branch's CI passes, update the VPS source and bridge
worktree by fast-forward/fetch. Keep local `config.yaml` and
`docker-compose.yml` edits. In Nexuss PowerShell, run
`scripts/provision_tas_release_key.ps1`; it uses `scp` to install the private
release key under `/etc/nexuss-release/approval.key` without printing it.
The bridge key remains at `/etc/nexuss-bridge/bridge.key`, separate from the
approval key. Compare key fingerprints privately if transfers fail; never
paste key material in a chat or repository.

Then on the VPS:

```sh
set -e
test -s /etc/nexuss-release/approval.key
install -d -m 0755 /opt/nexuss-agent
install -m 0644 /root/nexuss-bridge-p617/services/nexuss_bridge/release_ops.py /opt/nexuss-agent/release_ops.py
install -m 0644 /root/nexuss-bridge-p617/services/nexuss_bridge/nexuss-release.service /etc/systemd/system/nexuss-release.service
systemd-analyze verify /etc/systemd/system/nexuss-release.service
systemctl daemon-reload
systemctl enable --now nexuss-release.service
test -S /run/nexuss-release/release.sock
docker compose -p nexuss-supervisor \
  -f /root/nexuss-bridge-p617/services/nexuss_bridge/compose.yaml \
  -f /root/nexuss-bridge-p617/services/nexuss_bridge/host-ops.compose.yaml \
  -f /root/nexuss-bridge-p617/services/nexuss_bridge/release.compose.yaml \
  up -d --no-deps --build nexuss-bridge
```

Nexuss Windows must use the branch containing P6.32 and restart before chat
can prepare a release. A reviewed release commit must be present in the VPS
repository's local Git objects. Preparation requires exact-success TAS Python
Tests, Docker Build, Fuzz Build Smoke Test and P6.31 tracker workflow runs.

In Nexuss chat:

```text
Prepare TAS dashboard release from commit <40-hex-commit>
```

Nexuss responds with a release ID, current image, file hashes and manifest
digest. Review the linked commit and typed source changes. The exact approval
phrase that Nexuss displays triggers the signed build and replacement. A
successful receipt confirms both readiness and the two live BTC horizons. A
failed or ambiguous receipt blocks follow-up deployments until reviewed.

This deploys a reviewed commit; creating arbitrary patches and publishing PRs
from Nexuss Engineering is a separate future capability.
