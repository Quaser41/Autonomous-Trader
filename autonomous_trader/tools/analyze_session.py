# tools/analyze_session.py
# in the main folder use this cmd: python -u tools/analyze_session.py --save-csv

import os, json, csv, math, argparse, datetime as dt
from collections import defaultdict, deque
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # project root
LOG_DIR = os.path.join(ROOT, "data", "logs")

TRADES_CSV = os.path.join(LOG_DIR, "trades.csv")
EQUITY_CSV = os.path.join(LOG_DIR, "equity_curve.csv")
EVENTS_LOG = os.path.join(LOG_DIR, "events.log")
STATUS_LOG = os.path.join(LOG_DIR, "status.log")

def read_trades():
    if not os.path.exists(TRADES_CSV):
        return pd.DataFrame(columns=["timestamp","side","symbol","qty","price","extra"])
    df = pd.read_csv(TRADES_CSV)
    # Best-effort parse of "extra" JSON column
    def _parse_extra(x):
        try:
            return json.loads(x) if isinstance(x, str) and x.strip() else {}
        except Exception:
            return {}
    df["extra_dict"] = df["extra"].apply(_parse_extra)
    # cast timestamp -> datetime
    def _parse_ts(x):
        try:
            # timestamps are ISO strings written by logger
            return dt.datetime.fromisoformat(str(x).replace("Z",""))
        except Exception:
            return pd.NaT
    df["ts"] = df["timestamp"].apply(_parse_ts)
    df = df.sort_values("ts").reset_index(drop=True)
    return df

def read_equity():
    if not os.path.exists(EQUITY_CSV):
        return pd.DataFrame(columns=["timestamp","balance","equity"])
    df = pd.read_csv(EQUITY_CSV)
    # timestamps are unix seconds (int)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(None)
    return df.sort_values("dt").reset_index(drop=True)

def realized_pnl_summary(trades: pd.DataFrame):
    """
    We log:
      BUY rows  -> qty > 0, extra has {"score": ...}
      SELL rows -> qty == 0, extra has {"pnl": <float>, "reason": "..."}
    So realized PnL = sum of SELL.extra_dict['pnl'].
    We’ll also pair BUY->SELL per symbol to get holding time.
    """
    if trades.empty:
        return {
            "realized_pnl": 0.0,
            "n_roundtrips": 0,
            "win_rate": 0.0,
            "per_symbol": pd.DataFrame(columns=["symbol","trades","wins","losses","realized_pnl","avg_hold_min"])
        }

    sells = trades[(trades["side"]=="SELL")]
    sells["pnl"] = sells["extra_dict"].apply(lambda d: float(d.get("pnl", 0.0)) if isinstance(d, dict) else 0.0)

    # Pair BUY->SELL per symbol to estimate hold time
    per_symbol_stats = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "hold_minutes": []
    })
    # simple queue of OPEN buys per symbol (bot closes whole position on sell)
    open_buys = defaultdict(deque)

    for _, row in trades.iterrows():
        sym = row["symbol"]
        if row["side"] == "BUY":
            open_buys[sym].append(row)
        elif row["side"] == "SELL":
            pnl = float(row.get("pnl", row["extra_dict"].get("pnl", 0.0)) if isinstance(row.get("extra_dict"), dict) else 0.0)
            # pop the last (or earliest) open buy if any
            buy_row = open_buys[sym].popleft() if open_buys[sym] else None
            hold_min = None
            if buy_row is not None and pd.notna(buy_row["ts"]) and pd.notna(row["ts"]):
                hold_min = (row["ts"] - buy_row["ts"]).total_seconds() / 60.0

            ps = per_symbol_stats[sym]
            ps["trades"] += 1
            ps["pnl"] += pnl
            if pnl >= 0:
                ps["wins"] += 1
            else:
                ps["losses"] += 1
            if hold_min is not None:
                ps["hold_minutes"].append(hold_min)

    # Aggregate
    total_realized = float(sells["pnl"].sum()) if not sells.empty else 0.0
    n_roundtrips = int(sells.shape[0])
    wins = int((sells["pnl"] > 0).sum())
    win_rate = (wins / n_roundtrips) * 100.0 if n_roundtrips else 0.0

    rows = []
    for sym, ps in per_symbol_stats.items():
        avg_hold = (sum(ps["hold_minutes"])/len(ps["hold_minutes"])) if ps["hold_minutes"] else float("nan")
        rows.append({
            "symbol": sym,
            "trades": ps["trades"],
            "wins": ps["wins"],
            "losses": ps["losses"],
            "realized_pnl": round(ps["pnl"], 8),
            "avg_hold_min": (round(avg_hold, 2) if not math.isnan(avg_hold) else None)
        })
    per_symbol_df = pd.DataFrame(rows).sort_values(["realized_pnl","trades"], ascending=[False, False])

    return {
        "realized_pnl": round(total_realized, 8),
        "n_roundtrips": n_roundtrips,
        "win_rate": round(win_rate, 2),
        "per_symbol": per_symbol_df
    }

def compute_max_drawdown(equity: pd.DataFrame):
    if equity.empty or "equity" not in equity:
        return 0.0, None, None
    eq = equity["equity"].astype(float).values
    peak = -float("inf")
    max_dd = 0.0
    peak_i = trough_i = 0
    for i, val in enumerate(eq):
        if val > peak:
            peak = val
            peak_i = i
        dd = (peak - val)
        if dd > max_dd:
            max_dd = dd
            trough_i = i
    # Also compute % relative to peak when MDD occurred
    if peak > 0:
        dd_pct = (max_dd / peak) * 100.0
    else:
        dd_pct = 0.0
    start_dt = equity["dt"].iloc[peak_i] if "dt" in equity and len(equity)>peak_i else None
    end_dt = equity["dt"].iloc[trough_i] if "dt" in equity and len(equity)>trough_i else None
    return round(dd_pct, 2), start_dt, end_dt

def main(save_csv=False):
    trades = read_trades()
    equity = read_equity()

    pnl_summary = realized_pnl_summary(trades)
    mdd_pct, mdd_from, mdd_to = compute_max_drawdown(equity)

    print("\n=== SESSION SUMMARY ===")
    print(f"Trades file:  {TRADES_CSV if os.path.exists(TRADES_CSV) else '(missing)'}")
    print(f"Equity file:  {EQUITY_CSV if os.path.exists(EQUITY_CSV) else '(missing)'}")

    print("\n-- Realized PnL --")
    print(f"  Round trips  : {pnl_summary['n_roundtrips']}")
    print(f"  Win rate     : {pnl_summary['win_rate']} %")
    print(f"  Realized PnL : {pnl_summary['realized_pnl']:.8f}")

    if not equity.empty:
        start = equity['dt'].iloc[0]
        end = equity['dt'].iloc[-1]
        start_bal = float(equity['balance'].iloc[0])
        end_bal = float(equity['balance'].iloc[-1])
        print("\n-- Equity (from equity_curve.csv) --")
        print(f"  Period       : {start}  →  {end}")
        print(f"  Start Balance: {start_bal:.2f}")
        print(f"  End Balance  : {end_bal:.2f}")
        print(f"  Max Drawdown : {mdd_pct:.2f}% (peak {mdd_from} → trough {mdd_to})")

    ps = pnl_summary["per_symbol"]
    if not ps.empty:
        print("\n-- Per-Symbol --")
        print(ps.to_string(index=False))
        if save_csv:
            out_path = os.path.join(LOG_DIR, "per_symbol_summary.csv")
            ps.to_csv(out_path, index=False)
            print(f"\nSaved per-symbol summary to {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze trading session logs.")
    parser.add_argument("--save-csv", action="store_true", help="Save per-symbol summary CSV next to logs.")
    args = parser.parse_args()
    main(save_csv=args.save_csv)
