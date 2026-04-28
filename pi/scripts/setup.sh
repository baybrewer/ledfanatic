#!/bin/bash
# LED Fanatic — Pi setup script
# Run once on a fresh Raspberry Pi OS Lite install

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PI_DIR="${REPO_DIR}/pi"
SRC_DIR="/opt/ledfanatic/src"

echo "=== LED Fanatic Setup ==="
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
sudo mkdir -p /opt/ledfanatic/{config,media,cache,logs,src}
sudo chown -R pillar:pillar /opt/ledfanatic

# Copy pi/ source to canonical location
echo "Copying source to ${SRC_DIR}..."
sudo rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude '.DS_Store' \
  --exclude '.venv' \
  "${PI_DIR}/" "${SRC_DIR}/"
sudo chown -R pillar:pillar "${SRC_DIR}"

# Create virtual environment and install from canonical source
sudo -u pillar python3 -m venv /opt/ledfanatic/venv
sudo -u pillar /opt/ledfanatic/venv/bin/pip install --upgrade pip
sudo -u pillar /opt/ledfanatic/venv/bin/pip install -e "${SRC_DIR}[audio,video]"

# Copy example config if no real config exists
if [ ! -f /opt/ledfanatic/config/system.yaml ]; then
  sudo -u pillar cp "${PI_DIR}/config/system.yaml.example" /opt/ledfanatic/config/system.yaml
  echo ""
  echo "*** IMPORTANT: Edit /opt/ledfanatic/config/system.yaml ***"
  echo "*** Set auth.token and network.password before starting ***"
  echo ""
fi

# Copy other config files
for cfg in hardware.yaml effects.yaml; do
  if [ ! -f "/opt/ledfanatic/config/${cfg}" ]; then
    sudo -u pillar cp "${PI_DIR}/config/${cfg}" "/opt/ledfanatic/config/${cfg}"
  fi
done

# Install systemd service
sudo cp "${PI_DIR}/systemd/ledfanatic.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ledfanatic

# Allow pillar user to restart service and reboot without password
echo 'pillar ALL=(ALL) NOPASSWD: /bin/systemctl restart ledfanatic, /bin/systemctl stop ledfanatic, /sbin/reboot' | \
  sudo tee /etc/sudoers.d/pillar > /dev/null
sudo chmod 0440 /etc/sudoers.d/pillar

# Setup Wi-Fi hotspot from config
echo ""
echo "=== Wi-Fi hotspot setup ==="
if [ -f /opt/ledfanatic/config/system.yaml ]; then
  SSID=$(/opt/ledfanatic/venv/bin/python3 -c "import yaml; print(yaml.safe_load(open('/opt/ledfanatic/config/system.yaml'))['network']['ssid'])" 2>/dev/null || echo "LEDFanatic")
  PASS=$(/opt/ledfanatic/venv/bin/python3 -c "import yaml; print(yaml.safe_load(open('/opt/ledfanatic/config/system.yaml'))['network']['password'])" 2>/dev/null || echo "")
  IP=$(/opt/ledfanatic/venv/bin/python3 -c "import yaml; print(yaml.safe_load(open('/opt/ledfanatic/config/system.yaml'))['network']['ip'])" 2>/dev/null || echo "192.168.4.1")
  HOSTNAME=$(/opt/ledfanatic/venv/bin/python3 -c "import yaml; print(yaml.safe_load(open('/opt/ledfanatic/config/system.yaml'))['network']['hostname'])" 2>/dev/null || echo "ledfanatic")

  if [ "$PASS" != "" ] && [ "$PASS" != "CHANGE_ME" ]; then
    echo "Creating Wi-Fi hotspot: SSID=${SSID}"
    sudo nmcli connection delete Hotspot 2>/dev/null || true
    if sudo nmcli dev wifi hotspot ifname wlan0 ssid "$SSID" password "$PASS"; then
      sudo nmcli connection modify Hotspot autoconnect yes
      sudo nmcli connection modify Hotspot ipv4.addresses "${IP}/24"
      echo "Hotspot configured successfully."
    else
      echo "ERROR: Failed to create hotspot. Check wlan0 availability."
      echo "You can retry manually:"
      echo "  sudo nmcli dev wifi hotspot ifname wlan0 ssid '$SSID' password '$PASS'"
    fi
  else
    echo "WARNING: network.password not set in system.yaml — skipping hotspot setup"
  fi

  # Set hostname from config
  sudo hostnamectl set-hostname "$HOSTNAME"
else
  echo "WARNING: /opt/ledfanatic/config/system.yaml not found — skipping hotspot setup"
  sudo hostnamectl set-hostname ledfanatic
fi

echo ""
echo "=== Setup complete ==="
echo "1. Edit /opt/ledfanatic/config/system.yaml (set auth.token and network.password)"
echo "2. Start with: sudo systemctl start ledfanatic"
echo "3. View logs:  sudo journalctl -u ledfanatic -f"
echo "4. Web UI:     http://192.168.4.1"
