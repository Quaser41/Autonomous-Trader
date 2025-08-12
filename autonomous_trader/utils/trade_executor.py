
import os, json, time
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CFG_PATH = os.path.join(BASE_DIR, "config", "config.json")
with open(CFG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)
RISK_CFG = CFG.get("risk", {})

BAL_PATH = os.path.join(BASE_DIR, "data", "performance", "balance.txt")
POS_PATH = os.path.join(BASE_DIR, "data", "performance", "positions.json")
PPL_PATH = os.path.join(BASE_DIR, "data", "performance", "symbol_pnl.json")
CD_PATH  = os.path.join(BASE_DIR, "data", "runtime", "cooldowns.json")
TC_PATH  = os.path.join(BASE_DIR, "data", "runtime", "trade_count.json")

class PaperBroker:
    def __init__(self):
        os.makedirs(os.path.join(BASE_DIR, "data", "performance"), exist_ok=True)
        os.makedirs(os.path.join(BASE_DIR, "data", "runtime"), exist_ok=True)

        self.balance = self._load_balance()
        self.positions = self._load_positions()
        self.cooldowns = self._load_cooldowns()
        self.symbol_pnl = self._load_symbol_pnl()
        self.daily_trades, self.trade_day = self._load_trade_count()

        risk_cfg = RISK_CFG
        self.max_open = risk_cfg.get("max_open_trades", 3)
        self.tradable_ratio = risk_cfg.get("tradable_balance_ratio", 0.75)
        self.stake_ratio = risk_cfg.get("stake_per_trade_ratio", 0.2)
        self.cooldown_minutes = risk_cfg.get("cooldown_minutes", 30)
        self.max_trades_day = risk_cfg.get("max_trades_per_day", 10)

        self._persist_balance(); self._persist_positions(); self._persist_cooldowns(); self._persist_symbol_pnl(); self._persist_trade_count()

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
                with open(POS_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _load_cooldowns(self) -> Dict[str, float]:
        try:
            if os.path.exists(CD_PATH):
                with open(CD_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _load_symbol_pnl(self) -> Dict[str, float]:
        try:
            if os.path.exists(PPL_PATH):
                with open(PPL_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _load_trade_count(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            if os.path.exists(TC_PATH):
                with open(TC_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("count", 0), data.get("day", today)
        except Exception:
            pass
        return 0, today

    def _persist_balance(self):
        with open(BAL_PATH, "w") as f:
            f.write(str(self.balance))

    def _persist_positions(self):
        with open(POS_PATH, "w", encoding="utf-8") as f:
            json.dump(self.positions, f, indent=2)

    def _persist_cooldowns(self):
        with open(CD_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cooldowns, f, indent=2)

    def _persist_symbol_pnl(self):
        with open(PPL_PATH, "w", encoding="utf-8") as f:
            json.dump(self.symbol_pnl, f, indent=2)

    def _persist_trade_count(self):
        with open(TC_PATH, "w", encoding="utf-8") as f:
            json.dump({"count": self.daily_trades, "day": self.trade_day}, f, indent=2)

    # ---------- utils ----------
    def _now(self) -> float:
        return time.time()

    def _on_cooldown(self, symbol: str) -> bool:
        ts = self.cooldowns.get(symbol, 0)
        return (self._now() - ts) < self.cooldown_minutes * 60

    # ---------- risk sizing ----------
    def can_open(self) -> bool:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.trade_day:
            self.trade_day = today
            self.daily_trades = 0
            self._persist_trade_count()
        if self.daily_trades >= self.max_trades_day:
            return False
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
        exits_cfg = CFG.get("exits", {})
        base_sl = exits_cfg.get("stop_loss_pct", 0.01)
        sl_pct = meta.get("stop_loss_pct", base_sl)
        if sl_pct > 0:
            stake *= base_sl / sl_pct
        qty = max(0.00000001, stake / max(price, 1e-9))
        self.balance -= stake
        # initial stops
        trailing_cfg = CFG.get("trailing_stop", {})
        tp_pct = meta.get("take_profit_pct", exits_cfg.get("take_profit_pct", 0.006))
        self.positions[symbol] = {
            "qty": qty,
            "entry": price,
            "peak": price,
            "stop": price * (1 - sl_pct),
            "tp_price": price * (1 + tp_pct),
            "breakeven_trigger_pct": meta.get("breakeven_trigger_pct", trailing_cfg.get("breakeven_pct", 0.003)),
            "trailing_stop_pct": meta.get("trailing_stop_pct", trailing_cfg.get("trail_pct", 0.004)),
            "meta": meta
        }
        self.daily_trades += 1
        self._persist_balance(); self._persist_positions(); self._persist_trade_count()
        return {"symbol": symbol, "qty": qty, "price": price}

    def update_trailing(self, symbol: str, price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return
        entry = pos["entry"]
        pos["peak"] = max(pos.get("peak", entry), price)
        # breakeven if in profit enough
        trailing_cfg = CFG.get("trailing_stop", {})
        trigger_pct = pos.get("breakeven_trigger_pct", trailing_cfg.get("breakeven_pct", 0.003))
        trigger = entry * (1 + trigger_pct)
        if price >= trigger:
            pos["stop"] = max(pos["stop"], entry)  # move to breakeven
            # trail from peak
            t_pct = pos.get("trailing_stop_pct", trailing_cfg.get("trail_pct", 0.004))
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
        self.symbol_pnl[symbol] = self.symbol_pnl.get(symbol, 0.0) + pnl
        self._persist_balance(); self._persist_positions(); self._persist_cooldowns(); self._persist_symbol_pnl()
        return {"symbol": symbol, "price": price, "pnl": pnl, "balance": self.balance}
