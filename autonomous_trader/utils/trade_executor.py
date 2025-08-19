
import os, json, time
from typing import Dict, Any, Optional

# Import the Notifier logger so we can emit messages to events.log.  The module
# may be loaded in different ways (as part of a package or as a standalone
# module in tests), so we attempt the package import first and fall back to
# loading the sibling ``logger.py`` directly if that fails.
try:  # pragma: no cover - import fallback
    from utils.logger import Notifier  # type: ignore
except Exception:  # pragma: no cover
    import importlib.util, pathlib

    _spec = importlib.util.spec_from_file_location(
        "logger", pathlib.Path(__file__).resolve().parent / "logger.py"
    )
    _logger = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(_logger)
    Notifier = _logger.Notifier  # type: ignore

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
CFG = json.load(open(os.path.join(BASE_DIR, "config", "config.json"), "r"))
RISK_CFG = CFG.get("risk", {})

BAL_PATH = os.path.join(BASE_DIR, "data", "performance", "balance.txt")
POS_PATH = os.path.join(BASE_DIR, "data", "performance", "positions.json")
PPL_PATH = os.path.join(BASE_DIR, "data", "performance", "symbol_pnl.json")
CD_PATH  = os.path.join(BASE_DIR, "data", "runtime", "cooldowns.json")
TC_PATH  = os.path.join(BASE_DIR, "data", "runtime", "trade_count.json")
DP_PATH  = os.path.join(BASE_DIR, "data", "runtime", "daily_pnl.json")
RW_PATH  = os.path.join(BASE_DIR, "data", "runtime", "runtime_whitelist.json")

# Global notifier instance used for risk/decision logs.
LOGGER = Notifier(CFG)

class PaperBroker:
    def __init__(self):
        os.makedirs(os.path.join(BASE_DIR, "data", "performance"), exist_ok=True)
        os.makedirs(os.path.join(BASE_DIR, "data", "runtime"), exist_ok=True)

        self.balance = self._load_balance()
        self.positions = self._load_positions()
        self.cooldowns = self._load_cooldowns()
        self.symbol_pnl = self._load_symbol_pnl()
        self.daily_trades, self.trade_day = self._load_trade_count()
        self.daily_pnl, self.pnl_day = self._load_daily_pnl()

        risk_cfg = RISK_CFG
        self.max_open = risk_cfg.get("max_open_trades", 3)
        self.tradable_ratio = risk_cfg.get("tradable_balance_ratio", 0.75)
        self.stake_ratio = risk_cfg.get("stake_per_trade_ratio", 0.2)
        self.cooldown_minutes = risk_cfg.get("cooldown_minutes", 30)
        self.max_trades_day = risk_cfg.get("max_trades_per_day", 10)
        self.daily_loss_limit = risk_cfg.get("daily_loss_limit")
        self.fee_pct = risk_cfg.get("fee_pct", 0.0)
        self.slippage_pct = risk_cfg.get("slippage_pct", 0.0)

        # cache exit and trailing stop configuration
        self.exits_cfg = CFG.get("exits", {})
        self.trailing_cfg_base = CFG.get("trailing_stop", {})
        self.stop_loss_pct = self.exits_cfg.get("stop_loss_pct", 0.015)
        self.trail_pct = self.trailing_cfg_base.get("trail_pct", 0.012)

        self._persist_balance(); self._persist_positions(); self._persist_cooldowns(); self._persist_symbol_pnl(); self._persist_trade_count(); self._persist_daily_pnl()

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

    def _load_symbol_pnl(self) -> Dict[str, float]:
        try:
            if os.path.exists(PPL_PATH):
                return json.load(open(PPL_PATH, "r"))
        except Exception:
            pass
        return {}

    def _load_trade_count(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            if os.path.exists(TC_PATH):
                data = json.load(open(TC_PATH, "r"))
                return data.get("count", 0), data.get("day", today)
        except Exception:
            pass
        return 0, today

    def _load_daily_pnl(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        try:
            if os.path.exists(DP_PATH):
                data = json.load(open(DP_PATH, "r"))
                return data.get("pnl", 0.0), data.get("day", today)
        except Exception:
            pass
        return 0.0, today

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

    def _persist_daily_pnl(self):
        with open(DP_PATH, "w", encoding="utf-8") as f:
            json.dump({"pnl": self.daily_pnl, "day": self.pnl_day}, f, indent=2)

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
        if today != self.pnl_day:
            self.pnl_day = today
            self.daily_pnl = 0.0
            self._persist_daily_pnl()
        if self.daily_loss_limit is not None and self.daily_pnl <= -abs(self.daily_loss_limit):
            LOGGER.send("[RISK] Cannot open trade: daily_loss_limit reached")
            return False
        if self.daily_trades >= self.max_trades_day:
            LOGGER.send("[RISK] Cannot open trade: max_trades_day reached")
            return False
        if len(self.positions) >= self.max_open:
            LOGGER.send("[RISK] Cannot open trade: max_open_trades reached")
            return False
        return self.balance * self.tradable_ratio > 0

    def stake_amount(self, symbol: Optional[str] = None) -> float:
        stake = self.balance * self.tradable_ratio * self.stake_ratio
        if symbol:
            pnl = self.symbol_pnl.get(symbol, 0.0)
            if pnl > 0:
                stake *= 1.5
            elif pnl < 0:
                stake *= 0.5
        return min(stake, self.balance)

    # ---------- trading ----------
    def buy(self, symbol: str, price: float, meta: Dict[str, Any]):
        if not self.can_open() or self._on_cooldown(symbol):
            return None

        symbol_pnl = self.symbol_pnl.get(symbol, 0.0)
        limit = CFG.get("symbol_loss_limit")
        stake = self.stake_amount(symbol)
        if limit is not None:
            if symbol_pnl <= limit:
                try:
                    wl = json.load(open(RW_PATH, "r"))
                    if symbol in wl:
                        wl.remove(symbol)
                        with open(RW_PATH, "w", encoding="utf-8") as f:
                            json.dump(wl, f, indent=2)
                except Exception:
                    pass
                if CFG.get("debug", {}).get("verbose"):
                    print(f"[RISK] Skipping {symbol}: pnl {symbol_pnl:.2f} <= {limit:.2f}")
                return None
            elif symbol_pnl < 0 and abs(symbol_pnl) >= 0.8 * abs(limit):
                stake *= 0.5
        if stake <= 0:
            return None
        exits_cfg = self.exits_cfg
        base_sl = exits_cfg.get("stop_loss_pct", self.stop_loss_pct)
        sl_pct = meta.get("stop_loss_pct", base_sl)
        if sl_pct > 0:
            stake *= base_sl / sl_pct
        adj_price = price * (1 + self.slippage_pct + self.fee_pct)
        qty = max(0.00000001, stake / max(adj_price, 1e-9))
        self.balance -= stake
        # initial stops
        trailing_cfg_base = self.trailing_cfg_base
        overrides = trailing_cfg_base.get("overrides", {})
        symbol_cfg = overrides.get(symbol, {})
        trailing_cfg = {k: v for k, v in trailing_cfg_base.items() if k != "overrides"}
        trailing_cfg.update(symbol_cfg)
        tp_pct = meta.get("take_profit_pct", exits_cfg.get("take_profit_pct", 0.006))
        atr_pct = meta.get("atr_pct")
        if atr_pct is None:
            atr_mult = RISK_CFG.get("atr_stop_multiplier", 1.5)
            atr_pct = sl_pct / atr_mult if atr_mult else None
        self.positions[symbol] = {
            "qty": qty,
            "entry": adj_price,
            "peak": price,
            "stop": price * (1 - sl_pct),
            "tp_price": price * (1 + tp_pct),
            "activate_profit_pct": meta.get("activate_profit_pct", trailing_cfg.get("activate_profit_pct", 0.0)),
            "breakeven_trigger_pct": meta.get("breakeven_trigger_pct", trailing_cfg.get("breakeven_pct", 0.003)),
            "trailing_stop_pct": meta.get("trailing_stop_pct", trailing_cfg.get("trail_pct", self.trail_pct)),
            "atr_trail_multiplier": meta.get("atr_trail_multiplier", trailing_cfg.get("atr_trail_multiplier", 1.0)),
            "atr_pct": atr_pct,
            "trail_active": False,
            "meta": meta
        }
        self.daily_trades += 1
        self._persist_balance(); self._persist_positions(); self._persist_trade_count()
        return {"symbol": symbol, "qty": qty, "price": adj_price}

    def update_trailing(self, symbol: str, price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return
        entry = pos["entry"]
        pos["peak"] = max(pos.get("peak", entry), price)
        trailing_cfg_base = self.trailing_cfg_base
        overrides = trailing_cfg_base.get("overrides", {})
        symbol_cfg = overrides.get(symbol, {})
        trailing_cfg = {k: v for k, v in trailing_cfg_base.items() if k != "overrides"}
        trailing_cfg.update(symbol_cfg)
        activate_pct = pos.get("activate_profit_pct", trailing_cfg.get("activate_profit_pct", 0.0))
        if not pos.get("trail_active"):
            if price >= entry * (1 + activate_pct):
                pos["trail_active"] = True
            else:
                return
        # breakeven if in profit enough
        trigger_pct = pos.get("breakeven_trigger_pct", trailing_cfg.get("breakeven_pct", 0.003))
        if price >= entry * (1 + trigger_pct):
            pos["stop"] = max(pos["stop"], entry)  # move to breakeven
        # trail from peak using ATR multiple (fallback to pct)
        atr_mult = pos.get("atr_trail_multiplier", trailing_cfg.get("atr_trail_multiplier", 1.0))
        atr_pct = pos.get("atr_pct")
        if atr_pct and atr_mult > 0:
            t_pct = atr_pct * atr_mult
        else:
            t_pct = pos.get("trailing_stop_pct", trailing_cfg.get("trail_pct", self.trail_pct))
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
        qty = pos["qty"]
        adj_price = price * (1 - self.slippage_pct - self.fee_pct)
        proceeds = qty * adj_price
        pnl = proceeds - qty * pos["entry"]
        self.balance += proceeds
        del self.positions[symbol]
        self.cooldowns[symbol] = self._now()
        self.symbol_pnl[symbol] = self.symbol_pnl.get(symbol, 0.0) + pnl
        self.daily_pnl += pnl
        self._persist_balance(); self._persist_positions(); self._persist_cooldowns(); self._persist_symbol_pnl(); self._persist_daily_pnl()
        return {"symbol": symbol, "qty": qty, "price": adj_price, "pnl": pnl, "balance": self.balance}
