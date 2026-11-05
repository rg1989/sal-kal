set -e
D="$HOME/il-prices"; mkdir -p "$D" && cd "$D"
# ponytail: uv is only needed at install time; prefer a working one (mise shim may have no version set)
if uv --version >/dev/null 2>&1; then UV="uv"
elif command -v mise >/dev/null; then UV="mise x uv -- uv"
else sudo pacman -S --needed --noconfirm uv; UV="uv"; fi
[ -d src ] || git clone --depth 1 https://github.com/OpenIsraeliSupermarkets/israeli-supermarket-scarpers src
[ -d .venv ] || $UV venv -q --python 3.12 .venv
$UV pip install -q --python .venv/bin/python ./src
.venv/bin/python -m playwright install chromium
cat > run.sh <<'EOF'
#!/bin/bash
cd "$HOME/il-prices"
export STORAGE_PATH="$PWD/dumps" STATUS_DATABASE_PATH="$PWD/dumps/status" SINGLE_PASS=true NUMBER_OF_PROCESSES=4
find dumps -path dumps/status -prune -o -type f -mtime +2 -delete 2>/dev/null || true
exec .venv/bin/python src/main.py
EOF
chmod +x run.sh
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/il-prices.service <<EOF
[Unit]
Description=Download Israeli supermarket price files
[Service]
Type=oneshot
ExecStart=$D/run.sh
Nice=10
TimeoutStartSec=3h
EOF
cat > ~/.config/systemd/user/il-prices.timer <<'EOF'
[Unit]
Description=Fetch supermarket prices every 3 hours
[Timer]
OnCalendar=*-*-* 00/3:17:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now il-prices.timer
sudo loginctl enable-linger "$USER"
systemctl --user start --no-block il-prices.service
echo "Installed. Watch it with: journalctl --user -u il-prices -f"
