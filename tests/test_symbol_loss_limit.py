import importlib.util
from pathlib import Path
import json
import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "autonomous_trader" / "utils" / "trade_executor.py"
spec = importlib.util.spec_from_file_location("trade_executor", MODULE_PATH)
trade_executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trade_executor)


def _patch_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(trade_executor, "BAL_PATH", tmp_path / "balance.txt")
    monkeypatch.setattr(trade_executor, "POS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(trade_executor, "CD_PATH", tmp_path / "cooldowns.json")
    monkeypatch.setattr(trade_executor, "PPL_PATH", tmp_path / "pnl.json")
    monkeypatch.setattr(trade_executor, "TC_PATH", tmp_path / "tc.json")
    monkeypatch.setattr(trade_executor, "DP_PATH", tmp_path / "dp.json")
    monkeypatch.setattr(trade_executor, "RW_PATH", tmp_path / "runtime_whitelist.json")


def _setup_risk(monkeypatch):
    monkeypatch.setitem(trade_executor.RISK_CFG, "tradable_balance_ratio", 1.0)
    # configure risk so that position size is 500 at default 1.5% stop loss
    monkeypatch.setitem(trade_executor.RISK_CFG, "stake_per_trade_ratio", 0.0075)
    monkeypatch.setitem(trade_executor.RISK_CFG, "dry_run_wallet", 1000.0)
    monkeypatch.setitem(trade_executor.RISK_CFG, "reset_balance", False)
    monkeypatch.setitem(trade_executor.RISK_CFG, "daily_loss_limit", None)
    monkeypatch.setitem(trade_executor.RISK_CFG, "cooldown_minutes", 0)


def test_symbol_loss_limit_removes_from_whitelist(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _setup_risk(monkeypatch)
    monkeypatch.setitem(trade_executor.CFG, "symbol_loss_limit", -10.0)

    broker = trade_executor.PaperBroker()

    rw_path = trade_executor.RW_PATH
    rw_path.write_text(json.dumps(["BAD"]))

    broker.buy("BAD", 10.0, {})
    broker.sell("BAD", 0.0)

    assert sum(broker.symbol_pnl["BAD"]) == pytest.approx(-500.0)

    wl_before = json.loads(rw_path.read_text())
    assert "BAD" in wl_before

    assert broker.buy("BAD", 10.0, {}) is None

    wl_after = json.loads(rw_path.read_text())
    assert "BAD" not in wl_after
