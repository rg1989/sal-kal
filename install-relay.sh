#!/bin/bash
# Super-Pharm through this machine, while Sal-Kal itself runs on another server. Super-Pharm's price portal (behind
# Link11) refuses that server's datacenter address but answers a home connection. This sets up both ends:
# - here: the downloader and sal-relay.timer, which every 3 hours downloads Super-Pharm's files only and sends them on
#   (sandboxed like every sal-* unit; its key, the server's host key and the target stay in /srv/sal/data/relay);
# - on the server, over your own SSH login: an account that can only upload into Super-Pharm's folder there
#   (deploy/relay-target.sh). The server indexes those files like any chain's.
#   ./install-relay.sh <user@server> [ssh options, e.g. -i ~/.ssh/key]      (uses sudo here and there)
# Undo: sudo systemctl disable --now sal-relay.timer (and on the server: sudo userdel -r salrelay).
set -euo pipefail
cd "$(dirname "$0")"
DEST=${1:?usage: install-relay.sh <user@server> [ssh options]}
shift
HOST=${DEST#*@}
SSH=(ssh -o ConnectTimeout=15 "$@" "$DEST")
R=/srv/sal/data/relay

PY_SRC="$(deploy/find-python.sh)"
sudo PY_SRC="$PY_SRC" deploy/install.sh relay

echo "== the server: an upload-only account"
"${SSH[@]}" "sudo bash -s -- $(sudo base64 -w0 $R/key.pub)" < deploy/relay-target.sh
# the server's host key, read over this already-verified connection
KEY=$("${SSH[@]}" 'cat /etc/ssh/ssh_host_ed25519_key.pub' | awk '{print $1, $2}')
case "$KEY" in ssh-ed25519\ *) ;; *) echo "couldn't read the server's host key" >&2; exit 1 ;; esac
printf '%s %s\n' "$HOST" "$KEY" | sudo tee $R/known_hosts >/dev/null
printf 'salrelay@%s\n' "$HOST" | sudo tee $R/target >/dev/null
sudo chown sal:sal $R/known_hosts $R/target
sudo chmod 0640 $R/known_hosts $R/target

echo "== a test upload (nothing is sent)"
sudo -u sal rsync -n -rt -e "ssh -i $R/key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$R/known_hosts -o ConnectTimeout=20" \
  $R/dumps/status/ "salrelay@$HOST:" >/dev/null && echo "ok"

sudo systemctl start --no-block sal-relay.service
echo "Relaying Super-Pharm every 3 hours. Watch it: journalctl -u sal-relay -f"
