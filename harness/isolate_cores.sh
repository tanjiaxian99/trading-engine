#!/usr/bin/env bash
# Usage: harness/isolate_cores.sh <engine_core>
# Keeps the scheduler, timer tick and RCU callbacks off the engine core and its SMT sibling.
# Persistent, takes effect after a reboot
set -euo pipefail

engine_core="$1"
cpus="$(cat "/sys/devices/system/cpu/cpu$engine_core/topology/thread_siblings_list")"

echo "GRUB_CMDLINE_LINUX_DEFAULT=\"\$GRUB_CMDLINE_LINUX_DEFAULT isolcpus=$cpus nohz_full=$cpus rcu_nocbs=$cpus\"" |
    sudo tee /etc/default/grub.d/99-trading-engine-isolation.cfg >/dev/null
sudo update-grub
echo "Isolating CPUs $cpus, reboot to apply"
