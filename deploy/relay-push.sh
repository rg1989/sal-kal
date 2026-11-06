#!/bin/sh
# sal-relay's second step: send the price files this machine just downloaded to the live server, and drop them here
# once all are there (rsync exits non-zero otherwise, and then everything stays and goes with the next run).
# The server's account can only write into that chain's folder (deploy/relay-target.sh: rrsync -wo -no-del, which
# also refuses --remove-source-files), so the remote path is the folder itself. Settings live in $RELAY_DIR (made by
# install-relay.sh, never in the repo): target (user@server), known_hosts (the server's host key), key.
# No -t: a file must land with the time it arrives. The server's index run ends by deleting every file older than
# its own start (sal-index.service.d/delete-indexed.conf there), so a file sent during a run with its download time
# would be deleted before any run read it.
set -eu
R=${RELAY_DIR:-/srv/sal/data/relay}
F=${RELAY_FOLDER:?RELAY_FOLDER, e.g. SuperPharm}
[ -s "$R/target" ] || { echo "no $R/target: run install-relay.sh" >&2; exit 1; }
SRC="$R/dumps/$F"
[ -d "$SRC" ] || { echo "nothing downloaded yet"; exit 0; }
LIST=$(mktemp)
trap 'rm -f "$LIST"' EXIT
find "$SRC" -maxdepth 1 -type f ! -name '.*' -printf '%f\n' > "$LIST"
n=$(wc -l < "$LIST")
[ "$n" -gt 0 ] || { echo "no new $F files"; exit 0; }
rsync -r --chmod=F0640 --files-from="$LIST" --timeout=300 \
  -e "${RELAY_SSH:-ssh} -i $R/key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$R/known_hosts -o ConnectTimeout=20" \
  "$SRC/" "$(cat "$R/target"):"
(cd "$SRC" && xargs -d '\n' rm -f -- < "$LIST")
echo "sent $n $F files"
