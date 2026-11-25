#!/usr/bin/env bash
# Usage: harness/tune_host.sh <interface> <irq_core> <exchange_ip>
# Per-boot tuning on the engine host, reverts on reboot
set -euo pipefail

interface="$1"
irq_core="$2"
exchange_ip="$3"
ws_port=8443

# Steer every NIC interrupt to one core, away from the engine
sudo systemctl stop irqbalance 2>/dev/null || true
pci_address="$(basename "$(readlink -f "/sys/class/net/$interface/device")")"
for irq in $(grep -F "$pci_address" /proc/interrupts | cut -d: -f1); do
    echo "$irq_core" | sudo tee "/proc/irq/$irq/smp_affinity_list" >/dev/null || true
done

for governor in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo performance | sudo tee "$governor" >/dev/null
done

ensure_rule() {
    local table="$1" chain="$2"
    shift 2
    sudo iptables -t "$table" -C "$chain" "$@" 2>/dev/null || sudo iptables -t "$table" -I "$chain" 1 "$@"
}
# Untracked packets miss the usual ESTABLISHED accept rule, so they're accepted explicitly
ensure_rule raw PREROUTING -s "$exchange_ip" -p tcp --sport "$ws_port" -j CT --notrack
ensure_rule raw OUTPUT -d "$exchange_ip" -p tcp --dport "$ws_port" -j CT --notrack
ensure_rule filter INPUT -s "$exchange_ip" -p tcp --sport "$ws_port" -j ACCEPT

# XLIO allocates its packet buffers from huge pages
echo 2048 | sudo tee /proc/sys/vm/nr_hugepages >/dev/null

echo "isolated: $(cat /sys/devices/system/cpu/isolated)"
echo "NIC interrupts per core: $(for irq in $(grep -F "$pci_address" /proc/interrupts | cut -d: -f1); do
    cat "/proc/irq/$irq/effective_affinity_list"; done | sort | uniq -c | tr -s ' \n' ' ')"
