#!/usr/bin/env bash
# External health monitoring for the dashboard's /api/health endpoint - see
# RUNBOOK.md section 4 for why this exists (the dashboard is deliberately
# bound to 127.0.0.1 only, so a real external watcher can't hit it
# directly; this is a tiny colocated process that can, since it runs on
# the same VPS).
#
# Intended to run via cron every few minutes:
#   */5 * * * * cd /path/to/trading-analysis-platform && ALERT_WEBHOOK_URL='...' scripts/healthcheck_ping.sh >> logs/healthcheck.log 2>&1
#
# ALERT_WEBHOOK_URL must be set in the cron entry's own environment (cron
# does not read .env) - the same Slack/Discord-compatible incoming webhook
# this system already uses for AlertManager, so a health-check page lands
# in the same place as every other alert.
set -u

if [ -z "${ALERT_WEBHOOK_URL:-}" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) healthcheck_ping: ALERT_WEBHOOK_URL not set - nothing to page to, exiting" >&2
    exit 1
fi

RESPONSE=$(curl -sf --max-time 10 http://localhost:8000/api/health) || {
    curl -sf -X POST "$ALERT_WEBHOOK_URL" -d '{"text":"trading-platform health endpoint unreachable - dashboard container may be down"}' >/dev/null
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) healthcheck_ping: health endpoint unreachable, alert sent"
    exit 1
}

LABEL=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('label', 'unknown'))" 2>/dev/null || echo "unparseable")

if [ "$LABEL" != "healthy" ]; then
    curl -sf -X POST "$ALERT_WEBHOOK_URL" -d "{\"text\":\"trading-platform health check reports: $LABEL\"}" >/dev/null
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) healthcheck_ping: label=$LABEL, alert sent"
else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) healthcheck_ping: healthy"
fi
