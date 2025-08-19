
import os, json

from typing import Any

BASE = os.path.dirname(os.path.dirname(__file__))
PERF_PATH = os.path.join(BASE, "data", "performance", "symbol_pnl.json")
RUNTIME_PATH = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
BLACKLIST = {"ZRO/USD", "STG/USD", "PUMP/USD", "LTC/USDT"}

# Load configuration once at module import to avoid re-reading the file
_CONFIG_PATH = os.path.join(BASE, "config", "config.json")
try:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _cfg_file:
        _CONFIG = json.load(_cfg_file)
except Exception:
    _CONFIG = {}


def load_crypto_whitelist():
    wl = _CONFIG.get("whitelist", [])
    pnl_threshold = _CONFIG.get("whitelist_min_pnl", -1.0)

    def _expectancy(val: Any) -> float:
        if isinstance(val, list) and val:
            return sum(val) / len(val)
        if isinstance(val, (int, float)):
            return float(val)
        return 0.0

    # overlay with runtime list if exists
    if os.path.exists(RUNTIME_PATH):
        try:
            rw = json.load(open(RUNTIME_PATH, "r"))
            if isinstance(rw, list) and rw:
                wl = rw
        except Exception:
            pass

    # remove statically blacklisted symbols
    wl = [s for s in wl if s not in BLACKLIST]

    # drop symbols with negative expectancy and sort by expectancy
    if os.path.exists(PERF_PATH):
        try:
            pnl = json.load(open(PERF_PATH, "r"))
            wl = [s for s in wl if _expectancy(pnl.get(s)) >= max(0.0, pnl_threshold)]
            wl.sort(key=lambda s: _expectancy(pnl.get(s)), reverse=True)
        except Exception:
            pass

    return wl

def save_runtime_whitelist(symbols):
    from .trending_feed import update_runtime_whitelist
    update_runtime_whitelist(symbols)
