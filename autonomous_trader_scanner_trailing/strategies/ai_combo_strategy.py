
import numpy as np
import pandas as pd

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    up = (delta.clip(lower=0)).ewm(alpha=1/period, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = up / (down.replace(0, 1e-12))
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev_close = c.shift(1)
    tr = pd.concat([(h - l).abs(),
                    (h - prev_close).abs(),
                    (l - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def generate_signal(df: pd.DataFrame, cfg) -> dict:
    # compute indicators
    df = df.copy()
    df["ema_fast"] = ema(df["close"], 9)
    df["ema_slow"] = ema(df["close"], 21)
    df["rsi"] = rsi(df["close"], 14)
    df["atr"] = atr(df, 14)
    df["atr_ratio"] = df["atr"] / df["close"]
    last = df.iloc[-1]

    # movement gate (avoid chop)
    if last["atr_ratio"] < max(0.0015, cfg.get("scanner_min_atr_ratio", 0.003) * 0.5):
        return {"signal":"HOLD","score":0}

    cross_up = (df["ema_fast"].iloc[-2] <= df["ema_slow"].iloc[-2]) and (last["ema_fast"] > last["ema_slow"])
    rsi_ok = last["rsi"] < 70 and last["rsi"] > 45  # recovering momentum

    score = 0
    if cross_up: score += 1.0
    score += max(0, min(1.0, (last["rsi"] - 50) / 25)) * 0.5  # 0..0.5
    score += max(0, min(1.0, (last["ema_fast"] - last["ema_slow"]) / (0.002 * last["close"]))) * 0.5  # 0..0.5

    if cross_up and rsi_ok and score >= 0.7:
        return {"signal":"BUY","score":float(score)}
    return {"signal":"HOLD","score":float(score)}
