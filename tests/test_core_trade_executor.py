import importlib.util
from pathlib import Path
import pytest

# dynamically load trade_executor module without package structure
MODULE_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "trade_executor.py"
spec = importlib.util.spec_from_file_location("trade_executor_core", MODULE_PATH)
trade_executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trade_executor)


def _patch_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_executor, "BAL_PATH", tmp_path / "balance.txt")
    monkeypatch.setattr(trade_executor, "POS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(trade_executor, "CD_PATH", tmp_path / "cooldowns.json")
    monkeypatch.setattr(trade_executor, "PPL_PATH", tmp_path / "pnl.json")
    monkeypatch.setattr(trade_executor, "TC_PATH", tmp_path / "tc.json")
    monkeypatch.setattr(trade_executor, "DP_PATH", tmp_path / "dp.json")


def _setup_risk(monkeypatch):
    monkeypatch.setitem(trade_executor.RISK_CFG, "tradable_balance_ratio", 1.0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "stake_per_trade_ratio", 1.0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "dry_run_wallet", 1000.0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "reset_balance", False)
    monkeypatch.setitem(trade_executor.RISK_CFG, "daily_loss_limit", None)


def test_trade_flow(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _setup_risk(monkeypatch)

    broker = trade_executor.PaperBroker()
    assert broker.balance == 1000.0

    symbol = "TEST"
    buy_price = 10.0
    stake = broker.stake_amount(symbol)

    buy_order = broker.buy(symbol, buy_price, {})
    assert buy_order is not None
    assert broker.balance == pytest.approx(1000.0 - stake)
    assert symbol in broker.positions

    sell_price = 12.0
    result = broker.sell(symbol, sell_price)
    assert result is not None
    assert symbol not in broker.positions

    qty = stake / buy_price
    expected_balance = 1000.0 - stake + qty * sell_price
    assert broker.balance == pytest.approx(expected_balance)
    assert result["balance"] == pytest.approx(expected_balance)
    assert result["pnl"] == pytest.approx(qty * (sell_price - buy_price))


def test_reset_balance(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _setup_risk(monkeypatch)

    # simulate existing balance
    trade_executor.BAL_PATH.write_text("250")
    broker1 = trade_executor.PaperBroker()
    assert broker1.balance == pytest.approx(250.0)

    # enable reset and instantiate again
    trade_executor.RISK_CFG["reset_balance"] = True
    broker2 = trade_executor.PaperBroker()
    assert broker2.balance == pytest.approx(1000.0)
