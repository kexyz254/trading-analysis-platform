# P6.29: live dashboard maintenance

The dashboard can be diagnosed and restarted through Nexuss. The root host
executor accepts exactly `diagnostics` and `restart_dashboard` over a Unix
socket. The bridge signs the fixed request and keeps durable idempotency
receipts. The executor checks `/api/ready` twice before restarting the fixed
dashboard container and checks readiness again afterward. The engine,
circuit breaker, trading API and strategy are outside this capability.

Installation on the TAS VPS, after merging this branch into the deployed
bridge checkout and verifying the signed account evidence endpoint:

```sh
install -d -m 0755 /opt/nexuss-agent
install -m 0644 /root/nexuss-bridge-p617/services/nexuss_bridge/host_ops.py /opt/nexuss-agent/host_ops.py
install -m 0644 /root/nexuss-bridge-p617/services/nexuss_bridge/nexuss-host-ops.service /etc/systemd/system/nexuss-host-ops.service
systemctl daemon-reload
systemctl enable --now nexuss-host-ops.service
systemctl status --no-pager nexuss-host-ops.service
docker compose -p nexuss-supervisor -f /root/nexuss-bridge-p617/services/nexuss_bridge/compose.yaml -f /root/nexuss-bridge-p617/services/nexuss_bridge/host-ops.compose.yaml up -d --no-deps --build nexuss-bridge
```

The existing bridge key remains in `/etc/nexuss-bridge/bridge.key`; do not
copy it into Git or terminal output. The socket directory is host owned,
and only UID/GID 65532 may connect to the socket. The bridge container has
no Docker socket or host filesystem write mount. Do not deploy the compose
override before the systemd unit has created the Unix socket.

In Nexuss chat, `Diagnose TAS dashboard` reads readiness and records a
receipt; `Restore TAS dashboard` triggers a conditional recovery. When ready,
the recovery is a no-op. If recovery fails, the receipt reports the failure
and never claims TAS is healthy. This is the initial bounded live write
capability; source editing, testing and deployment need separate adapters.
