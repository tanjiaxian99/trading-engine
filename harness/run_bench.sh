#!/usr/bin/env bash
# Usage: harness/run_bench.sh <normal|volatile> <out_dir> [--xlio]
# Runs on the engine host: starts the harness on the exchange host over ssh, captures every
# packet with NIC hardware timestamps, runs the engine, then pairs ticks with orders.
# Env: EXCHANGE (ssh target) and INTERFACE (NIC facing the exchange) are required.
# ENGINE_CORE=4, LOGGER_CORE=6, SNIFFER_CORE=1, ENGINE_BIN and EXCHANGE_DIR (repo path on the
# exchange host, relative to its home) are optional
set -euo pipefail

harness_dir="$(cd "$(dirname "$0")" && pwd)"
repo_dir="$(dirname "$harness_dir")"
profile="$1"
out_dir="$2"
xlio="${3:-}"

exchange="${EXCHANGE:?set EXCHANGE to the ssh target of the exchange host}"
interface="${INTERFACE:?set INTERFACE to the NIC facing the exchange}"
engine_core="${ENGINE_CORE:-4}"
logger_core="${LOGGER_CORE:-6}"
sniffer_core="${SNIFFER_CORE:-1}"
engine_bin="${ENGINE_BIN:-$repo_dir/build/release/trading_engine}"
exchange_dir="${EXCHANGE_DIR:-trading-engine}"

case "$profile" in
    normal) seconds=120 feed_args="--mode firehose --interval 1.0 --rate 10" ;;
    volatile) seconds=60 feed_args="--mode firehose --interval 0.028 --rate 20" ;;
    *) echo "Profile must be normal or volatile" >&2; exit 2 ;;
esac

mkdir -p "$out_dir"
sniffer="$repo_dir/build/hwsniff"
[[ -x "$sniffer" ]] || gcc -O2 -o "$sniffer" "$harness_dir/hwsniff.c" -libverbs

ssh "$exchange" "cd $exchange_dir && pkill -f '[f]ake_exchange.py'; sleep 1;
    (harness/.venv/bin/python harness/fake_exchange.py $feed_args > /tmp/fake_exchange.log 2>&1 < /dev/null &)"
sleep 2

ib_device="$(ls "/sys/class/net/$interface/device/infiniband/")"
sudo taskset -c "$sniffer_core" "$sniffer" "$ib_device" "$out_dir/wire.pcap" 2> "$out_dir/sniffer.log" &
sleep 1

engine_env=(OKX_API_KEY=x OKX_API_SECRET=x OKX_PASSPHRASE=x OKX_MARKET_DATA_MODE=sbe)
if [[ "$xlio" == "--xlio" ]]; then
    # XLIO needs root for raw NIC access and unlimited locked memory for its buffers
    sudo env LD_PRELOAD=/opt/xlio/lib/libxlio.so "${engine_env[@]}" \
        prlimit --memlock=unlimited:unlimited taskset -c "$engine_core" "$engine_bin" \
        > "$out_dir/engine.log" 2>&1 &
else
    env "${engine_env[@]}" taskset -c "$engine_core" "$engine_bin" > "$out_dir/engine.log" 2>&1 &
fi
sleep 0.5
pid="$(pgrep -x trading_engine)"

# Every thread but the main one (the log writer, and XLIO's own thread) goes to the logger core
for tid in $(ls "/proc/$pid/task"); do
    [[ "$tid" == "$pid" ]] || sudo taskset -p -c "$logger_core" "$tid" >/dev/null
done

sleep "$seconds"
sudo kill -INT "$pid"
while sudo kill -0 "$pid" 2>/dev/null; do
    sleep 0.2
done
sudo pkill -INT -x hwsniff
wait
ssh "$exchange" "pkill -f '[f]ake_exchange.py'; cat /tmp/fake_exchange.log" > "$out_dir/exchange.log"

sudo chmod 644 "$out_dir/wire.pcap"
tail -2 "$out_dir/sniffer.log"
grep 'Tick-to-trade (total)' "$out_dir/engine.log" | tail -1 | sed 's/.*> /engine /'
python3 "$harness_dir/pair_capture.py" "$out_dir/wire.pcap"
