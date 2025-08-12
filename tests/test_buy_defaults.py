import importlib.util
from pathlib import Path

import pytest


# dynamically load the main trade executor module
MODULE_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "trade_executor.py"
spec = importlib.util.spec_from_file_location("trade_executor_main", MODULE_PATH)
trade_executor_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trade_executor_main)


def test_buy_defaults_from_cfg(tmp_path, monkeypatch):
    # redirect persistence paths
    monkeypatch.setattr(trade_executor_main, "BAL_PATH", tmp_path / "balance.txt")
    monkeypatch.setattr(trade_executor_main, "POS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(trade_executor_main, "CD_PATH", tmp_path / "cooldowns.json")

    # deterministic risk parameters
    monkeypatch.setitem(trade_executor_main.RISK_CFG, "tradable_balance_ratio", 1.0)
    monkeypatch.setitem(trade_executor_main.RISK_CFG, "stake_per_trade_ratio", 1.0)
    monkeypatch.setitem(trade_executor_main.RISK_CFG, "dry_run_wallet", 1000.0)

    exits_cfg = {"stop_loss_pct": 0.1, "take_profit_pct": 0.2}
    trailing_cfg = {"breakeven_pct": 0.15, "trail_pct": 0.25}
    monkeypatch.setitem(trade_executor_main.CFG, "exits", exits_cfg)
    monkeypatch.setitem(trade_executor_main.CFG, "trailing_stop", trailing_cfg)

    broker = trade_executor_main.PaperBroker()
    price = 10.0
    broker.buy("TEST", price, {})

    pos = broker.positions["TEST"]
    assert pos["stop"] == pytest.approx(price * (1 - exits_cfg["stop_loss_pct"]))
    assert pos["tp_price"] == pytest.approx(price * (1 + exits_cfg["take_profit_pct"]))
    assert pos["breakeven_trigger_pct"] == pytest.approx(trailing_cfg["breakeven_pct"])
    assert pos["trailing_stop_pct"] == pytest.approx(trailing_cfg["trail_pct"])

