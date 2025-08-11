import os
import json
import time
import threading
import asyncio
from typing import List

from utils.market_data_cryptofeed import (
    CryptoFeedHub,
    register_global_hub,
    get_global_hub,
)
from utils.trending_feed import start_trending_feed
from utils.data_fetchers import load_crypto_whitelist
from utils.trade_executor import PaperBroker
from strategies import ai_combo_strategy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE_DIR, "config", "config.json")
CFG = json.load(open(CFG_PATH, "r"))


def _start_feed() -> CryptoFeedHub:
    hub = CryptoFeedHub(CFG)
    register_global_hub(hub)
    t = threading.Thread(target=hub.run, daemon=True)
    t.start()
    try:
        asyncio.run(hub.wait_ready())
    except Exception:
        pass
    return hub


def _scan_symbols(broker: PaperBroker, symbols: List[str]):
    hub = get_global_hub()
    if not hub:
        return
    timeframe = CFG.get("timeframe_crypto", "5m")
    exits_cfg = CFG.get("exits", {})
    trail_cfg = CFG.get("trailing_stop", {})
    for sym in symbols:
        df = hub.ohlcv_df(sym, timeframe=timeframe, limit=200)
        if df is None or df.empty or len(df) < 50:
            continue
        sig = ai_combo_strategy.generate_signal(df, CFG)
        if sig.get("signal") == "BUY":
            price = float(df["close"].iloc[-1])
            meta = {
                "stop_loss_pct": exits_cfg.get("stop_loss_pct", 0.01),
                "take_profit_pct": exits_cfg.get("take_profit_pct", 0.02),
                "breakeven_trigger_pct": trail_cfg.get("breakeven_pct", 0.005),
                "trailing_stop_pct": trail_cfg.get("trail_pct", 0.006),
                "score": sig.get("score"),
            }
            broker.buy(sym, price, meta)

    # check exits
    for sym in list(broker.positions.keys()):
        price, _ = hub.snapshot(sym)
        if price is None:
            continue
        should, _ = broker.should_exit(sym, price)
        if should:
            broker.sell(sym, price)


def main():
    _start_feed()
    start_trending_feed()
    broker = PaperBroker()
    while True:
        symbols = load_crypto_whitelist()
        _scan_symbols(broker, symbols)
        time.sleep(60)


if __name__ == "__main__":
    main()
