#!/usr/bin/env bash
# Usage: harness/setup_vm.sh [exchange_ip], defaults to 127.0.0.1
set -euo pipefail

harness_dir="$(cd "$(dirname "$0")" && pwd)"
exchange_ip="${1:-127.0.0.1}"
hosts_marker="# trading-engine-harness"

sudo apt-get install -y -qq python3-venv
python3 -m venv "$harness_dir/.venv"
"$harness_dir/.venv/bin/pip" install -q -r "$harness_dir/requirements.txt"

"$harness_dir/gen_certs.sh"
sudo install -m 644 "$harness_dir/certs/ca.crt" /usr/local/share/ca-certificates/trading-engine-harness.crt
sudo update-ca-certificates

sudo sed -i "/$hosts_marker/d" /etc/hosts
echo "$exchange_ip wspap.okx.com www.okx.com $hosts_marker" | sudo tee -a /etc/hosts >/dev/null

# Lets the harness bind 443 without root, reverts on reboot
sudo sysctl -q net.ipv4.ip_unprivileged_port_start=443
