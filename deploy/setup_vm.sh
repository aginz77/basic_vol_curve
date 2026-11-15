#!/usr/bin/env bash
# Run on the VM (Debian/Ubuntu) from the copied repo directory:  sudo bash deploy/setup_vm.sh
# Installs the app to /opt/ivsurface and runs it as a systemd service on 127.0.0.1:8000.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/opt/ivsurface

apt-get update
apt-get install -y python3 python3-venv rsync

id ivsurface >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin ivsurface

mkdir -p "$APP_DIR"
rsync -a --delete --exclude .git --exclude .venv --exclude '__pycache__' "$REPO_DIR"/ "$APP_DIR"/
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
chown -R ivsurface:ivsurface "$APP_DIR"

if [ ! -f /etc/ivsurface.env ]; then
  install -m 600 -o root -g root "$REPO_DIR/deploy/env.example" /etc/ivsurface.env
  echo ">>> Fill in /etc/ivsurface.env, then: sudo systemctl restart ivsurface"
fi

install -m 644 "$REPO_DIR/deploy/ivsurface.service" /etc/systemd/system/ivsurface.service
systemctl daemon-reload
systemctl enable --now ivsurface
systemctl --no-pager status ivsurface || true
