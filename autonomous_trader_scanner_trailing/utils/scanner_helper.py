# utils/scanner_helper.py
import os
from typing import List, Tuple
from utils.data_fetchers import save_runtime_whitelist
from utils.market_data_cryptofeed import get_global_hub

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
        out_syms = [s for s,_ in fallback[:top_n]]
        save_runtime_whitelist(out_syms)
        return out_syms

    rows.sort(key=lambda x: x[1], reverse=True)
    out = [s for s,_,_ in rows[:top_n]]
    save_runtime_whitelist(out)
    return out
