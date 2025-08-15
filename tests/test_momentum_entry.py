import importlib.util
from pathlib import Path
import pandas as pd
import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "momentum.py"
spec = importlib.util.spec_from_file_location("momentum", MODULE_PATH)
momentum = importlib.util.module_from_spec(spec)
spec.loader.exec_module(momentum)

apply_momentum_entry = momentum.apply_momentum_entry


def test_momentum_triggers_buy():
    df = pd.DataFrame({"close": [100, 100, 100, 105]})
    sig = {"signal": "HOLD"}
    cfg = {"strategy": {"momentum_pct": 0.04}}
    out = apply_momentum_entry(df, sig, cfg)
    assert out["signal"] == "BUY"
    assert out["score"] == pytest.approx(0.05)


def test_momentum_below_threshold_returns_hold():
    df = pd.DataFrame({"close": [100, 100, 100, 102]})
    sig = {"signal": "HOLD", "failed": "gate"}
    cfg = {"strategy": {"momentum_pct": 0.05}}
    out = apply_momentum_entry(df, sig, cfg)
    assert out["signal"] == "HOLD"
    assert out["failed"] == "gate"
    assert out["score"] == pytest.approx(0.02)
