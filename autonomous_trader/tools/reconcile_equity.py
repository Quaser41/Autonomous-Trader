#!/usr/bin/env python3
"""Verify equity balance against cumulative per-symbol PnL.

Reads ``data/performance/balance.txt`` and ``data/performance/symbol_pnl.json``
and checks that the wallet balance equals the configured starting balance plus
the sum of per-symbol PnL. Any discrepancy is logged via ``Notifier``.

Example cron entry to run daily at midnight (UTC):

    0 0 * * * /usr/bin/python /path/to/tools/reconcile_equity.py
"""

import os
import json
import sys
from typing import Dict


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.append(ROOT)

from utils.logger import Notifier

# Paths
BAL_PATH = os.path.join(ROOT, "data", "performance", "balance.txt")
PNL_PATH = os.path.join(ROOT, "data", "performance", "symbol_pnl.json")
CFG_PATH = os.path.join(ROOT, "config", "config.json")


def _read_balance() -> float:
    try:
        with open(BAL_PATH, "r", encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        return 0.0


def _read_symbol_pnl() -> Dict[str, float]:
    try:
        with open(PNL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def reconcile() -> None:
    cfg = json.load(open(CFG_PATH, "r", encoding="utf-8"))
    start = cfg.get("risk", {}).get("dry_run_wallet", 0.0)
    notifier = Notifier(cfg)

    balance = _read_balance()
    pnl_map = _read_symbol_pnl()
    total_pnl = sum(float(v) for v in pnl_map.values())
    expected = start + total_pnl
    diff = balance - expected

    if abs(diff) > 1e-6:
        notifier.send(
            f"[RECON] Equity mismatch: balance {balance:.2f}, expected {expected:.2f}, diff {diff:.2f}"
        )
    else:
        notifier.send(f"[RECON] Equity matches cumulative PnL: {balance:.2f}")


if __name__ == "__main__":
    reconcile()
