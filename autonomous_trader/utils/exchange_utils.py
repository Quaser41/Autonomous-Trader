# utils/exchange_utils.py
import os, json
BASE = os.path.dirname(os.path.dirname(__file__))
CFG_PATH = os.path.join(BASE, "config", "config.json")
with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

# Lazy ccxt import only if needed
_ccxt = None
def _get_ccxt():
    global _ccxt
    if _ccxt is None:
        import ccxt
        _ccxt = ccxt
    return _ccxt

class DummyExchange:
    """
    Exchange shim for cryptofeed-only mode.
    Provides fetch_ohlcv via CryptoFeedHub synthetic aggregation.
    """
    def load_markets(self):
        # Not used in cryptofeed mode
        return {}

    def fetch_ohlcv(self, symbol, timeframe="5m", limit=200):
        try:
            from utils.market_data_cryptofeed import get_global_hub
            hub = get_global_hub()
            if not hub:
                return []
            df = hub.ohlcv_df(symbol, timeframe=timeframe, limit=limit)
            if df is None or df.empty:
                return []
            # Return ccxt-style list of lists
            return df[["time","open","high","low","close","volume"]].values.tolist()
        except Exception:
            return []

def get_exchange():
    # If using cryptofeed for data, return a dummy that never calls REST
    name = (CFG.get("exchange") or "").lower()
    if name == "cryptofeed":
        return DummyExchange()

    # Otherwise create a normal ccxt exchange
    ccxt = _get_ccxt()
    ex_class = getattr(ccxt, name if name else "binance")
    exchange = ex_class({"enableRateLimit": CFG.get("rate_limit", True)})
    return exchange

def filter_supported_symbols(exchange, symbols):
    """
    Filters the provided symbols for those supported by the exchange.
    In cryptofeed (DummyExchange) mode, skips ccxt REST entirely and just
    does a basic format check.
    """
    if isinstance(exchange, DummyExchange):
        # Basic sanity check: keep only symbols that look like "XXX/YYY"
        return [s for s in symbols if "/" in s]

    # ccxt path (calls REST)
    markets = exchange.load_markets()
    ok = []
    for s in symbols:
        if s in markets and markets[s].get("spot", False):
            ok.append(s)
    return ok
