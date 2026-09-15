# P6.17 supervisory gateway — connection milestone

This is a limited, opt-in first milestone, not full autonomous management.
It exposes projected health/status/decision data and durable advisory feedback.
No engine files, production configuration, exchange keys, Docker socket, execution
endpoints, strategy parameters or breaker controls are mounted or callable.
Existing engine and dashboard containers do not need restarting.

## Protocol

All requests require HMAC-SHA256 over UTF-8 `v1\nMETHOD\nPATH_AND_QUERY\nTIMESTAMP\nNONCE\nBODY_SHA256`.
Headers: X-Nexuss-Time (Unix seconds), X-Nexuss-Nonce (32 lowercase hex),
X-Nexuss-Signature (hex HMAC). Timestamp tolerance is 60 seconds. Replay records
survive restart. Use a random 32-byte-or-longer secret supplied through a private
file. Never put it in source control or chat. HTTP is for loopback only here;
remote access requires a separately configured private tunnel or HTTPS proxy.

GET /agent/v1/capabilities
GET /agent/v1/evidence/health
GET /agent/v1/evidence/status
GET /agent/v1/evidence/decisions?symbol=BTC%2FUSDT
POST /agent/v1/feedback
GET /agent/v1/feedback/{feedback_id}

Feedback fields: feedback_id, kind (research/incident/repair_proposal/evaluation),
summary, sources (HTTPS URLs without credentials or queries), observed_at,
expires_at, execution_requested=false. Times require timezone offsets. IDs are
idempotent; conflicting reuse returns 409. Feedback is stored only, never executed.
Do not submit credentials in feedback text. Data field projection deliberately
omits raw logs and reason text; this is not a universal secret scrubber.

## Deployment, after review

Use a separate checkout of the reviewed bridge branch. Do not pull over the VPS's
modified config.yaml/docker-compose.yml. From that separate checkout:

```bash
sudo install -d -m 0700 -o 65532 -g 65532 /etc/nexuss-bridge /var/lib/nexuss-bridge
sudo bash -c 'set -e; umask 077; if [ ! -e /etc/nexuss-bridge/bridge.key ]; then openssl rand -hex 32 > /etc/nexuss-bridge/bridge.key; fi; chown 65532:65532 /etc/nexuss-bridge/bridge.key'
docker compose -p nexuss-supervisor -f services/nexuss_bridge/compose.yaml up -d --build
```

The gateway binds only 127.0.0.1:8300. Transfer the key privately to the Nexuss
machine using your existing authenticated SSH connection. For an initial session,
use `ssh -N -L 8300:127.0.0.1:8300 root@167.233.158.160` from that machine.
Configure NEXUSS_TAS_BRIDGE_URL=http://127.0.0.1:8300 and
NEXUSS_TAS_SECRET_FILE to the private local key path before restarting Nexuss.
This SSH tunnel is session-bound, not an always-on solution.

Verify with Nexuss GET /v1/trading/evidence/health from local control.
HTTP success is separate from TAS health: a tripped breaker remains critical.
Rollback: `docker compose -p nexuss-supervisor -f services/nexuss_bridge/compose.yaml down`.
Retain /var/lib/nexuss-bridge and its replay/feedback database; do not delete volumes.

## Outstanding before the complete requested system

- Transactional decision outbox and event push/replay; current reads are on demand.
- Live news/economic/research providers, provenance, freshness and evaluation.
- Capability registry, conversation routing and dashboard integration in Nexuss.
- Automated diagnosis, isolated patch/test workflow and governed maintenance.
- Approved strategy activation and outcome comparison; feedback alone is not improvement.
- Always-on Nexuss worker and production private transport provisioning.
- Review of server-specific configuration and live deployment verification.

No profitability or accuracy gain is claimed. Tests use fake TAS responses and
temporary SQLite files; no exchange or production database is contacted.
