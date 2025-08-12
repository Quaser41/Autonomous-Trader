
import os, json

BASE = os.path.dirname(os.path.dirname(__file__))
PERF_PATH = os.path.join(BASE, "data", "performance", "symbol_pnl.json")
BLACKLIST = {"ZRO/USD", "STG/USD", "PUMP/USD", "LTC/USDT"}


def load_crypto_whitelist():
    cfg = json.load(open(os.path.join(BASE, "config", "config.json"), "r"))
    wl = cfg.get("whitelist", [])
    # overlay with runtime list if exists
    rt = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
    if os.path.exists(rt):
        try:
            rw = json.load(open(rt, "r"))
            if isinstance(rw, list) and rw:
                wl = rw
        except Exception:
            pass

    # remove statically blacklisted symbols
    wl = [s for s in wl if s not in BLACKLIST]

    # drop symbols with negative cumulative PnL
    if os.path.exists(PERF_PATH):
        try:
            pnl = json.load(open(PERF_PATH, "r"))
            wl = [s for s in wl if pnl.get(s, 0.0) >= 0.0]
        except Exception:
            pass

    return wl

def save_runtime_whitelist(symbols):
    path = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(symbols, f, indent=2)
