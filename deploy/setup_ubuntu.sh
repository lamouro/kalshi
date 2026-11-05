#!/usr/bin/env bash
# One-shot setup for the always-on recorder host (Ubuntu 22.04/24.04, e.g. AWS us-east-2).
# Keys: put the Kalshi private key at /etc/kalshi/key.pem (chmod 600) and KALSHI_KEY_ID in
# /etc/kalshi/env. Nothing secret is stored in this repo.
set -euo pipefail
sudo apt-get update -y && sudo apt-get install -y python3-venv git chrony
sudo mkdir -p /opt/kalshi /data/kalshi /etc/kalshi
sudo chown "$USER" /opt/kalshi /data/kalshi
git clone https://github.com/lamouro/kalshi /opt/kalshi/repo || (cd /opt/kalshi/repo && git pull)
python3 -m venv /opt/kalshi/venv
/opt/kalshi/venv/bin/pip install -q websockets cryptography
sudo cp /opt/kalshi/repo/deploy/kalshi-recorder.service /opt/kalshi/repo/deploy/kalshi-rest.service /etc/systemd/system/
sudo systemctl daemon-reload
# keyless recorder works immediately:
sudo systemctl enable --now kalshi-rest
# WS recorder needs /etc/kalshi/env with KALSHI_KEY_ID=... and KALSHI_PRIVATE_KEY_PATH=/etc/kalshi/key.pem
if [ -f /etc/kalshi/env ]; then sudo systemctl enable --now kalshi-recorder; fi
echo "done. logs: journalctl -u kalshi-rest -f ; data in /data/kalshi"
