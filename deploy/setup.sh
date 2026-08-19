#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Agent X — one-shot EC2 provisioner (Ubuntu 22.04 / 24.04, 'ubuntu' user).
#
# No repo URL is hardcoded: this script operates on the checkout it lives in.
# You clone the repo yourself (with whatever URL it has — personal, org, or a
# fork), then run this from inside it. It installs deps via uv, wires the
# secrets you copied up (~/.env and ~/root.crt), initializes the DB, and runs
# the app as a systemd service on :8080 that auto-restarts and survives reboots.
#
# Usage (see docs/DEPLOY.md):
#   scp -i key.pem .env ubuntu@<DNS>:~/.env
#   scp -i key.pem ~/.postgresql/root.crt ubuntu@<DNS>:~/root.crt
#   ssh -i key.pem ubuntu@<DNS>
#   git clone <YOUR-REPO-URL> ~/agent-x      # your own clone URL
#   cd ~/agent-x && bash deploy/setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Repo root = the parent of this script's directory (deploy/). Self-locating,
# so nothing here depends on the repo's owner/name/URL.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${PORT:-8080}"
cd "$APP_DIR"

echo "==> [1/8] system packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv git curl ca-certificates

echo "==> [2/8] uv (cross-platform python runner)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo "==> [3/8] swap (micro instances have ~1GB RAM; the embedder needs headroom)"
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> [4/8] refresh checkout (if it's a git clone)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only || echo "   (skipping pull — local changes or detached; continuing)"
fi

echo "==> [5/8] wire secrets copied to \$HOME"
if [ -f "$HOME/.env" ] && [ ! -f "$APP_DIR/.env" ]; then
  mv "$HOME/.env" "$APP_DIR/.env"
fi
if [ ! -f "$APP_DIR/.env" ]; then
  echo "!! No .env at $APP_DIR/.env — scp your local .env up, then re-run." >&2
  exit 1
fi
# CockroachDB CA cert → place at ~/.postgresql/root.crt and repoint the DSN at it
if [ -f "$HOME/root.crt" ]; then
  mkdir -p "$HOME/.postgresql"
  mv "$HOME/root.crt" "$HOME/.postgresql/root.crt"
fi
if [ -f "$HOME/.postgresql/root.crt" ]; then
  sed -i "s#sslrootcert=[^&\"' ]*#sslrootcert=$HOME/.postgresql/root.crt#g" "$APP_DIR/.env"
fi

echo "==> [6/8] python deps via uv"
uv venv
uv pip install -r requirements.txt

echo "==> [7/8] initialize database (idempotent)"
uv run scripts/init_db.py || echo "   (init_db returned non-zero; likely already initialized — continuing)"

echo "==> [8/8] systemd service (auto-restart + boot-persistent)"
sudo tee /etc/systemd/system/agent-x.service >/dev/null <<UNIT
[Unit]
Description=Agent X — verifiable forgetting for AI-agent memory
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$HOME/.local/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now agent-x
sleep 4
sudo systemctl --no-pager status agent-x | head -12 || true

IP=$(curl -fsSL http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "<PUBLIC_IP>")
echo ""
echo "============================================================"
echo " Agent X is live:  http://$IP:$PORT/app"
echo " Logs:               sudo journalctl -u agent-x -f"
echo " Restart:            sudo systemctl restart agent-x"
echo "============================================================"
