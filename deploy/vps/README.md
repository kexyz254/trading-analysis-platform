# ATS–UMIL v1.6.2 VPS deployment

Target: Ubuntu/Debian VPS with systemd and Nginx.

## Production properties

- ATS and UMIL use separate pinned Python environments.
- UMIL binds only to `127.0.0.1:8100` and receives no trading credentials.
- ATS binds only to `127.0.0.1:8000`; Nginx is the public entry point.
- Both services restart automatically.
- A one-minute health timer restarts an unhealthy service.
- The UMIL SQLite evidence database is backed up consistently every day and retained for 14 days.
- Both Uvicorn services use one worker because their background state and SQLite projections are process-local.

## Deploy

Create a release on the Windows development machine using `BUILD_VPS_RELEASE.ps1`, upload and extract it on the VPS, then run:

```bash
cd /path/to/extracted/release
sudo SOURCE_DIR="$PWD" DOMAIN="analytics.example.com" \
  ./deploy/vps/scripts/install-or-upgrade.sh
```

Before public use, put ATS-only secrets in `/etc/ats/ats.env`, then restart ATS:

```bash
sudoedit /etc/ats/ats.env
sudo systemctl restart ats.service
```

Never put exchange credentials in `/etc/ats/umil.env`; UMIL will refuse to start when trading-secret variables are exposed.

For TLS after DNS points to the VPS:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d analytics.example.com
```

Operational checks:

```bash
systemctl status umil.service ats.service --no-pager
systemctl list-timers 'ats-umil-*' 'umil-backup*'
curl -fsS http://127.0.0.1:8100/health/ready
curl -fsS http://127.0.0.1:8000/api/pro/umil/health
journalctl -u umil.service -u ats.service -n 200 --no-pager
```
