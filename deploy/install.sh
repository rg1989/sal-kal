#!/bin/bash
# Root half of install-app.sh, install-downloader.sh and install-relay.sh. Idempotent.
#   sudo PY_SRC=<dir of a Python 3.12 install> deploy/install.sh app|downloader|relay
# Puts Sal under its own system user in /srv/sal and installs the sandboxed systemd units:
#   /srv/sal/python      Python 3.12 (the downloader needs < 3.13), root-owned
#   /srv/sal/code, venv  the app (copied from this repo), root-owned, read-only to Sal
#   /srv/sal/downloader  the price-file downloader (venv, from PyPI with hashes), root-owned; run by app/download.py
#   /srv/sal/data        the only writable place: databases, photos, price files (sal:sal 0750)
set -euo pipefail
MODE=${1:?usage: install.sh app|downloader|relay}
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
install -m 0644 "$REPO"/deploy/sandbox-check.py "$REPO"/deploy/trim-status.py $S/code/
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
  systemctl enable -q --now sal-web.socket sal-lan.socket sal-index.timer sal-photos.timer sal-backfill.timer
  systemctl enable -q sal-web.service
  systemctl restart sal-web.service  # re-running this deploys code changes
  ;;
downloader|relay)
  # never swap the venv under a running download (a oneshot is "activating" while it runs, which is-active calls inactive)
  for u in sal-download.service sal-relay.service; do
    case "$(systemctl show -p ActiveState --value $u)" in
      activating|active|deactivating|reloading) echo "$u is running: run this again when it ends" >&2; exit 1 ;;
    esac
  done
  # its runner (python -m app.download) and Sal's two fixes to it; the app's deploy copies all of app/ anyway
  install -d $S/code/app
  install -m 0644 "$REPO"/app/__init__.py "$REPO"/app/download.py "$REPO"/app/scraper_fixes.py $S/code/app/
  install -m 0644 "$REPO"/deploy/downloader-requirements.txt $S/code/
  # il-supermarket-scraper from PyPI, every package at an exact version with its sha256 (deploy/lock-downloader.py),
  # built beside the old venv and swapped in only once it installed (venv/bin/python is only ever run with -m)
  install -d $S/downloader
  rm -rf $S/downloader/venv.new
  $S/python/bin/python3.12 -m venv $S/downloader/venv.new
  $S/downloader/venv.new/bin/python -m pip install -q --disable-pip-version-check --require-hashes \
    -r "$REPO"/deploy/downloader-requirements.txt
  rm -rf $S/downloader/venv.old
  [ ! -d $S/downloader/venv ] || mv $S/downloader/venv $S/downloader/venv.old
  mv $S/downloader/venv.new $S/downloader/venv
  # before 2026-11-06: a git clone that never updated, and Chromium (no chain uses a browser: only upstream's tests)
  rm -rf $S/downloader/venv.old $S/downloader/src $S/ms-playwright
  $S/downloader/venv/bin/python -m compileall -q $S/code/app/download.py $S/code/app/scraper_fixes.py
  chown -R root:root $S/downloader $S/code
  if [ "$MODE" = downloader ]; then
    systemctl enable -q --now sal-download.timer
  else
    # relay (home, while the site runs elsewhere): only Super-Pharm, sent on to the server (install-relay.sh does the rest)
    install -m 0644 "$REPO"/deploy/relay-push.sh $S/code/
    install -d -o sal -g sal -m 0750 $S/data/relay $S/data/relay/dumps $S/data/relay/dumps/status
    # start from what this machine already fetched for it: all of that was indexed before the site moved
    if [ ! -e $S/data/relay/dumps/status/superpharm.json ] && [ -f $S/data/dumps/status/superpharm.json ]; then
      install -o sal -g sal -m 0640 $S/data/dumps/status/superpharm.json $S/data/relay/dumps/status/
    fi
    [ -f $S/data/relay/key ] || runuser -u sal -- ssh-keygen -q -t ed25519 -N '' -C "sal-relay@$(hostname)" -f $S/data/relay/key
    systemctl enable -q --now sal-relay.timer  # its runs wait for install-relay.sh's target (ConditionPathExists)
  fi
  ;;
*) echo "usage: install.sh app|downloader|relay" >&2; exit 1 ;;
esac
