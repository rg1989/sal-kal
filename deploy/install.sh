#!/bin/bash
# Root half of install-app.sh and install-downloader.sh. Idempotent.
#   sudo PY_SRC=<dir of a Python 3.12 install> deploy/install.sh app|downloader
# Puts Sal under its own system user in /srv/sal and installs the sandboxed systemd units:
#   /srv/sal/python      Python 3.12 (the downloader needs < 3.13), root-owned
#   /srv/sal/code, venv  the app (copied from this repo), root-owned, read-only to Sal
#   /srv/sal/downloader  the price-file downloader (src + venv), root-owned
#   /srv/sal/ms-playwright  Chromium for the chains that need a browser
#   /srv/sal/data        the only writable place: databases, photos, price files (sal:sal 0750)
set -euo pipefail
MODE=${1:?usage: install.sh app|downloader}
REPO="$(cd "$(dirname "$0")/.." && pwd)"
S=/srv/sal
[ "$EUID" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

# Accounts: sal runs Sal; members of salsock may use its socket; cloudflared runs the tunnel.
getent group salsock >/dev/null || groupadd --system salsock
id sal >/dev/null 2>&1 || useradd --system --user-group --home-dir $S/data --no-create-home --shell /usr/bin/nologin sal
id cloudflared >/dev/null 2>&1 || useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/bin/nologin cloudflared
usermod -aG salsock cloudflared
# you may read Sal's data (group sal); Sal can never read yours
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then usermod -aG sal "$SUDO_USER"; fi

# Its own btrfs subvolume, so the system's snapshots don't hold on to the churning price files.
if [ ! -d $S ]; then
  if [ "$(stat -f -c %T /srv)" = btrfs ]; then btrfs subvolume create $S >/dev/null; else mkdir $S; fi
fi
chmod 0755 $S
install -d -o sal -g sal -m 0750 $S/data $S/data/dumps $S/data/photos
if [ ! -x $S/python/bin/python3.12 ]; then
  [ -x "${PY_SRC:-}/bin/python3.12" ] || { echo "PY_SRC must be a Python 3.12 install (see deploy/find-python.sh)" >&2; exit 1; }
  cp -a "$PY_SRC" $S/python
  chown -R root:root $S/python
fi

# Units, and the sandbox every sal-*.service gets.
install -m 0644 "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.socket "$REPO"/deploy/systemd/*.timer /etc/systemd/system/
install -d /etc/systemd/system/sal-.service.d
install -m 0644 "$REPO"/deploy/systemd/sal-.service.d/sandbox.conf /etc/systemd/system/sal-.service.d/
install -d $S/code
install -m 0644 "$REPO"/deploy/sandbox-check.py $S/code/
ip -o addr show | awk '{print $4}' | cut -d/ -f1 > $S/code/check-targets.txt  # every address this machine has
systemctl daemon-reload

case "$MODE" in
app)
  rsync -a --delete --exclude __pycache__ "$REPO"/app/ $S/code/app/
  install -m 0644 "$REPO"/requirements.txt $S/code/
  [ -x $S/venv/bin/python ] || $S/python/bin/python3.12 -m venv $S/venv
  $S/venv/bin/pip install -q --disable-pip-version-check -r $S/code/requirements.txt
  $S/venv/bin/python -m compileall -q $S/code/app
  chown -R root:root $S/code $S/venv
  systemctl enable -q --now sal-web.socket sal-lan.socket sal-index.timer sal-photos.timer
  systemctl enable -q sal-web.service
  systemctl restart sal-web.service  # re-running this deploys code changes
  ;;
downloader)
  install -d $S/downloader
  [ -d $S/downloader/src ] || git clone -q --depth 1 https://github.com/OpenIsraeliSupermarkets/israeli-supermarket-scarpers $S/downloader/src
  [ -x $S/downloader/venv/bin/python ] || $S/python/bin/python3.12 -m venv $S/downloader/venv
  $S/downloader/venv/bin/pip install -q --disable-pip-version-check $S/downloader/src
  PLAYWRIGHT_BROWSERS_PATH=$S/ms-playwright $S/downloader/venv/bin/python -m playwright install chromium
  chown -R root:root $S/downloader $S/ms-playwright
  systemctl enable -q --now sal-download.timer
  ;;
*) echo "usage: install.sh app|downloader" >&2; exit 1 ;;
esac
