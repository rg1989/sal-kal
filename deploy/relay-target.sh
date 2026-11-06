#!/bin/bash
# The server half of install-relay.sh (run on the server with sudo; install-relay.sh does it over SSH):
# an account, salrelay, whose one key can do nothing but upload into Super-Pharm's price-file folder.
#   sudo deploy/relay-target.sh <base64 of the relay's ssh-ed25519 public key>
# The key is forced to `rrsync -wo -no-del <folder>`: write-only, no deletes, no shell, no forwarding, and no path
# outside the folder. The account reaches the folder through ACLs (search only on data/ and dumps/, nothing readable),
# and what it writes stays readable by sal (the indexer) through the folder's default ACL. Idempotent.
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
PUB=$(printf %s "${1:?usage: relay-target.sh <base64 public key>}" | base64 -d)
case "$PUB" in ssh-ed25519\ *) ;; *) echo "not an ssh-ed25519 public key" >&2; exit 1 ;; esac
case "$PUB" in *$'\n'*|*\"*) echo "unexpected characters in the key" >&2; exit 1 ;; esac
D=/srv/sal/data/dumps/SuperPharm
[ -d /srv/sal/data/dumps ] || { echo "no /srv/sal/data/dumps: install Sal-Kal here first" >&2; exit 1; }
RR=$(command -v rrsync) || { echo "rrsync not found (it comes with rsync 3.2.4+)" >&2; exit 1; }
if ! command -v setfacl >/dev/null; then  # (</dev/null: this script may itself be arriving on stdin, bash -s)
  if command -v apt-get >/dev/null; then DEBIAN_FRONTEND=noninteractive apt-get install -y -q acl >/dev/null </dev/null
  else pacman -S --noconfirm --needed acl >/dev/null </dev/null; fi
fi

# key-only, no password ('*': no password can match, unlike '!' which sshd may treat as a locked account)
id salrelay >/dev/null 2>&1 || useradd --system --home-dir /var/lib/salrelay --create-home --shell /bin/sh salrelay
usermod -p '*' salrelay
install -d -o sal -g sal -m 0750 $D
setfacl -m u:salrelay:--x /srv/sal/data /srv/sal/data/dumps
setfacl -m u:salrelay:rwx -m d:u:salrelay:rwx -m d:u:sal:rwx $D
install -d -o salrelay -g salrelay -m 0700 /var/lib/salrelay/.ssh
printf 'restrict,command="%s -wo -no-del %s" %s\n' "$RR" "$D" "$PUB" > /var/lib/salrelay/.ssh/authorized_keys
chown salrelay:salrelay /var/lib/salrelay/.ssh/authorized_keys
chmod 0600 /var/lib/salrelay/.ssh/authorized_keys
echo "salrelay may upload into $D"
