#!/usr/bin/env bash
set -euo pipefail

harness_dir="$(cd "$(dirname "$0")" && pwd)"

sudo apt-get install -y -qq python3-venv
python3 -m venv "$harness_dir/.venv"
"$harness_dir/.venv/bin/pip" install -q -r "$harness_dir/requirements.txt"

"$harness_dir/gen_certs.sh"

# Lets the harness bind 443 without root, reverts on reboot
sudo sysctl -q net.ipv4.ip_unprivileged_port_start=443
