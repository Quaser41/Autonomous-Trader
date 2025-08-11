# utils/scanner_helper.py
import os
import json
from typing import List, Tuple
from utils.data_fetchers import save_runtime_whitelist
from utils.market_data_cryptofeed import get_global_hub

BASE = os.path.dirname(os.path.dirname(__file__))
TREND_PATH = os.path.join(BASE, "data", "runtime", "runtime_whitelist.json")

def _to_quote_vol_usd(price: float, base_vol: float) -> float:
    if price is None or base_vol is None:
        return 0.0
    try:
        return float(price) * float(base_vol)
    except Exception:
        return 0.0

def run_scanner(cfg) -> List[str]:
    """
    Build a runtime whitelist using only cryptofeed live data.
    - Pull symbols seen by the hub
    - Rank by quote volume (price * base_volume_24h)
    - Filter by ATR% floor (cfg['scanner']['min_atr_pct'])
    """
    hub = get_global_hub()
    if not hub:
        save_runtime_whitelist([])
        return []

    sc = cfg.get("scanner", {})
    min_qv = float(sc.get("min_24h_usdt_volume", 10_000_000))
    min_atr_pct = float(sc.get("min_atr_pct", 0.8))
    top_n = int(sc.get("max_symbols", 20))
    trend_n = int(sc.get("max_trending_symbols", top_n // 2))

    trending: List[str] = []
    if os.path.exists(TREND_PATH):
        try:
            with open(TREND_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for s in data:
                        if isinstance(s, str):
                            s = s.strip().upper()
                            if s and s not in trending:
                                trending.append(s)
        except Exception:
            pass
    trending = trending[:trend_n]

    rows: List[Tuple[str, float, float]] = []  # (symbol, qv_usd, atr_pct)
    for sym in hub.list_symbols():
        price, base_vol_24h = hub.snapshot(sym)
        if price is None or base_vol_24h is None:
            continue
        qv = _to_quote_vol_usd(price, base_vol_24h)
        if qv < min_qv:
            continue
        atrp = hub.atr_pct(sym) or 0.0
        if atrp < min_atr_pct:
            continue
        rows.append((sym, qv, atrp))

    # If nothing passed ATR filter, soften: volume-only
    if not rows:
        fallback = []
        for sym in hub.list_symbols():
            price, base_vol_24h = hub.snapshot(sym)
            qv = _to_quote_vol_usd(price or 0.0, base_vol_24h or 0.0)
            if qv >= min_qv:
                fallback.append((sym, qv))
        fallback.sort(key=lambda x: x[1], reverse=True)
        max_volume = max(0, top_n - len(trending))
        vol_syms = [s for s,_ in fallback[:max_volume]]
        out_syms: List[str] = []
        out_syms.extend(trending)
        for s in vol_syms:
            if s not in out_syms:
                out_syms.append(s)
        out_syms = out_syms[:top_n]
        save_runtime_whitelist(out_syms)
        return out_syms

    rows.sort(key=lambda x: x[1], reverse=True)
    max_volume = max(0, top_n - len(trending))
    vol_syms = [s for s,_,_ in rows[:max_volume]]
    out: List[str] = []
    out.extend(trending)
    for s in vol_syms:
        if s not in out:
            out.append(s)
        if len(out) >= top_n:
            break
    save_runtime_whitelist(out)
    return out
