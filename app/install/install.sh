#!/bin/bash
# Sal-Kal on your own computer (macOS and Linux). Installs it, or updates it in place (your carts and prices stay),
# then opens the setup page in your browser. Nothing needs admin rights; everything goes in one folder:
#   macOS  ~/Library/Application Support/Sal-Kal      Linux  ~/.local/share/sal-kal
# Run:  curl -fsSL https://sal-kal.com/install.sh | bash
# (SAL_HOME picks another folder; SAL_KAL_SOURCE a .tar.gz URL or a folder of the code, for testing.)
set -euo pipefail

# all in one function: with `curl | bash`, bash reads the whole of it before running a line (nothing that reads
# standard input can swallow the rest of the script)
main() {
UV_VERSION=0.13.0
SRC="${SAL_KAL_SOURCE:-https://github.com/rg1989/sal-kal/archive/refs/heads/main.tar.gz}"
B=$'\e[1m'; D=$'\e[2m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'
[ -t 1 ] || { B=; D=; Y=; R=; N=; }
step() { printf '\n%s[%s/5]%s %s\n' "$B$Y" "$1" "$N" "$2"; }
fail() { printf '\n%sSal-Kal could not be installed:%s %s\n' "$R$B" "$N" "$1" >&2; exit 1; }
trap 'fail "a step failed (above). Check the internet connection and run the same command again."' ERR

case "$(uname -s)" in
  Darwin) OS=mac; ROOT="${SAL_HOME:-$HOME/Library/Application Support/Sal-Kal}" ;;
  Linux) OS=linux; ROOT="${SAL_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/sal-kal}" ;;
  *) fail "this installer is for macOS and Linux (Windows: https://sal-kal.com/download)" ;;
esac
case "$(uname -m)" in
  x86_64|amd64) ARCH=x86_64 ;;
  arm64|aarch64) ARCH=aarch64 ;;
  *) fail "unsupported processor: $(uname -m)" ;;
esac
[ "$OS" = mac ] && TARGET="$ARCH-apple-darwin" || TARGET="$ARCH-unknown-linux-gnu"
sha() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

printf '\n%sסל קל · Sal-Kal%s\n' "$B" "$N"
printf '%sInstalling into %s. This takes a few minutes; keep this window open.%s\n' "$D" "$ROOT" "$N"
mkdir -p "$ROOT/bin" "$ROOT/data"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export UV_PYTHON_INSTALL_DIR="$ROOT/python" UV_CACHE_DIR="$ROOT/cache" UV_NO_CONFIG=1 UV_PYTHON_PREFERENCE=only-managed

step 1 "Installer tools"
if [ "$("$ROOT/bin/uv" --version 2>/dev/null | cut -d' ' -f2)" != "$UV_VERSION" ]; then
  U="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$TARGET.tar.gz"
  curl -fsSL "$U" -o "$TMP/uv.tar.gz"
  curl -fsSL "$U.sha256" -o "$TMP/uv.sha256"
  [ "$(sha "$TMP/uv.tar.gz")" = "$(cut -d' ' -f1 "$TMP/uv.sha256")" ] || fail "the download of uv didn't match its checksum"
  tar -xzf "$TMP/uv.tar.gz" -C "$TMP"
  install -m 0755 "$TMP/uv-$TARGET/uv" "$ROOT/bin/uv"
fi
UV="$ROOT/bin/uv"

step 2 "Python"
"$UV" python install -q 3.12

step 3 "Sal-Kal"
# an earlier copy that's running lets go of its files first
if [ -x "$ROOT/venv/bin/python" ] && [ -f "$ROOT/app/app/local.py" ]; then
  (cd "$ROOT/app" && "$ROOT/venv/bin/python" -m app.local stop) >/dev/null 2>&1 || true
fi
rm -rf "$ROOT/app.new"; mkdir -p "$ROOT/app.new"
if [ -d "$SRC" ]; then
  (cd "$SRC" && tar --exclude .git --exclude .venv --exclude __pycache__ --exclude .claude -cf - .) | tar -xf - -C "$ROOT/app.new"
else
  curl -fsSL "$SRC" -o "$TMP/app.tar.gz"
  tar -xzf "$TMP/app.tar.gz" -C "$ROOT/app.new" --strip-components 1
fi
[ -f "$ROOT/app.new/app/local.py" ] || fail "the download doesn't look like Sal-Kal"
rm -rf "$ROOT/app.old"; [ -d "$ROOT/app" ] && mv "$ROOT/app" "$ROOT/app.old"
mv "$ROOT/app.new" "$ROOT/app"; rm -rf "$ROOT/app.old"

step 4 "Components (about 100 MB)"
[ -x "$ROOT/venv/bin/python" ] || "$UV" venv -q --python 3.12 "$ROOT/venv"
"$UV" pip install -q --python "$ROOT/venv/bin/python" -r "$ROOT/app/requirements.txt" -r "$ROOT/app/app/install/requirements.txt"

rm -rf "$ROOT/cache"   # the download cache: 200+ MB nothing needs again

step 5 "Starting"
trap - ERR
cd "$ROOT/app"
"$ROOT/venv/bin/python" -m app.local install || fail "it didn't start. Its log: $ROOT/data/sal-kal.log"
printf '%sThe setup page opened in your browser. You can close this window.%s\n\n' "$B" "$N"
}

main "$@"
