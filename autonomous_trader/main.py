import os, json, time, threading, asyncio, sys
import pandas as pd

# --- Windows: use selector loop (more compatible with websockets)
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from utils.logger import Notifier, log_trade, log_status, log_equity
from utils.trade_executor import PaperBroker
from utils.data_fetchers import load_crypto_whitelist
from utils.exchange_utils import get_exchange, filter_supported_symbols
from strategies.ai_combo_strategy import generate_signal
from utils.scanner_helper import run_scanner
from utils.trending_feed import start_trending_feed

BASE = os.path.dirname(__file__)
with open(os.path.join(BASE, "config", "config.json"), "r") as f:
    CFG = json.load(f)

EXCHANGE = get_exchange()

# ---------- Live hub selection (Kraken-only or legacy cryptofeed)
HAS_CF = True
_feed_hub = None

def _symbols_from_cfg_as_slash(cfg):
    df = cfg.get("data_feeds", {})
    syms = df.get("symbols") or cfg.get("trade_universe", []) or ["BTC/USDT", "ETH/USDT"]
    # normalize to "BASE/USDT" with slash
    out = []
    for s in syms:
        s = s.upper().replace("-", "/")
        if "/" not in s:
            s = s + "/USDT"
        out.append(s)
    return out

try:
    if (CFG.get("market_data") or "").lower() == "kraken_ws":
        # Use our new Kraken-only hub
        from utils.market_data_kraken import KrakenHub, register_global_hub
        kr_syms = _symbols_from_cfg_as_slash(CFG)
        _feed_hub = KrakenHub(symbols=kr_syms)
        register_global_hub(_feed_hub)
        print(f"[BOOT] Kraken hub enabled for: {', '.join(kr_syms)}")
    else:
        # Fallback: legacy multi-exchange cryptofeed hub
        from utils.market_data_cryptofeed import CryptoFeedHub, register_global_hub
        def _mk_feed_cfg(cfg):
            df = cfg.get("data_feeds", {})
            symbols_src = df.get("symbols") or [s.replace("/", "-") for s in cfg.get("trade_universe", [])]
            if not symbols_src:
                symbols_src = ["BTC-USDT", "ETH-USDT"]
            exchanges_src = df.get("exchanges") or [ (cfg.get("exchange","BINANCE")).upper() ]
            channels_src = df.get("channels") or ["ticker", "trades"]
            return {"data_feeds": {"exchanges": exchanges_src, "channels": channels_src, "symbols": symbols_src, "normalize_symbols": True}}
        _feed_hub = CryptoFeedHub(_mk_feed_cfg(CFG))
        register_global_hub(_feed_hub)
except Exception as e:
    print("[BOOT] Live market data hub unavailable:", e)
    HAS_CF = False

# ---------- helpers
def fetch_candles(symbol, timeframe="5m", limit=200):
    df_live = pd.DataFrame()
    # 1) Try live (synthetic) candles from the hub
    if HAS_CF and _feed_hub is not None:
        try:
            tmp = _feed_hub.ohlcv_df(symbol, timeframe=timeframe, limit=limit)
            if tmp is not None and not tmp.empty:
                df_live = tmp[["time","open","high","low","close","volume"]].copy()
        except Exception as e:
            print(f"[WARN] ohlcv_df error {symbol}: {e}")

    # 2) If we have too few bars, backfill from REST (Kraken via ccxt)
    need_backfill = df_live.empty or len(df_live) < min(100, limit // 2)
    df_rest = pd.DataFrame()
    if need_backfill:
        try:
            ohlcv = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if ohlcv:
                df_rest = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])
        except Exception as e:
            print(f"[WARN] fetch_ohlcv error {symbol}: {e}")

    # 3) Merge (REST first for history, live last to overwrite newest points)
    if not df_live.empty and not df_rest.empty:
        df = pd.concat([df_rest, df_live], ignore_index=True)
        df = df.drop_duplicates(subset=["time"], keep="last").sort_values("time")
        return df.tail(limit).reset_index(drop=True)
    if not df_live.empty:
        return df_live.tail(limit).reset_index(drop=True)
    if not df_rest.empty:
        return df_rest.tail(limit).reset_index(drop=True)

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

# ---------- trading loop (background thread)
def trading_loop():
    n = Notifier(CFG)
    broker = PaperBroker()
    exit_cfg = get_exit_cfg()

    # initial whitelist
    try:
        wl = filter_supported_symbols(EXCHANGE, load_crypto_whitelist())
    except Exception as e:
        print("[BOOT] load_markets/whitelist failed, falling back:", e)
        wl = []
    if not wl:
        wl = _symbols_from_cfg_as_slash(CFG)[:5]  # start with configured symbols if none
    print(f"[BOOT] Starting with {len(wl)} symbols: {', '.join(wl[:10])}{'…' if len(wl)>10 else ''}")

    last_scan_ts = 0.0
    prices = {}
    heartbeat_every = max(10, CFG.get("logging", {}).get("print_status_every_sec", 30))
    last_beat = 0
    debug_printed = set()

    while True:
        last_scan_ts = maybe_run_scanner(last_scan_ts)

        try:
            scanner_syms = filter_supported_symbols(EXCHANGE, load_crypto_whitelist()) or []
            trending_path = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
            trending_syms = []
            if os.path.exists(trending_path):
                try:
                    with open(trending_path, "r", encoding="utf-8") as f:
                        trending_syms = json.load(f)
                except Exception:
                    pass

            merged_syms, seen = [], set()
            for s in trending_syms + scanner_syms:
                if s not in seen:
                    seen.add(s); merged_syms.append(s)

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

            if sym not in debug_printed:
                if df.empty:
                    print(f"[DATA] {sym}: no candles yet (live+rest)")
                else:
                    closes = df["close"].tail(3).round(6).tolist()
                    print(f"[DATA] {sym}: {len(df)} candles | last closes {closes}")
                debug_printed.add(sym)


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

                # --- TEMP: simple momentum trigger so we can test entries
                try:
                    test_momo = float(CFG.get("debug", {}).get("test_momo_entry_pct", 0.0))
                except Exception:
                    test_momo = 0.0

                if sig.get("signal") == "HOLD" and test_momo > 0 and len(df) >= 4:
                    c = df["close"]
                    momo = (float(c.iloc[-1]) / float(c.iloc[-4])) - 1.0  # ~3 bars lookback
                    print(f"[TEST] {sym} momentum={momo:.3%} (threshold={test_momo:.3%})")  # <-- add this line

                    if momo >= test_momo:
                        sig = {"signal": "BUY", "score": momo}
                    elif momo <= -test_momo:
                        sig = {"signal": "HOLD", "score": momo}
                # --- END TEMP

                print(f"[SIG] {sym} -> {sig}")  # <--- add this single line
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

def run():
    print("[BOOT] Launching bot…")
    print(f"[BOOT] Exchange: {CFG.get('exchange')} | Timeframe: {CFG.get('timeframe_crypto','5m')}")

    # Start trending feed (background thread)
    start_trending_feed(interval_min=5)

    # Start trading loop in a non-daemon thread
    t = threading.Thread(target=trading_loop, daemon=False)
    t.start()

    # Run the live hub in the main thread (blocking)
    if HAS_CF and _feed_hub is not None:
        try:
            _feed_hub.run()
        except KeyboardInterrupt:
            print("\n[BOOT] Shutting down…")

    # If hub returned (or not present), keep process alive so trading thread runs
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[BOOT] Shutting down…")

if __name__ == "__main__":
    run()
