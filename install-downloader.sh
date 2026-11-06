#!/bin/bash
# Install the price-file downloader (OpenIsraeliSupermarkets/israeli-supermarket-scarpers, from PyPI pinned with
# hashes: deploy/downloader-requirements.txt) into /srv/sal/downloader and its 3-hourly timer (sal-download.timer),
# run by app/download.py with Sal's fixes (app/scraper_fixes.py). Uses sudo. Files land in /srv/sal/data/dumps;
# it runs in the same sandbox as the rest of Sal. Refuses while a download is running.
set -euo pipefail
cd "$(dirname "$0")"
# the old per-user downloader (before 2026-11-05)
U=~/.config/systemd/user
if [ -e $U/il-prices.service ]; then
  systemctl --user disable -q --now il-prices.timer 2>/dev/null || true
  systemctl --user stop il-prices.service 2>/dev/null || true
  rm -f $U/il-prices.service $U/il-prices.timer
  systemctl --user daemon-reload
fi
PY_SRC="$(deploy/find-python.sh)"
sudo PY_SRC="$PY_SRC" deploy/install.sh downloader
echo "Installed. Watch it with: journalctl -u sal-download -f"
