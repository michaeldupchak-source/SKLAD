#!/bin/bash
# deploy.sh — запускается на VPS автоматически через GitHub Actions
# Путь: /var/www/sklad/deploy.sh
set -e

APP_DIR="/var/www/sklad"
SERVICE="sklad"

echo "→ Pulling latest code..."
cd "$APP_DIR"
git pull origin main

echo "→ Installing dependencies..."
source .venv/bin/activate
pip install -r requirements.txt --quiet

echo "→ Restarting service..."
sudo systemctl restart "$SERVICE"

echo "✅ Deploy complete at $(date)"
