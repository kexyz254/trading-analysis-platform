#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${SOURCE_DIR:-$(pwd)}"
APP_DIR="${APP_DIR:-/opt/trading-analysis-platform}"
DOMAIN="${DOMAIN:-_}"
ATS_LOCK="${ATS_LOCK:-$SOURCE_DIR/deploy/vps/locks/requirements-ats.lock}"
UMIL_LOCK="${UMIL_LOCK:-$SOURCE_DIR/deploy/vps/locks/requirements-umil.lock}"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Run as root: sudo SOURCE_DIR=$SOURCE_DIR DOMAIN=$DOMAIN $0" >&2
    exit 1
fi
for file in "$ATS_LOCK" "$UMIL_LOCK"; do
    [[ -s "$file" ]] || { echo "Missing pinned dependency lock: $file" >&2; exit 1; }
done

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3 python3-venv python3-pip nginx curl rsync ca-certificates

id atsapp >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin atsapp
install -d -o atsapp -g atsapp -m 0750 "$APP_DIR" /var/lib/ats /var/lib/umil /var/log/ats-umil /var/backups/ats-umil
install -d -o root -g atsapp -m 0750 /etc/ats

RELEASE_BACKUP="/var/backups/ats-umil/release-$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -d "$APP_DIR/services" ]]; then
    install -d -m 0750 "$RELEASE_BACKUP"
    rsync -a --delete-excluded \
        --exclude '.venv-ats/' --exclude '.venv-umil/' \
        --exclude 'services/umil/var/' --exclude '*.db' \
        "$APP_DIR/" "$RELEASE_BACKUP/"
fi

rsync -a --delete \
    --exclude '.git/' --exclude '.venv*/' --exclude 'venv/' \
    --exclude 'node_modules/' --exclude '__pycache__/' --exclude '*.pyc' \
    --exclude '*.db' --exclude '*.sqlite*' --exclude '*.log' \
    "$SOURCE_DIR/" "$APP_DIR/"

python3 -m venv "$APP_DIR/.venv-ats"
python3 -m venv "$APP_DIR/.venv-umil"
"$APP_DIR/.venv-ats/bin/python" -m pip install --upgrade pip wheel
"$APP_DIR/.venv-umil/bin/python" -m pip install --upgrade pip wheel
"$APP_DIR/.venv-ats/bin/pip" install --requirement "$ATS_LOCK"
"$APP_DIR/.venv-umil/bin/pip" install --requirement "$UMIL_LOCK"

cat > /etc/ats/umil.env <<ENV
PYTHONPATH=$APP_DIR
PYTHONUNBUFFERED=1
UMIL_DATABASE_PATH=/var/lib/umil/umil.db
UMIL_BIND_HOST=127.0.0.1
UMIL_BIND_PORT=8100
UMIL_AUTO_START=true
UMIL_ENFORCE_SECRET_FREE_ENV=true
UMIL_EVIDENCE_VERIFICATION_INTERVAL_SECONDS=30
UMIL_EVIDENCE_FULL_VERIFICATION_SECONDS=3600
UMIL_EVIDENCE_VERIFICATION_BATCH_SIZE=64
ENV

if [[ ! -f /etc/ats/ats.env ]]; then
cat > /etc/ats/ats.env <<ENV
PYTHONPATH=$APP_DIR
PYTHONUNBUFFERED=1
ATS_UMIL_BASE_URL=http://127.0.0.1:8100
ENV
fi
chown root:atsapp /etc/ats/ats.env /etc/ats/umil.env
chmod 0640 /etc/ats/ats.env /etc/ats/umil.env

install -m 0644 "$APP_DIR/deploy/vps/systemd/umil.service" /etc/systemd/system/umil.service
install -m 0644 "$APP_DIR/deploy/vps/systemd/ats.service" /etc/systemd/system/ats.service
install -m 0644 "$APP_DIR/deploy/vps/systemd/ats-umil-healthcheck.service" /etc/systemd/system/ats-umil-healthcheck.service
install -m 0644 "$APP_DIR/deploy/vps/systemd/ats-umil-healthcheck.timer" /etc/systemd/system/ats-umil-healthcheck.timer
install -m 0644 "$APP_DIR/deploy/vps/systemd/umil-backup.service" /etc/systemd/system/umil-backup.service
install -m 0644 "$APP_DIR/deploy/vps/systemd/umil-backup.timer" /etc/systemd/system/umil-backup.timer
install -m 0755 "$APP_DIR/deploy/vps/scripts/ats-umil-healthcheck" /usr/local/sbin/ats-umil-healthcheck
install -m 0755 "$APP_DIR/deploy/vps/scripts/umil-backup" /usr/local/sbin/umil-backup

sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/vps/nginx/ats.conf.template" > /etc/nginx/sites-available/ats
ln -sfn /etc/nginx/sites-available/ats /etc/nginx/sites-enabled/ats
rm -f /etc/nginx/sites-enabled/default
nginx -t

chown -R atsapp:atsapp "$APP_DIR" /var/lib/ats /var/lib/umil /var/log/ats-umil /var/backups/ats-umil

cd "$APP_DIR"
"$APP_DIR/.venv-umil/bin/python" -m py_compile \
    services/umil/app/anomaly_calibration.py \
    services/umil/app/service.py services/umil/app/versions.py
"$APP_DIR/.venv-umil/bin/python" -m unittest discover -s services/umil/tests -p 'test_*.py'
"$APP_DIR/.venv-umil/bin/python" -m unittest tests.test_umil_gateway
"$APP_DIR/.venv-ats/bin/python" -c 'import pandas, uvicorn, webapp.server'

systemctl daemon-reload
systemctl enable nginx umil.service ats.service ats-umil-healthcheck.timer umil-backup.timer
systemctl restart nginx
systemctl restart umil.service

for _ in $(seq 1 90); do
    curl --fail --silent --max-time 5 http://127.0.0.1:8100/health/ready >/dev/null && break
    sleep 1
done
curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8100/health/ready >/dev/null
systemctl restart ats.service
for _ in $(seq 1 120); do
    curl --fail --silent --max-time 5 http://127.0.0.1:8000/api/pro/umil/health >/dev/null && break
    sleep 1
done
curl --fail --silent --show-error --max-time 15 http://127.0.0.1:8000/api/pro/umil/health >/dev/null
systemctl start ats-umil-healthcheck.timer umil-backup.timer

VERSION=$("$APP_DIR/.venv-umil/bin/python" -c 'from services.umil.app.versions import VERSIONS; print(VERSIONS.service_version)')
echo "ATS-UMIL deployment healthy"
echo "Version: $VERSION"
echo "Local ATS: http://127.0.0.1:8000"
echo "Public host: http://$DOMAIN"
echo "Release backup: $RELEASE_BACKUP"
echo "Add ATS credentials only to /etc/ats/ats.env. Never add them to /etc/ats/umil.env."
