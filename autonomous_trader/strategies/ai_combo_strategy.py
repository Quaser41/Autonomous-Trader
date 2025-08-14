import numpy as np
import pandas as pd

# ---------- indicators
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = up / (down.replace(0, 1e-12))
    return 100 - (100 / (1 + rs))

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_close = c.shift(1)
    tr = pd.concat([(h - l).abs(),
                    (h - prev_close).abs(),
                    (l - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

# ---------- strategy
def generate_signal(df: pd.DataFrame, cfg) -> dict:
    if len(df) < 60:
        return {"signal": "HOLD", "score": 0.0}

    df = df.copy()

    # core indicators
    df["ema20"]  = ema(df["close"], 20)
    df["ema50"]  = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"]    = rsi(df["close"], 14)
    df["atr"]    = atr(df, 14)
    df["atr_pct"] = (df["atr"] / df["close"]).fillna(0.0)
    df["vol_ma"] = df["volume"].rolling(20).mean()

    macd_line, signal_line, hist = macd(df["close"], 12, 26, 9)
    df["macd_hist"] = hist

    # breakout levels
    lookback = 20
    df["hh"] = df["high"].rolling(lookback).max()
    df["ll"] = df["low"].rolling(lookback).min()

    last   = df.iloc[-1]
    prev   = df.iloc[-2]

    # ---- gates
    # volatility gate: allow between ~0.2% and ~6% (tune if needed)
    atr_min = 0.002   # 0.2%
    atr_max = 0.06    # 6%
    if not (atr_min <= float(last["atr_pct"]) <= atr_max):
        return {"signal": "HOLD", "score": 0.0}

    # trend filter: ema50 > ema200 and both rising
    trend_up = (last["ema50"] > last["ema200"]) and (last["ema50"] > prev["ema50"]) and (last["ema200"] >= prev["ema200"])

    # momentum/volume confirm
    macd_flip_up = (prev["macd_hist"] <= 0) and (last["macd_hist"] > 0)
    rsi_ok = 50 <= last["rsi"] <= 70
    vol_ok = pd.notna(last["vol_ma"]) and last["volume"] > last["vol_ma"] * 1.2
    if not vol_ok:
        return {"signal": "HOLD", "score": 0.0}

    # breakout: close above recent 20-bar high with tiny buffer
    breakout = (last["close"] > float(last["hh"]) * 1.001) if pd.notna(last["hh"]) else False

    # assemble score (0..1.5-ish, then clamp)
    score = 0.0
    if trend_up:        score += 0.6
    if macd_flip_up:    score += 0.5
    if rsi_ok:          score += 0.2
    if breakout:        score += 0.4
    if vol_ok:         score += 0.2

    # soften if price is far above ema20 (reduce buying extended moves)
    stretch = (last["close"] - last["ema20"]) / last["close"]
    if stretch > 0.02:
        score -= min(0.3, float(stretch) * 5)  # penalize heavy extension

    score = float(max(0.0, min(1.5, score)))

    min_score = cfg.get("strategy", {}).get("buy_score_threshold", 1.5)
    if trend_up and (macd_flip_up or breakout) and score >= min_score:
        atr_pct = float(last["atr_pct"])
        risk_cfg = cfg.get("risk", {})
        atr_mult = risk_cfg.get("atr_stop_multiplier", 1.5)
        rr_ratio = risk_cfg.get("rr_ratio", 2.0)
        sl_pct = max(0.001, min(0.05, atr_pct * atr_mult))
        tp_pct = sl_pct * rr_ratio
        return {"signal": "BUY", "score": score, "sl_pct": sl_pct, "tp_pct": tp_pct}

    return {"signal": "HOLD", "score": score}
