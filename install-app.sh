#!/bin/bash
# Deploy Sal (web app, indexer, photos) from this folder into /srv/sal and restart it. Uses sudo.
# Sal runs as its own system user in a sandbox (deploy/systemd/sal-.service.d/sandbox.conf) because
# this machine also runs other private home services. The downloader is installed by install-downloader.sh.
set -euo pipefail
cd "$(dirname "$0")"
# the old per-user services (before 2026-11-05) would hold port 8080
U=~/.config/systemd/user
if [ -e $U/sal-web.service ]; then
  systemctl --user disable -q --now sal-web.service sal-index.timer sal-photos.timer 2>/dev/null || true
  rm -f $U/sal-web.service $U/sal-index.service $U/sal-index.timer $U/sal-photos.service $U/sal-photos.timer
  systemctl --user daemon-reload
fi
PY_SRC="$(deploy/find-python.sh)"
sudo PY_SRC="$PY_SRC" deploy/install.sh app
echo "Sal is up: http://$(hostname):8080 (this network and Tailscale)"
