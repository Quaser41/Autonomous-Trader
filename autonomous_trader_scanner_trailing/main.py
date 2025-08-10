import os, json, time, threading, asyncio
import pandas as pd

from utils.logger import Notifier, log_trade, log_status, log_equity
from utils.trade_executor import PaperBroker
from utils.data_fetchers import load_crypto_whitelist
from utils.exchange_utils import get_exchange, filter_supported_symbols
from strategies.ai_combo_strategy import generate_signal
from utils.scanner_helper import run_scanner

# ---- Try cryptofeed hub, but don’t die if it’s missing
HAS_CF = True
try:
    from utils.market_data_cryptofeed import CryptoFeedHub, slash_to_norm, register_global_hub
except Exception as e:
    print("[BOOT] Live market data (cryptofeed) unavailable:", e)
    HAS_CF = False
    def slash_to_norm(s: str) -> str:
        return s.replace("/", "-")  # fallback normalizer
    def register_global_hub(_):  # no-op
        pass

BASE = os.path.dirname(__file__)
with open(os.path.join(BASE, "config", "config.json"), "r") as f:
    CFG = json.load(f)

EXCHANGE = get_exchange()

# ---------- Cryptofeed bootstrap
def _mk_feed_cfg(cfg):
    df = cfg.get("data_feeds", {})
    symbols_src = df.get("symbols") or [slash_to_norm(s) for s in cfg.get("trade_universe", [])]
    if not symbols_src:
        symbols_src = ["BTC-USDT", "ETH-USDT"]

    exchanges_src = df.get("exchanges")
    if not exchanges_src:
        ex = cfg.get("exchange", "BINANCE")
        exchanges_src = [ex.upper()]

    channels_src = df.get("channels") or ["ticker", "trades"]

    return {
        "data_feeds": {
            "exchanges": exchanges_src,
            "channels": channels_src,
            "symbols": symbols_src,
            "normalize_symbols": True
        }
    }

_feed_hub = None
if HAS_CF:
    _feed_hub = CryptoFeedHub(_mk_feed_cfg(CFG))
    register_global_hub(_feed_hub)

def _run_feed_in_bg():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_feed_hub.run())

def start_market_data():
    t = threading.Thread(target=_run_feed_in_bg, daemon=True)
    t.start()

from utils.trending_feed import start_trending_feed

# ---------- helpers
def fetch_candles(symbol, timeframe="5m", limit=200):
    if HAS_CF and _feed_hub is not None:
        try:
            df = _feed_hub.ohlcv_df(symbol, timeframe=timeframe, limit=limit)
            if df is not None and not df.empty:
                return df[["time","open","high","low","close","volume"]].copy()
        except Exception as e:
            print(f"[WARN] ohlcv_df error {symbol}: {e}")

    for _ in range(2):
        try:
            ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
        except Exception as e:
            print(f"[WARN] fetch_ohlcv error {symbol}: {e}")
            time.sleep(1)
    return pd.DataFrame()

def maybe_run_scanner(last_scan_ts):
    now = time.time()
    refresh_min = CFG.get("scanner", {}).get("refresh_minutes", 90)
    if (now - last_scan_ts) >= (refresh_min * 60):
        print(f"[SCANNER] Running symbol scanner (every {refresh_min} min)…")
        try:
            syms = run_scanner(CFG)
            print(f"[SCANNER] Updated runtime whitelist with {len(syms)} symbols.")
        except Exception as e:
            print("[SCANNER] failed:", e)
        return now
    return last_scan_ts

def compute_unrealized_pnl(broker, prices):
    pnl = 0.0
    for sym, pos in broker.positions.items():
        price = prices.get(sym)
        if price is None:
            continue
        pnl += pos["qty"] * (price - pos["entry"])
    return pnl

def get_exit_cfg():
    exits = CFG.get("exits", {})
    trailing = CFG.get("trailing_stop", {})
    return {
        "take_profit_pct": exits.get("take_profit_pct"),
        "stop_loss_pct": exits.get("stop_loss_pct"),
        "breakeven_trigger_pct": trailing.get("breakeven_pct"),
        "trailing_stop_pct": trailing.get("trail_pct"),
        "trailing_enable": trailing.get("enable", True),
        "activate_profit_pct": trailing.get("activate_profit_pct"),
    }

def run():
    print("[BOOT] Launching bot…")
    print(f"[BOOT] Exchange: {CFG.get('exchange')} | Timeframe: {CFG.get('timeframe_crypto','5m')}")
    n = Notifier(CFG)
    broker = PaperBroker()
    exit_cfg = get_exit_cfg()

    # Start live market data and trending feed ONCE here
    if HAS_CF and _feed_hub is not None:
        start_market_data()
        start_trending_feed(interval_min=5)
        try:
            asyncio.run(_feed_hub.wait_ready(timeout=15.0))  # give feeds time to warm up
        except RuntimeError:
            pass

    try:
        wl = filter_supported_symbols(EXCHANGE, load_crypto_whitelist())
    except Exception as e:
        print("[BOOT] load_markets/whitelist failed, falling back:", e)
        wl = []

    if not wl:
        wl = ["BTC/USDT", "ETH/USDT"]
    print(f"[BOOT] Starting with {len(wl)} symbols: {', '.join(wl[:10])}{'…' if len(wl)>10 else ''}")

    last_scan_ts = 0.0
    prices = {}
    heartbeat_every = max(10, CFG.get("logging", {}).get("print_status_every_sec", 30))
    last_beat = 0

    while True:
        last_scan_ts = maybe_run_scanner(last_scan_ts)

        try:
            # Scanner results
            scanner_syms = filter_supported_symbols(EXCHANGE, load_crypto_whitelist()) or []

            # Trending results
            trending_path = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
            trending_syms = []
            if os.path.exists(trending_path):
                try:
                    with open(trending_path, "r", encoding="utf-8") as f:
                        trending_syms = json.load(f)
                except Exception:
                    pass

            # Merge and dedupe, trending first
            merged_syms = []
            seen = set()
            for s in trending_syms + scanner_syms:
                if s not in seen:
                    seen.add(s)
                    merged_syms.append(s)

            max_syms = CFG.get("scanner", {}).get("max_symbols", 20)
            wl = merged_syms[:max_syms] if merged_syms else wl

            print(f"[WL] Active trading list ({len(wl)}): {', '.join(wl)}")

        except Exception as e:
            print("[LOOP] whitelist merge failed:", e)

        processed = 0
        for sym in wl[:50]:
            live_price = None
            if HAS_CF and _feed_hub is not None:
                lp, _vol = _feed_hub.snapshot(sym)
                live_price = lp

            df = fetch_candles(sym, CFG.get("timeframe_crypto", "5m"))

            price = None
            if live_price is not None:
                price = float(live_price)
            elif not df.empty:
                price = float(df["close"].iloc[-1])

            if price is None:
                continue

            prices[sym] = price
            processed += 1

            if sym in broker.positions:
                should_exit, reason = broker.should_exit(sym, price)
                if should_exit:
                    r = broker.sell(sym, price)
                    if r:
                        log_trade("SELL", sym, 0, price, {"pnl": r["pnl"], "reason": reason})
                        n.send(f"SELL {sym} @ {price:.4f} | PnL: {r['pnl']:.2f} ({reason})")
                continue

            if not df.empty and broker.can_open():
                sig = generate_signal(df, CFG)
                if sig.get("signal") == "BUY":
                    o = broker.buy(sym, price, {
                        "score": sig.get("score"),
                        "take_profit_pct": exit_cfg["take_profit_pct"],
                        "stop_loss_pct": exit_cfg["stop_loss_pct"],
                        "breakeven_trigger_pct": exit_cfg["breakeven_trigger_pct"],
                        "trailing_stop_pct": exit_cfg["trailing_stop_pct"],
                        "trailing_enable": exit_cfg["trailing_enable"],
                        "activate_profit_pct": exit_cfg["activate_profit_pct"],
                    })
                    if o:
                        log_trade("BUY", sym, o["qty"], price, {"score": sig.get("score")})
                        n.send(f"BUY {sym} @ {price:.4f} [score={sig.get('score', 0):.2f}]")

        now = time.time()
        if now - last_beat >= heartbeat_every:
            unreal = compute_unrealized_pnl(broker, prices)
            mv = sum([pos["qty"] * prices.get(sym, pos["entry"]) for sym, pos in broker.positions.items()])
            cost = sum([pos["qty"] * pos["entry"] for pos in broker.positions.values()])
            equity = broker.balance + mv - cost
            log_status(broker.balance, len(broker.positions), unreal)
            log_equity(now, broker.balance, equity)
            print(f"[HB] cash={broker.balance:.2f} open={len(broker.positions)} unreal={unreal:.2f} scanned={processed}")
            last_beat = now

        time.sleep(10)

if __name__ == "__main__":
    run()
