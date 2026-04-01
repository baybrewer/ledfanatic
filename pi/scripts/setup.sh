#!/bin/bash
# Pillar Controller — Pi setup script
# Run once on a fresh Raspberry Pi OS Lite install

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PI_DIR="${REPO_DIR}/pi"
SRC_DIR="/opt/pillar/src"

echo "=== Pillar Controller Setup ==="
echo "Repo: ${REPO_DIR}"

# Create user
if ! id -u pillar &>/dev/null; then
  sudo useradd -r -m -s /bin/bash pillar
  sudo usermod -aG dialout,audio pillar
  echo "Created pillar user"
fi

# Install system dependencies
sudo apt-get update
sudo apt-get install -y \
  python3 python3-pip python3-venv \
  ffmpeg \
  libportaudio2 portaudio19-dev \
  avahi-daemon \
  network-manager \
  rsync \
  git

# Create directory structure
sudo mkdir -p /opt/pillar/{config,media,cache,logs,src}
sudo chown -R pillar:pillar /opt/pillar

# Copy pi/ source to canonical location
echo "Copying source to ${SRC_DIR}..."
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude '.DS_Store' \
  "${PI_DIR}/" "${SRC_DIR}/"
sudo chown -R pillar:pillar "${SRC_DIR}"

# Create virtual environment and install from canonical source
sudo -u pillar python3 -m venv /opt/pillar/venv
sudo -u pillar /opt/pillar/venv/bin/pip install --upgrade pip
sudo -u pillar /opt/pillar/venv/bin/pip install -e "${SRC_DIR}[audio]"

# Copy example config if no real config exists
if [ ! -f /opt/pillar/config/system.yaml ]; then
  sudo -u pillar cp "${PI_DIR}/config/system.yaml.example" /opt/pillar/config/system.yaml
  echo ""
  echo "*** IMPORTANT: Edit /opt/pillar/config/system.yaml ***"
  echo "*** Set auth.token and network.password before starting ***"
  echo ""
fi

# Copy other config files
for cfg in hardware.yaml effects.yaml; do
  if [ ! -f "/opt/pillar/config/${cfg}" ]; then
    sudo -u pillar cp "${PI_DIR}/config/${cfg}" "/opt/pillar/config/${cfg}"
  fi
done

# Install systemd service
sudo cp "${PI_DIR}/systemd/pillar.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pillar

# Allow pillar user to restart service and reboot without password
echo 'pillar ALL=(ALL) NOPASSWD: /bin/systemctl restart pillar, /bin/systemctl stop pillar, /sbin/reboot' | \
  sudo tee /etc/sudoers.d/pillar > /dev/null
sudo chmod 0440 /etc/sudoers.d/pillar

# Setup Wi-Fi hotspot from config
echo ""
echo "=== Wi-Fi hotspot setup ==="
if [ -f /opt/pillar/config/system.yaml ]; then
  SSID=$(python3 -c "import yaml; print(yaml.safe_load(open('/opt/pillar/config/system.yaml'))['network']['ssid'])" 2>/dev/null || echo "Pillar-Control")
  PASS=$(python3 -c "import yaml; print(yaml.safe_load(open('/opt/pillar/config/system.yaml'))['network']['password'])" 2>/dev/null || echo "")
  IP=$(python3 -c "import yaml; print(yaml.safe_load(open('/opt/pillar/config/system.yaml'))['network']['ip'])" 2>/dev/null || echo "192.168.4.1")

  if [ "$PASS" != "" ] && [ "$PASS" != "CHANGE_ME" ]; then
    echo "Creating Wi-Fi hotspot: SSID=${SSID}"
    sudo nmcli connection delete Hotspot 2>/dev/null || true
    sudo nmcli dev wifi hotspot ifname wlan0 ssid "$SSID" password "$PASS" || true
    sudo nmcli connection modify Hotspot autoconnect yes || true
    sudo nmcli connection modify Hotspot ipv4.addresses "${IP}/24" || true
    echo "Hotspot configured."
  else
    echo "WARNING: network.password not set in system.yaml — skipping hotspot setup"
  fi
else
  echo "WARNING: /opt/pillar/config/system.yaml not found — skipping hotspot setup"
fi

# Set hostname
sudo hostnamectl set-hostname pillar

echo ""
echo "=== Setup complete ==="
echo "1. Edit /opt/pillar/config/system.yaml (set auth.token and network.password)"
echo "2. Start with: sudo systemctl start pillar"
echo "3. View logs:  sudo journalctl -u pillar -f"
echo "4. Web UI:     http://192.168.4.1"
