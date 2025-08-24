import importlib.util
from pathlib import Path
import pytest

# dynamically load trade_executor module without package structure
MODULE_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "trade_executor.py"
spec = importlib.util.spec_from_file_location("trade_executor", MODULE_PATH)
trade_executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trade_executor)


def test_trade_flow(tmp_path, monkeypatch):
    # redirect persistence to temporary paths
    monkeypatch.setattr(trade_executor, "BAL_PATH", tmp_path / "balance.txt")
    monkeypatch.setattr(trade_executor, "POS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(trade_executor, "CD_PATH", tmp_path / "cooldowns.json")
    monkeypatch.setattr(trade_executor, "PPL_PATH", tmp_path / "pnl.json")
    monkeypatch.setattr(trade_executor, "TC_PATH", tmp_path / "tc.json")
    monkeypatch.setattr(trade_executor, "DP_PATH", tmp_path / "dp.json")

    # ensure deterministic risk configuration
    monkeypatch.setitem(trade_executor.RISK_CFG, "tradable_balance_ratio", 1.0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "stake_per_trade_ratio", 0.015)
    monkeypatch.setitem(trade_executor.RISK_CFG, "dry_run_wallet", 1000.0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "daily_loss_limit", None)

    broker = trade_executor.PaperBroker()
    assert broker.balance == 1000.0

    symbol = "TEST"
    buy_price = 10.0
    risk = broker.stake_amount(symbol)
    sl_pct = trade_executor.CFG.get("exits", {}).get("stop_loss_pct", broker.stop_loss_pct)
    expected_qty = risk / (sl_pct * buy_price)
    expected_stake = expected_qty * buy_price

    # buy reduces balance and records position
    buy_order = broker.buy(symbol, buy_price, {})
    assert buy_order is not None
    assert buy_order["qty"] == pytest.approx(expected_qty)
    assert broker.balance == pytest.approx(1000.0 - expected_stake)
    assert symbol in broker.positions

    # sell removes position, increases balance and reports PnL
    sell_price = 12.0
    result = broker.sell(symbol, sell_price)
    assert result is not None
    assert symbol not in broker.positions

    expected_balance = 1000.0 - expected_stake + expected_qty * sell_price
    assert broker.balance == pytest.approx(expected_balance)
    assert result["balance"] == pytest.approx(expected_balance)
    assert result["pnl"] == pytest.approx(expected_qty * (sell_price - buy_price))
