# test_kraken_ws.py
import asyncio, json, time, math
from collections import defaultdict

# Kraken public WS endpoint
WS_URL = "wss://ws.kraken.com"

# Use Kraken's pair codes (BTC is XBT on Kraken).
# Keep it to symbols Kraken actually lists.
PAIRS = ["XBT/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT"]

# simple rolling OHLCV buckets (1-minute) from trades just to prove we can build candles
buckets = defaultdict(lambda: {})  # buckets[pair][minute] = [o,h,l,c,vol]

def update_candle(pair, price, size, ts):
    minute = int(ts // 60)
    b = buckets[pair].get(minute)
    if b is None:
        buckets[pair][minute] = [price, price, price, price, size]
    else:
        o, h, l, _, v = b
        h = max(h, price)
        l = min(l, price)
        c = price
        v += size
        buckets[pair][minute] = [o, h, l, c, v]

async def run():
    import websockets  # provided by your venv already
    print("[TEST] Connecting to Kraken WS…")
    async with websockets.connect(WS_URL, ping_interval=15, ping_timeout=15) as ws:
        # Subscribe to ticker + trade
        sub_ticker = {
            "event": "subscribe",
            "pair": PAIRS,
            "subscription": {"name": "ticker"}
        }
        sub_trade = {
            "event": "subscribe",
            "pair": PAIRS,
            "subscription": {"name": "trade"}
        }
        await ws.send(json.dumps(sub_ticker))
        await ws.send(json.dumps(sub_trade))

        tick_counts = defaultdict(int)
        trade_counts = defaultdict(int)
        start = time.time()

        while True:
            raw = await ws.recv()
            msg = json.loads(raw)

            # Status / heartbeats
            if isinstance(msg, dict):
                et = msg.get("event")
                if et == "subscriptionStatus":
                    status = msg.get("status")
                    channel = msg.get("subscription", {}).get("name")
                    pair = msg.get("pair")
                    print(f"[SUB] {channel} {pair}: {status}")
                elif et in ("systemStatus", "heartbeat"):
                    pass
                continue

            # Data frames are lists
            if isinstance(msg, list) and len(msg) >= 4:
                channel = msg[-2]   # "ticker" or "trade"
                pair = msg[-1]
                payload = msg[1]

                if channel == "ticker":
                    # payload is a dict; last price in 'c'[0]
                    try:
                        price = float(payload["c"][0])
                        tick_counts[pair] += 1
                        # print a throttled line per pair
                        if tick_counts[pair] % 10 == 0:
                            print(f"[TICKER] {pair} last={price}")
                    except Exception:
                        pass

                elif channel == "trade":
                    # payload is a list of trades: [price, volume, time, side, orderType, misc]
                    try:
                        for t in payload:
                            price = float(t[0]); size = float(t[1]); ts = float(t[2])
                            trade_counts[pair] += 1
                            update_candle(pair, price, size, ts)
                    except Exception:
                        pass

            # Every ~15s print a one-line summary
            if time.time() - start >= 15:
                parts = []
                for p in PAIRS:
                    parts.append(f"{p}: ticks={tick_counts[p]}, trades={trade_counts[p]}")
                print("[SUM] " + " | ".join(parts))
                start = time.time()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        # On exit, dump the most recent candle per pair so you can see it worked
        print("\n[TEST] Stopping. Latest 1m OHLCV (if any):")
        for p, minutes in buckets.items():
            if not minutes:
                continue
            last_min = sorted(minutes.keys())[-1]
            o,h,l,c,v = minutes[last_min]
            print(f"  {p} @ {last_min*60:.0f}s -> O={o} H={h} L={l} C={c} V={v}")
