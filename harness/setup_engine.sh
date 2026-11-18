#!/usr/bin/env bash
# Usage: harness/setup_engine.sh [exchange_ip] [ca_cert]
# Defaults to an exchange on this machine, otherwise copy certs/ca.crt over from the exchange
set -euo pipefail

harness_dir="$(cd "$(dirname "$0")" && pwd)"
exchange_ip="${1:-127.0.0.1}"
ca_cert="${2:-$harness_dir/certs/ca.crt}"
hosts_marker="# trading-engine-harness"

sudo install -m 644 "$ca_cert" /usr/local/share/ca-certificates/trading-engine-harness.crt
sudo update-ca-certificates

sudo sed -i "/$hosts_marker/d" /etc/hosts
echo "$exchange_ip wspap.okx.com www.okx.com $hosts_marker" | sudo tee -a /etc/hosts >/dev/null
