
import os, json, time
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CFG = json.load(open(os.path.join(BASE_DIR, "config", "config.json"), "r"))
RISK_CFG = CFG.get("risk", {})

BAL_PATH = os.path.join(BASE_DIR, "data", "performance", "balance.txt")
POS_PATH = os.path.join(BASE_DIR, "data", "performance", "positions.json")
CD_PATH  = os.path.join(BASE_DIR, "data", "runtime", "cooldowns.json")

class PaperBroker:
    def __init__(self):
        os.makedirs(os.path.join(BASE_DIR, "data", "performance"), exist_ok=True)
        os.makedirs(os.path.join(BASE_DIR, "data", "runtime"), exist_ok=True)

        self.balance = self._load_balance()
        self.positions = self._load_positions()
        self.cooldowns = self._load_cooldowns()

        risk_cfg = RISK_CFG
        self.max_open = risk_cfg.get("max_open_trades", 3)
        self.tradable_ratio = risk_cfg.get("tradable_balance_ratio", 0.75)
        self.stake_ratio = risk_cfg.get("stake_per_trade_ratio", 0.2)
        self.cooldown_minutes = risk_cfg.get("cooldown_minutes", 30)

        self._persist_balance(); self._persist_positions(); self._persist_cooldowns()

    # ---------- persistence ----------
    def _load_balance(self) -> float:
        risk_cfg = CFG.get("risk", {})
        reset = risk_cfg.get("reset_balance", False) or CFG.get("reset_balance", False)
        try:
            if os.path.exists(BAL_PATH) and not reset:
                return float(open(BAL_PATH, "r").read().strip())
        except Exception:
            pass
        return RISK_CFG.get("dry_run_wallet", 1000.0)

    def _load_positions(self) -> Dict[str, Any]:
        try:
            if os.path.exists(POS_PATH):
                return json.load(open(POS_PATH, "r"))
        except Exception:
            pass
        return {}

    def _load_cooldowns(self) -> Dict[str, float]:
        try:
            if os.path.exists(CD_PATH):
                return json.load(open(CD_PATH, "r"))
        except Exception:
            pass
        return {}

    def _persist_balance(self):
        with open(BAL_PATH, "w") as f:
            f.write(str(self.balance))

    def _persist_positions(self):
        with open(POS_PATH, "w", encoding="utf-8") as f:
            json.dump(self.positions, f, indent=2)

    def _persist_cooldowns(self):
        with open(CD_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cooldowns, f, indent=2)

    # ---------- utils ----------
    def _now(self) -> float:
        return time.time()

    def _on_cooldown(self, symbol: str) -> bool:
        ts = self.cooldowns.get(symbol, 0)
        return (self._now() - ts) < self.cooldown_minutes * 60

    # ---------- risk sizing ----------
    def can_open(self) -> bool:
        return len(self.positions) < self.max_open and self.balance * self.tradable_ratio > 0

    def stake_amount(self) -> float:
        return min(self.balance * self.tradable_ratio * self.stake_ratio, self.balance)

    # ---------- trading ----------
    def buy(self, symbol: str, price: float, meta: Dict[str, Any]):
        if not self.can_open() or self._on_cooldown(symbol):
            return None
        stake = self.stake_amount()
        if stake <= 0:
            return None
        qty = max(0.00000001, stake / max(price, 1e-9))
        self.balance -= stake
        # initial stops
        sl_pct = meta.get("stop_loss_pct", RISK_CFG.get("stop_loss_pct", 0.006))
        tp_pct = meta.get("take_profit_pct", RISK_CFG.get("take_profit_pct", 0.006))
        self.positions[symbol] = {
            "qty": qty,
            "entry": price,
            "peak": price,
            "stop": price * (1 - sl_pct),
            "tp_price": price * (1 + tp_pct),
            "breakeven_trigger_pct": meta.get("breakeven_trigger_pct", RISK_CFG.get("breakeven_trigger_pct", 0.003)),
            "trailing_stop_pct": meta.get("trailing_stop_pct", RISK_CFG.get("trailing_stop_pct", 0.004)),
            "meta": meta
        }
        self._persist_balance(); self._persist_positions()
        return {"symbol": symbol, "qty": qty, "price": price}

    def update_trailing(self, symbol: str, price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return
        entry = pos["entry"]
        pos["peak"] = max(pos.get("peak", entry), price)
        # breakeven if in profit enough
        trigger = entry * (1 + pos.get("breakeven_trigger_pct", 0.003))
        if price >= trigger:
            pos["stop"] = max(pos["stop"], entry)  # move to breakeven
            # trail from peak
            t_pct = pos.get("trailing_stop_pct", 0.004)
            trail_stop = pos["peak"] * (1 - t_pct)
            if trail_stop > pos["stop"]:
                pos["stop"] = trail_stop

    def should_exit(self, symbol: str, price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return False, "no_pos"
        # hard TP
        if price >= pos.get("tp_price", float("inf")):
            return True, "tp"
        # trailing/breakeven update and check stops
        self.update_trailing(symbol, price)
        if price <= pos.get("stop", 0):
            return True, "sl_or_trail"
        return False, ""

    def sell(self, symbol: str, price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return None
        proceeds = pos["qty"] * price
        pnl = proceeds - pos["qty"] * pos["entry"]
        self.balance += proceeds
        del self.positions[symbol]
        self.cooldowns[symbol] = self._now()
        self._persist_balance(); self._persist_positions(); self._persist_cooldowns()
        return {"symbol": symbol, "price": price, "pnl": pnl, "balance": self.balance}
