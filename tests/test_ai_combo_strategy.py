import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

MODULE_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "strategies" / "ai_combo_strategy.py"
spec = importlib.util.spec_from_file_location("ai_combo_strategy", MODULE_PATH)
ai_combo_strategy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai_combo_strategy)


def _synth_data() -> pd.DataFrame:
    """Create a minimal dataset triggering a BUY with base threshold."""
    n = 100
    close = np.linspace(100, 150, n)
    # add breakout on final bar
    close[-1] = close[-2] + 5
    high = close + 1
    low = close - 1
    # avoid including last close in 20-bar high
    high[-1] = close[-1] - 1
    low[-1] = close[-1] - 2
    volume = np.ones(n) * 1000
    volume[-1] = 1500
    return pd.DataFrame({"high": high, "low": low, "close": close, "volume": volume})


def test_buy_score_threshold_respected():
    df = _synth_data()
    base = ai_combo_strategy.generate_signal(df, {"strategy": {"buy_score_threshold": 0.0}})
    score = base["score"]
    assert base["signal"] == "BUY"

    high_cfg = {"strategy": {"buy_score_threshold": score + 0.1}}
    low_cfg = {"strategy": {"buy_score_threshold": score - 0.1}}

    assert ai_combo_strategy.generate_signal(df, high_cfg)["signal"] == "HOLD"
    assert ai_combo_strategy.generate_signal(df, low_cfg)["signal"] == "BUY"


def test_min_avg_volume_filter():
    df = _synth_data()
    cfg = {"strategy": {"filters": {"avg_volume_period": 20, "min_avg_volume": 2000}}}
    res = ai_combo_strategy.generate_signal(df, cfg)
    assert res["signal"] == "HOLD"
    assert res.get("failed") == "avg_volume"


def test_atr_ceiling_filter():
    df = _synth_data()
    df["high"] = df["close"] * 1.2
    df["low"] = df["close"] * 0.8
    cfg = {"strategy": {"filters": {"max_atr_pct": 0.01}}}
    res = ai_combo_strategy.generate_signal(df, cfg)
    assert res["signal"] == "HOLD"
    assert res.get("failed") == "atr_range"


def test_adx_filter():
    df = _synth_data()
    cfg = {"strategy": {"filters": {"adx_period": 14, "min_adx": 101}}}
    res = ai_combo_strategy.generate_signal(df, cfg)
    assert res["signal"] == "HOLD"
    assert res.get("failed") == "adx"
