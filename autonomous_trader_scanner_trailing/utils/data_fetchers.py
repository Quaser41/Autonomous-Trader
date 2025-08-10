
import os, json

BASE = os.path.dirname(os.path.dirname(__file__))

def load_crypto_whitelist():
    cfg = json.load(open(os.path.join(BASE, "config", "config.json"), "r"))
    wl = cfg.get("whitelist", [])
    # overlay with runtime list if exists
    rt = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
    if os.path.exists(rt):
        try:
            rw = json.load(open(rt,"r"))
            if isinstance(rw, list) and rw:
                wl = rw
        except Exception:
            pass
    return wl

def save_runtime_whitelist(symbols):
    path = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(symbols, f, indent=2)
