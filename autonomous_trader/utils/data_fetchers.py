
import os, json

BASE = os.path.dirname(os.path.dirname(__file__))
PERF_PATH = os.path.join(BASE, "data", "performance", "symbol_pnl.json")
BLACKLIST = {"ZRO/USD", "STG/USD", "PUMP/USD", "LTC/USDT"}


def load_crypto_whitelist():
    cfg_path = os.path.join(BASE, "config", "config.json")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    wl = cfg.get("whitelist", [])
    # overlay with runtime list if exists
    rt = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
    if os.path.exists(rt):
        try:
            with open(rt, "r", encoding="utf-8") as f:
                rw = json.load(f)
            if isinstance(rw, list) and rw:
                wl = rw
        except Exception:
            pass

    # remove statically blacklisted symbols
    wl = [s for s in wl if s not in BLACKLIST]

    # drop symbols with negative cumulative PnL
    if os.path.exists(PERF_PATH):
        try:
            with open(PERF_PATH, "r", encoding="utf-8") as f:
                pnl = json.load(f)
            wl = [s for s in wl if pnl.get(s, 0.0) >= 0.0]
        except Exception:
            pass

    return wl

def save_runtime_whitelist(symbols):
    path = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(symbols, f, indent=2)
