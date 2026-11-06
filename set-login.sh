#!/bin/bash
# Turn on sign-in for Sal: writes /srv/sal/data/auth.json (readable by Sal only). Uses sudo. No restart needed.
#   ./set-login.sh google <client id>        asks for the client secret (not shown, not in the process list)
#   ./set-login.sh facebook <app id>         asks for the app secret
#   ./set-login.sh base-url https://sal.example.com
#   ./set-login.sh owner-email you@gmail.com only this account takes over the household's carts (optional)
#   ./set-login.sh turnstile <site key>      Cloudflare Turnstile bot check on sign-in; asks for the secret key
#   ./set-login.sh govmap <api key>          GovMap's national address points (exact houses); key registered to the site's domain
#   ./set-login.sh off google|facebook|turnstile|govmap
#   ./set-login.sh status
# With no provider set up Sal runs as before: one household, no sign-in.
set -euo pipefail
if [ "${1:-}" = google ] || [ "${1:-}" = facebook ] || [ "${1:-}" = turnstile ]; then
  [ $# -eq 2 ] || { echo "usage: $0 $1 <id>" >&2; exit 2; }
  read -rsp "$1 secret: " SECRET; echo
  [ -n "$SECRET" ] || { echo "no secret given" >&2; exit 2; }
  printf '%s' "$SECRET" | sudo -u sal sh -c 'cd /srv/sal/code && SAL_SECRET="$(cat)" BASKET_DB=/srv/sal/data/basket.db /srv/sal/venv/bin/python -m app.auth "$@"' sh "$1" "$2"
else
  sudo -u sal sh -c 'cd /srv/sal/code && BASKET_DB=/srv/sal/data/basket.db /srv/sal/venv/bin/python -m app.auth "$@"' sh "$@"
fi
