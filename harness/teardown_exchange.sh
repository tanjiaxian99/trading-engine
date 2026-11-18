#!/usr/bin/env bash
set -euo pipefail

sudo sysctl -q net.ipv4.ip_unprivileged_port_start=1024
