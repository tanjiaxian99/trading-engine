import struct
import time

# Layout from okx/marketdata/okx_sbe_1_0.xml
SCHEMA_ID = 1
SCHEMA_VERSION = 0
BBO_TBT_TEMPLATE_ID = 1000

HEADER = struct.Struct("<HHHH")
BBO_TBT = struct.Struct("<qqqqqqqqiibb")


def encode_bbo_tbt(inst_id_code, seq_id, ask_px, ask_sz, bid_px, bid_sz, px_exponent, sz_exponent):
    now_us = time.time_ns() // 1000
    header = HEADER.pack(BBO_TBT.size, BBO_TBT_TEMPLATE_ID, SCHEMA_ID, SCHEMA_VERSION)
    body = BBO_TBT.pack(inst_id_code, now_us, now_us, seq_id, ask_px, ask_sz, bid_px, bid_sz, 1, 1,
                        px_exponent, sz_exponent)
    return header + body
