#!/usr/bin/env bash
set -euo pipefail

sudo sed -i "/# trading-engine-harness/d" /etc/hosts
sudo rm -f /usr/local/share/ca-certificates/trading-engine-harness.crt
sudo update-ca-certificates --fresh
