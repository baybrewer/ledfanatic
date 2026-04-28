#!/bin/bash
# Deploy updated code to the Pi
# Usage: ./deploy.sh [pi-hostname-or-ip]

set -euo pipefail

PI_HOST="${1:-ledfanatic.local}"
PI_USER="jim"
PI_PATH="/opt/ledfanatic"
SRC_PATH="${PI_PATH}/src"
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "Deploying to ${PI_USER}@${PI_HOST}..."

# Sync pi/ source to canonical location
rsync -avz --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude '.venv' \
  --exclude '.pytest_cache' \
  "${REPO_DIR}/pi/" \
  "${PI_USER}@${PI_HOST}:${SRC_PATH}/"

# Reinstall package and restart
ssh "${PI_USER}@${PI_HOST}" "\
  ${PI_PATH}/venv/bin/pip install -e ${SRC_PATH}[audio,video] && \
  sudo systemctl restart ledfanatic"

echo "Deployed and restarted."
