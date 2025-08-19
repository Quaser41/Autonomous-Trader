import importlib.util
from pathlib import Path
import json
import pytest

# load trade_executor
TE_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "trade_executor.py"
spec_te = importlib.util.spec_from_file_location("trade_executor", TE_PATH)
trade_executor = importlib.util.module_from_spec(spec_te)
spec_te.loader.exec_module(trade_executor)

# load data_fetchers
DF_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "data_fetchers.py"
spec_df = importlib.util.spec_from_file_location("data_fetchers", DF_PATH)
data_fetchers = importlib.util.module_from_spec(spec_df)
spec_df.loader.exec_module(data_fetchers)


def _patch_trade_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_executor, "BAL_PATH", tmp_path / "balance.txt")
    monkeypatch.setattr(trade_executor, "POS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(trade_executor, "CD_PATH", tmp_path / "cooldowns.json")
    monkeypatch.setattr(trade_executor, "PPL_PATH", tmp_path / "pnl.json")
    monkeypatch.setattr(trade_executor, "TC_PATH", tmp_path / "tc.json")
    monkeypatch.setattr(trade_executor, "DP_PATH", tmp_path / "dp.json")


def _setup_risk(monkeypatch):
    monkeypatch.setitem(trade_executor.RISK_CFG, "tradable_balance_ratio", 1.0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "stake_per_trade_ratio", 0.1)
    monkeypatch.setitem(trade_executor.RISK_CFG, "dry_run_wallet", 1000.0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "max_trades_per_day", 100)
    monkeypatch.setitem(trade_executor.RISK_CFG, "cooldown_minutes", 0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "daily_loss_limit", None)


def test_symbol_pnl_rolling_window(tmp_path, monkeypatch):
    _patch_trade_paths(tmp_path, monkeypatch)
    _setup_risk(monkeypatch)
    broker = trade_executor.PaperBroker()
    for _ in range(trade_executor.EXPECTANCY_WINDOW + 5):
        broker.buy("AAA", 10.0, {})
        broker.sell("AAA", 11.0)
    assert len(broker.symbol_pnl["AAA"]) == trade_executor.EXPECTANCY_WINDOW


def test_whitelist_drops_negative_expectancy(tmp_path, monkeypatch):
    perf_path = tmp_path / "symbol_pnl.json"
    perf_path.write_text(json.dumps({"GOOD": [1, -0.5, 2], "BAD": [-1, -1, -1]}))

    monkeypatch.setattr(data_fetchers, "PERF_PATH", perf_path)
    monkeypatch.setattr(data_fetchers, "RUNTIME_PATH", tmp_path / "runtime.json")
    monkeypatch.setattr(data_fetchers, "_CONFIG", {"whitelist": ["GOOD", "BAD"]})

    wl = data_fetchers.load_crypto_whitelist()
    assert "GOOD" in wl
    assert "BAD" not in wl
