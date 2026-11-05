#!/bin/bash
# Print the folder of a uv-managed Python 3.12 (installing it if needed), for deploy/install.sh to copy
# into /srv/sal/python. Prints nothing when /srv/sal already has its Python.
set -euo pipefail
[ -x /srv/sal/python/bin/python3.12 ] && exit 0
if uv --version >/dev/null 2>&1; then UV="uv"
elif command -v mise >/dev/null; then UV="mise x uv -- uv"
else sudo pacman -S --needed --noconfirm uv >&2; UV="uv"; fi
$UV python install 3.12 >&2
dirname "$(dirname "$(readlink -f "$($UV python find --managed-python 3.12)")")"
