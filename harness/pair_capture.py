#!/usr/bin/env python3
# Usage: harness/pair_capture.py capture.pcap
# Capture during a paced run with:
#   sudo tcpdump -i lo -s 128 -B 65536 --time-stamp-precision=nano -w capture.pcap 'tcp port 8443'
# Add -j adapter_unsynced on a NIC with hardware timestamps.
import argparse
import struct
from collections import Counter

WS_PORT = 8443
LINKTYPE_ETHERNET = 1
ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_VLAN = 0x8100
PROTO_TCP = 6

# Smaller TLS records are heartbeats (WebSocket ping/pong or OKX's "ping" text), never ticks
# or orders
MIN_MESSAGE_LEN = 64

# The harness waits --warmup seconds after the engine logs in before its first move, so the
# first tick after this much silence starts the feed and everything before it is connection setup
FEED_START_GAP_NS = 1_000_000_000

PCAP_MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", False),
    b"\xa1\xb2\xc3\xd4": (">", False),
    b"\x4d\x3c\xb2\xa1": ("<", True),
    b"\xa1\xb2\x3c\x4d": (">", True),
}


def read_frames(path):
    with open(path, "rb") as f:
        header = f.read(24)
        if header[:4] not in PCAP_MAGICS:
            raise SystemExit(f"{path} is not a pcap file")
        endian, nanos = PCAP_MAGICS[header[:4]]
        link_type = struct.unpack(endian + "I", header[20:24])[0] & 0x0FFFFFFF
        if link_type != LINKTYPE_ETHERNET:
            raise SystemExit(f"Unsupported link type {link_type}, capture on one interface, not 'any'")

        record = struct.Struct(endian + "IIII")
        while chunk := f.read(record.size):
            sec, frac, captured_len, _ = record.unpack(chunk)
            frame = f.read(captured_len)
            yield sec * 1_000_000_000 + (frac if nanos else frac * 1000), frame


def parse_tcp(frame):
    ethertype = struct.unpack_from("!H", frame, 12)[0]
    ip = 14
    if ethertype == ETHERTYPE_VLAN:
        ethertype = struct.unpack_from("!H", frame, 16)[0]
        ip = 18
    if ethertype != ETHERTYPE_IPV4 or frame[ip + 9] != PROTO_TCP:
        return None

    ip_header_len = (frame[ip] & 0x0F) * 4
    total_len = struct.unpack_from("!H", frame, ip + 2)[0]
    tcp = ip + ip_header_len
    src_port, dst_port, seq = struct.unpack_from("!HHI", frame, tcp)
    tcp_header_len = (frame[tcp + 12] >> 4) * 4
    return src_port, dst_port, seq, total_len - ip_header_len - tcp_header_len


def is_retransmit(last_seq_end, flow, seq, payload_len):
    end = (seq + payload_len) & 0xFFFFFFFF
    last = last_seq_end.get(flow)
    if last is not None:
        advance = (end - last) & 0xFFFFFFFF
        if advance == 0 or advance >= 0x80000000:
            return True
    last_seq_end[flow] = end
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture")
    parser.add_argument("--md-port", type=int, help="engine-side port of the market data connection")
    parser.add_argument("--order-port", type=int, help="engine-side port of the private connection")
    args = parser.parse_args()

    packets = []
    for ts, frame in read_frames(args.capture):
        tcp = parse_tcp(frame)
        if tcp and tcp[3] >= MIN_MESSAGE_LEN:
            packets.append((ts, *tcp))
    if not packets:
        raise SystemExit("No TCP payload packets in capture")

    # The private connection carries nearly all engine sends, the market data connection
    # carries nearly all harness sends that aren't order acks
    to_harness = Counter(src for _, src, dst, _, _ in packets if dst == WS_PORT)
    from_harness = Counter(dst for _, src, dst, _, _ in packets if src == WS_PORT)
    order_port = args.order_port or to_harness.most_common(1)[0][0]
    md_port = args.md_port or max((port for port in from_harness if port != order_port),
                                  key=from_harness.get)

    # Only valid for paced runs, where every tick is answered by one order before the next tick
    latencies_ns = []
    tick_ts = None
    feed_started = False
    last_setup_ts = None
    unmatched_ticks = 0
    extra_order_packets = 0
    retransmits = 0
    last_seq_end = {}
    for ts, src, dst, seq, payload_len in packets:
        if is_retransmit(last_seq_end, (src, dst), seq, payload_len):
            retransmits += 1
            continue
        if src == WS_PORT and dst == md_port:
            if not feed_started:
                if last_setup_ts is None or ts - last_setup_ts < FEED_START_GAP_NS:
                    last_setup_ts = ts
                    continue
                feed_started = True
            if tick_ts is not None:
                unmatched_ticks += 1
            tick_ts = ts
        elif src == order_port and dst == WS_PORT and feed_started:
            if tick_ts is None:
                extra_order_packets += 1
            else:
                latencies_ns.append(ts - tick_ts)
                tick_ts = None
    if tick_ts is not None:
        unmatched_ticks += 1

    print(f"market data port={md_port} order port={order_port}")
    print(f"pairs={len(latencies_ns)} unmatched_ticks={unmatched_ticks} "
          f"extra_order_packets={extra_order_packets} retransmits={retransmits}")
    if not latencies_ns:
        return

    latencies_ns.sort()
    n = len(latencies_ns)

    def pct(q):
        return latencies_ns[min(n - 1, int(q * n))] / 1000

    print(f"tick-to-trade us: p50={pct(0.5):.2f} p90={pct(0.9):.2f} p99={pct(0.99):.2f} "
          f"p99.9={pct(0.999):.2f} max={latencies_ns[-1] / 1000:.2f}")


if __name__ == "__main__":
    main()
