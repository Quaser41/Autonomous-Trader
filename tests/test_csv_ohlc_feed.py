import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

# dynamically load modules so the package need not be installed
UTIL_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "csv_ohlc_feed.py"
spec = importlib.util.spec_from_file_location("csv_ohlc_feed", UTIL_PATH)
csv_ohlc_feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csv_ohlc_feed)
read_csv_ohlcv = csv_ohlc_feed.read_csv_ohlcv

STRAT_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "strategies" / "ai_combo_strategy.py"
spec2 = importlib.util.spec_from_file_location("ai_combo_strategy", STRAT_PATH)
ai_combo_strategy = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(ai_combo_strategy)
generate_signal = ai_combo_strategy.generate_signal


def _synth_data() -> pd.DataFrame:
    """Create a dataset that triggers a BUY signal."""
    n = 100
    close = np.linspace(100, 150, n)
    close[-1] = close[-2] + 5  # breakout on final bar
    high = close + 1
    low = close - 1
    high[-1] = close[-1] - 1
    low[-1] = close[-1] - 2
    volume = np.ones(n) * 1000
    volume[-1] = 1500
    open_ = close.copy()
    return pd.DataFrame({
        "timestamp": np.arange(n),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def test_generate_signal_from_csv(tmp_path):
    df = _synth_data()
    csv_file = tmp_path / "sample.csv"
    df.to_csv(csv_file, index=False)

    loaded = read_csv_ohlcv(csv_file)
    cfg = {"strategy": {"buy_score_threshold": 0.0}}
    signal = generate_signal(loaded, cfg)

    assert signal["signal"] == "BUY"
