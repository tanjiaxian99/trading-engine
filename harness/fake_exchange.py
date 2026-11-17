#!/usr/bin/env python3
import argparse
import asyncio
import itertools
import json
import logging
import random
import ssl
import time
from pathlib import Path

from aiohttp import WSMsgType, web

import sbe

log = logging.getLogger("fake_exchange")

CERT_DIR = Path(__file__).resolve().parent / "certs"
REST_PORT = 443
WS_PORT = 8443

INSTRUMENTS = {
    "ETH-USDT": {"instIdCode": 2001, "tickSz": "0.01", "lotSz": "0.000001", "minSz": "0.001"},
    "BTC-USDT": {"instIdCode": 2002, "tickSz": "0.1", "lotSz": "0.00000001", "minSz": "0.00001"},
}
INST_ID_BY_CODE = {spec["instIdCode"]: inst_id for inst_id, spec in INSTRUMENTS.items()}
FEED_INST_ID = "ETH-USDT"

PX_EXPONENT = -2
SZ_EXPONENT = -4
BASE_PX = 300000

# DEPTH + BAND must stay within the engine's 32-level book capacity
DEPTH = 20
BAND = 8

ORDER_OPS = {"order", "batch-orders", "amend-order", "cancel-order"}


def dumps(obj):
    return json.dumps(obj, separators=(",", ":"))


def now_ms():
    return time.time_ns() // 1_000_000


def ok(data):
    return web.json_response({"code": "0", "msg": "", "data": data}, dumps=dumps)


class Market:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.best_bid = BASE_PX
        self.bids = {BASE_PX - i: self.random_size() for i in range(DEPTH)}
        self.asks = {BASE_PX + 1 + i: self.random_size() for i in range(DEPTH)}
        self.seq_id = 1

    def random_size(self):
        return self.rng.randint(100, 50_000)

    def move(self):
        step = self.rng.choice((-1, 1))
        if abs(self.best_bid + step - BASE_PX) > BAND:
            step = -step

        bid = self.best_bid
        if step > 0:
            del self.asks[bid + 1]
            self.bids[bid + 1] = self.random_size()
            self.best_bid += 1
            return {bid + 1: self.bids[bid + 1]}, {bid + 1: 0}

        del self.bids[bid]
        self.asks[bid] = self.random_size()
        self.best_bid -= 1
        return {bid: 0}, {bid: self.asks[bid]}

    # Resizes the top of one side without moving the mid
    def churn(self):
        if self.rng.random() < 0.5:
            self.bids[self.best_bid] = self.random_size()
            return {self.best_bid: self.bids[self.best_bid]}, {}
        ask = self.best_bid + 1
        self.asks[ask] = self.random_size()
        return {}, {ask: self.asks[ask]}

    def books_message(self, bids, asks, prev_seq_id):
        def levels(side, descending):
            return [[f"{px / 100:.2f}", f"{sz / 10000:.4f}", "0", "1"]
                    for px, sz in sorted(side.items(), reverse=descending)]

        return dumps({
            "arg": {"channel": "books", "instId": FEED_INST_ID},
            "action": "snapshot" if prev_seq_id == -1 else "update",
            "data": [{
                "asks": levels(asks, False),
                "bids": levels(bids, True),
                "ts": str(now_ms()),
                "checksum": 0,
                "prevSeqId": prev_seq_id,
                "seqId": self.seq_id,
            }],
        })

    def bbo_message(self):
        ask = self.best_bid + 1
        return sbe.encode_bbo_tbt(INSTRUMENTS[FEED_INST_ID]["instIdCode"], self.seq_id, ask,
                                  self.asks[ask], self.best_bid, self.bids[self.best_bid],
                                  PX_EXPONENT, SZ_EXPONENT)


# Queues sends per socket so a new subscriber always gets its snapshot before any update
class Subscriber:
    def __init__(self, ws):
        self.ws = ws
        self.queue = asyncio.Queue()
        self.task = asyncio.create_task(self.drain())

    def send(self, message):
        self.queue.put_nowait(message)

    async def drain(self):
        try:
            while True:
                message = await self.queue.get()
                if isinstance(message, bytes):
                    await self.ws.send_bytes(message)
                else:
                    await self.ws.send_str(message)
        except ConnectionResetError:
            pass

    def close(self):
        self.task.cancel()


class Exchange:
    def __init__(self, market):
        self.market = market
        self.orders = {}
        self.ord_ids = itertools.count(1)
        self.private_sockets = set()
        self.books_subscribers = {}
        self.sbe_subscribers = {}
        self.order_seen = asyncio.Event()
        self.move_sent_ns = None

        self.moves = 0
        self.matched = 0
        self.timeouts = 0
        self.churned = 0
        self.resubscribes = 0
        self.rtt_us = []

    def routes(self):
        return [
            web.get("/api/v5/public/time", self.rest_time),
            web.get("/api/v5/account/balance", self.rest_balance),
            web.get("/api/v5/public/instruments", self.rest_instruments),
            web.get("/api/v5/account/instruments", self.rest_instruments),
            web.get("/api/v5/trade/orders-pending", self.rest_orders_pending),
            web.post("/api/v5/trade/order", self.rest_place_order),
            web.post("/api/v5/trade/cancel-order", self.rest_cancel_order),
            web.get("/ws/v5/private", self.private_ws),
            web.get("/ws/v5/public", self.public_ws),
            web.get("/ws/v5/public-sbe", self.sbe_ws),
        ]

    def publish(self, bids, asks, moved):
        prev_seq_id = self.market.seq_id
        self.market.seq_id += 1
        if self.books_subscribers:
            update = self.market.books_message(bids, asks, prev_seq_id)
            for subscriber in self.books_subscribers.values():
                subscriber.send(update)
        if self.sbe_subscribers:
            bbo = self.market.bbo_message()
            for subscriber in self.sbe_subscribers.values():
                subscriber.send(bbo)

        if not moved:
            self.churned += 1
            return
        if self.move_sent_ns is not None:
            self.timeouts += 1
        self.moves += 1
        self.order_seen.clear()
        self.move_sent_ns = time.perf_counter_ns()

    def on_order_op(self):
        if self.move_sent_ns is None:
            return
        self.matched += 1
        self.rtt_us.append((time.perf_counter_ns() - self.move_sent_ns) / 1000)
        self.move_sent_ns = None
        self.order_seen.set()

    def apply_order_op(self, op, arg):
        if op in ("order", "batch-orders"):
            order = {
                "instType": "SPOT",
                "instId": INST_ID_BY_CODE.get(arg.get("instIdCode"), arg.get("instId", "")),
                "ordId": str(next(self.ord_ids)),
                "clOrdId": arg.get("clOrdId", ""),
                "side": arg["side"],
                "ordType": "limit",
                "px": arg["px"],
                "sz": arg["sz"],
                "state": "live",
                "accFillSz": "0",
                "avgPx": "",
                "fillPx": "",
                "fillSz": "0",
            }
            self.orders[order["ordId"]] = order
        else:
            order = self.orders.get(arg.get("ordId", ""))
            if order is None:
                return {"ordId": arg.get("ordId", ""), "clOrdId": "", "sCode": "51603",
                        "sMsg": "Order does not exist"}, None
            if op == "amend-order":
                order["px"] = arg.get("newPx", order["px"])
                order["sz"] = arg.get("newSz", order["sz"])
            else:
                del self.orders[order["ordId"]]
                order["state"] = "canceled"

        result = {"ordId": order["ordId"], "clOrdId": order["clOrdId"], "ts": str(now_ms()),
                  "sCode": "0", "sMsg": ""}
        return result, order

    async def push_order(self, order):
        order["uTime"] = str(now_ms())
        message = dumps({"arg": {"channel": "orders", "instType": "SPOT"}, "data": [order]})
        for ws in list(self.private_sockets):
            await ws.send_str(message)

    async def rest_time(self, request):
        return ok([{"ts": str(now_ms())}])

    async def rest_balance(self, request):
        return ok([{"details": [
            {"ccy": "USDT", "cashBal": "100000", "availBal": "100000"},
            {"ccy": "ETH", "cashBal": "10", "availBal": "10"},
        ]}])

    async def rest_instruments(self, request):
        inst_id = request.query.get("instId", "")
        if inst_id not in INSTRUMENTS:
            return ok([])
        return ok([{"instType": "SPOT", "instId": inst_id, **INSTRUMENTS[inst_id]}])

    async def rest_orders_pending(self, request):
        return ok(list(self.orders.values()))

    async def rest_place_order(self, request):
        return await self.rest_order_op("order", await request.json())

    async def rest_cancel_order(self, request):
        return await self.rest_order_op("cancel-order", await request.json())

    async def rest_order_op(self, op, arg):
        result, order = self.apply_order_op(op, arg)
        if order is not None:
            await self.push_order(order)
        code = "0" if result["sCode"] == "0" else "1"
        return web.json_response({"code": code, "msg": "", "data": [result]}, dumps=dumps)

    async def accept(self, request, heartbeat=None):
        ws = web.WebSocketResponse(compress=False, heartbeat=heartbeat)
        await ws.prepare(request)
        tls = request.transport.get_extra_info("ssl_object")
        log.info("%s connected over %s %s", request.path, tls.version(), tls.cipher()[0])
        return ws

    async def private_ws(self, request):
        ws = await self.accept(request)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                if msg.data == "ping":
                    await ws.send_str("pong")
                    continue

                req = json.loads(msg.data)
                op = req.get("op")
                if op in ORDER_OPS:
                    self.on_order_op()
                    outcomes = [self.apply_order_op(op, arg) for arg in req["args"]]
                    results = [result for result, _ in outcomes]
                    all_ok = all(result["sCode"] == "0" for result in results)
                    await ws.send_str(dumps({"id": req["id"], "op": op, "code": "0" if all_ok else "1",
                                             "msg": "", "data": results}))
                    for _, order in outcomes:
                        if order is not None:
                            await self.push_order(order)
                elif op == "login":
                    await ws.send_str(dumps({"event": "login", "code": "0", "msg": ""}))
                elif op == "subscribe":
                    for arg in req["args"]:
                        await ws.send_str(dumps({"event": "subscribe", "arg": arg}))
                    self.private_sockets.add(ws)
                else:
                    log.warning("Unhandled private op: %s", msg.data)
        finally:
            self.private_sockets.discard(ws)
            log.info("%s disconnected", request.path)
        return ws

    async def public_ws(self, request):
        ws = await self.accept(request)
        subscriber = Subscriber(ws)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                if msg.data == "ping":
                    subscriber.send("pong")
                    continue

                req = json.loads(msg.data)
                op = req.get("op")
                for arg in req.get("args", []):
                    subscriber.send(dumps({"event": op, "arg": arg}))
                    if arg.get("channel") != "books":
                        continue
                    if op == "subscribe":
                        subscriber.send(self.market.books_message(self.market.bids,
                                                                  self.market.asks, -1))
                        self.books_subscribers[ws] = subscriber
                    elif op == "unsubscribe":
                        self.books_subscribers.pop(ws, None)
                        self.resubscribes += 1
        finally:
            self.books_subscribers.pop(ws, None)
            subscriber.close()
            log.info("%s disconnected", request.path)
        return ws

    async def sbe_ws(self, request):
        # The engine doesn't send its own heartbeat on SBE, it only answers server pings
        ws = await self.accept(request, heartbeat=15)
        subscriber = Subscriber(ws)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                req = json.loads(msg.data)
                for arg in req.get("args", []):
                    if req.get("op") == "subscribe" and arg.get("channel") == "bbo-tbt":
                        subscriber.send(self.market.bbo_message())
                        self.sbe_subscribers[ws] = subscriber
        finally:
            self.sbe_subscribers.pop(ws, None)
            subscriber.close()
            log.info("%s disconnected", request.path)
        return ws

    def engine_connected(self):
        return bool(self.private_sockets) and bool(self.books_subscribers or self.sbe_subscribers)

    async def wait_for_engine(self, args):
        self.move_sent_ns = None
        log.info("Waiting for the engine to log in and subscribe")
        while not self.engine_connected():
            await asyncio.sleep(0.1)
        # Let the engine's startup order round trip finish first
        await asyncio.sleep(args.warmup)
        log.info("Starting %s feed", args.mode)

    async def run_feed(self, args):
        await self.wait_for_engine(args)

        if args.mode == "paced":
            for _ in range(args.count) if args.count else itertools.count():
                if not self.engine_connected():
                    await self.wait_for_engine(args)
                self.publish(*self.market.move(), moved=True)
                try:
                    await asyncio.wait_for(self.order_seen.wait(), 1.0)
                except TimeoutError:
                    self.timeouts += 1
                    self.move_sent_ns = None
                await asyncio.sleep(args.interval)
            return

        loop = asyncio.get_running_loop()
        start = loop.time()
        next_move = start + args.interval
        churn_sent = 0
        while not args.count or self.moves < args.count:
            if not self.engine_connected():
                await self.wait_for_engine(args)
                start = loop.time()
                next_move = start + args.interval
                churn_sent = 0
            now = loop.time()
            due = int((now - start) * args.rate) - churn_sent
            for _ in range(due):
                self.publish(*self.market.churn(), moved=False)
            churn_sent += due
            if now >= next_move:
                self.publish(*self.market.move(), moved=True)
                next_move += args.interval
            await asyncio.sleep(0.001)

    def summary(self):
        rtt = sorted(self.rtt_us)
        p50 = rtt[len(rtt) // 2] if rtt else 0
        p99 = rtt[int(len(rtt) * 0.99)] if rtt else 0
        return (f"moves={self.moves} matched={self.matched} timeouts={self.timeouts} "
                f"churned={self.churned} resubscribes={self.resubscribes} "
                f"harness_rtt_us p50={p50:.0f} p99={p99:.0f}")

    async def report(self):
        while True:
            await asyncio.sleep(10)
            log.info("%s", self.summary())


async def main(args):
    exchange = Exchange(Market(args.seed))
    app = web.Application()
    app.add_routes(exchange.routes())
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(CERT_DIR / "server.crt", CERT_DIR / "server.key")
    for port in (REST_PORT, WS_PORT):
        await web.TCPSite(runner, "0.0.0.0", port, ssl_context=ssl_context).start()
    log.info("Listening on %d (REST) and %d (WS)", REST_PORT, WS_PORT)

    reporter = asyncio.create_task(exchange.report())
    try:
        await exchange.run_feed(args)
        log.info("Feed finished: %s", exchange.summary())
        # Keep acking orders until the engine is stopped
        await asyncio.Event().wait()
    finally:
        reporter.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("paced", "firehose"), default="paced")
    # Each move costs two amends against the engine's 60 per 2s limit
    parser.add_argument("--interval", type=float, default=0.1, help="seconds between price moves")
    parser.add_argument("--rate", type=float, default=1000.0, help="firehose churn messages/s")
    parser.add_argument("--count", type=int, default=0, help="price moves to send, 0 for no limit")
    parser.add_argument("--warmup", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main(parser.parse_args()))
    except KeyboardInterrupt:
        pass
