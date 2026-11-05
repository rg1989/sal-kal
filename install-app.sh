#!/bin/bash
# Install Sal (web UI + indexer) as systemd user services. Run on the server from this folder.
# Needs install-downloader.sh first (it fills ~/il-prices/dumps).
set -e
APP="$(cd "$(dirname "$0")" && pwd)"
cd "$APP"
# ponytail: uv is only needed here; the services run straight from .venv
if uv --version >/dev/null 2>&1; then UV="uv"
elif command -v mise >/dev/null; then UV="mise x uv -- uv"
else sudo pacman -S --needed --noconfirm uv; UV="uv"; fi
[ -d .venv ] || $UV venv -q --python 3.12 .venv
$UV pip install -q --python .venv/bin/python -r requirements.txt

U=~/.config/systemd/user
mkdir -p $U
cat > $U/sal-web.service <<EOF
[Unit]
Description=Sal basket prices web UI (port 8080)
After=network-online.target
[Service]
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/uvicorn app.server:app --host 0.0.0.0 --port 8080
Restart=on-failure
[Install]
WantedBy=default.target
EOF
cat > $U/sal-index.service <<EOF
[Unit]
Description=Index downloaded supermarket price files for Sal
[Service]
Type=oneshot
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python -m app.indexer
# then place new stores on the map (OpenStreetMap, 1 lookup/s, cached)
ExecStart=$APP/.venv/bin/python -m app.geo
# then recompute the deals shelves (real savings vs typical prices)
ExecStart=$APP/.venv/bin/python -m app.deals
Nice=10
TimeoutStartSec=3h
EOF
# ponytail: a 30-min timer instead of hooking il-prices.service — the indexer is incremental,
# picks up chains as they finish downloading, and still runs when one chain's download fails.
cat > $U/sal-index.timer <<'EOF'
[Unit]
Description=Index new price files every 30 minutes
[Timer]
OnCalendar=*:05,35
Persistent=true
[Install]
WantedBy=timers.target
EOF
# store catalogue photos (background removed): a polite batch every hour until all are in, then new products
cat > $U/sal-photos.service <<EOF
[Unit]
Description=Fetch store catalogue photos for Sal
[Service]
Type=oneshot
WorkingDirectory=$APP
ExecStart=$APP/.venv/bin/python -m app.images
Nice=10
EOF
cat > $U/sal-photos.timer <<'EOF'
[Unit]
Description=Fetch product photos, a gentle batch every hour
[Timer]
OnCalendar=*:50
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now sal-index.timer sal-photos.timer
systemctl --user enable sal-web.service
systemctl --user restart sal-web.service  # re-running this script deploys code changes
systemctl --user start --no-block sal-index.service sal-photos.service
echo "Sal is up: http://$(hostname):8080 (over Tailscale)"
