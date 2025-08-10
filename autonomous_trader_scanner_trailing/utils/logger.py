
import os, sys, json, datetime as dt

BASE = os.path.dirname(os.path.dirname(__file__))
LOG_DIR = os.path.join(BASE, "data", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

CFG = json.load(open(os.path.join(BASE, "config", "config.json"), "r"))

class Notifier:
    def __init__(self, cfg):
        self.enabled = bool(cfg.get("telegram_enabled", False))
        self.token = cfg.get("telegram_token","")
        self.chat_id = cfg.get("telegram_chat_id","")
        # Lazy import only if enabled
        if self.enabled:
            try:
                from telegram import Bot
                self.bot = Bot(self.token)
            except Exception as e:
                print("[WARN] Telegram disabled (init failed):", e)
                self.enabled = False
                self.bot = None
        else:
            self.bot = None

    def send(self, msg: str):
        stamp = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{stamp}] {msg}"
        print(line)
        with open(os.path.join(LOG_DIR, "events.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.enabled and self.bot:
            try:
                self.bot.send_message(chat_id=self.chat_id, text=line)
            except Exception as e:
                print("[WARN] Telegram send failed:", e)

def log_trade(side: str, symbol: str, qty: float, price: float, extra=None):
    extra = extra or {}
    stamp = dt.datetime.utcnow().isoformat()
    # events
    with open(os.path.join(LOG_DIR, "events.log"), "a", encoding="utf-8") as f:
        f.write(f"[{stamp} UTC] {side} {symbol} @ {price:.4f}\n")
    # trades.csv
    trades_path = os.path.join(LOG_DIR, "trades.csv")
    write_header = not os.path.exists(trades_path)
    import csv
    with open(trades_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp","side","symbol","qty","price","extra"])
        w.writerow([stamp, side, symbol, qty, price, json.dumps(extra)])
    # stdout
    print(f"[{stamp}] {side} {symbol} {qty:.8f} @ {price:.4f}")

def log_status(balance: float, open_trades: int, unrealized_pnl: float):
    stamp = dt.datetime.utcnow().isoformat()
    line = f"[{stamp}] Balance: ${balance:.2f} | Open trades: {open_trades} | Unrealized PnL: ${unrealized_pnl:.2f}"
    print(line)
    with open(os.path.join(LOG_DIR, "status.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")

def log_equity(timestamp: float, balance: float, equity: float):
    path = os.path.join(LOG_DIR, "equity_curve.csv")
    write_header = not os.path.exists(path)
    import csv
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp","balance","equity"])
        w.writerow([int(timestamp), balance, equity])
