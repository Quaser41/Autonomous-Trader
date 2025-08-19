# Autonomous Trader

This project focuses solely on cryptocurrency markets. Legacy stock trading
features and the Alpaca integration have been removed.

## Resetting Paper Trading Balance

By default, the bot preserves your paper-trading balance across runs
(`risk.reset_balance` is `false`). To start a fresh session:

1. Delete the stored balance file:
   ```bash
   rm data/performance/balance.txt
   ```
   The next run will recreate it using the `dry_run_wallet` value.

2. **Or** set the reset flag in the configuration. In `config/config.json`:
   ```json
   {
     "risk": {
       "reset_balance": true
     }
   }
   ```
   On startup the bot will ignore any existing balance and initialize the wallet
   from `risk.dry_run_wallet`.

After resetting, set `reset_balance` back to `false` if you want to persist the
balance across runs.

## Trailing Stop Configuration

The `trailing_stop` section of `config/config.json` controls how open
positions lock in profit:

```
"trailing_stop": {
  "enable": true,
  "activate_profit_pct": 0.003,
  "breakeven_pct": 0.006,
  "trail_pct": 0.01,
  "atr_trail_multiplier": 1.0
}
```

- **activate_profit_pct** – start trailing only after this profit is reached.
- **breakeven_pct** – once price exceeds this, the stop moves to entry.
- **trail_pct** – fallback trailing distance if ATR data is unavailable.
- **atr_trail_multiplier** – multiplier applied to ATR percent to compute the
  trailing distance.

When active, the bot trails the stop using `ATR * atr_trail_multiplier` from
the position peak.

## Updating the Trending Whitelist

The bot can trade a dynamic universe of symbols derived from trending sources
(CoinMarketCap, DEXTools and Reddit). To refresh this list outside the bot
runtime, run:

```bash
python tools/update_trending_whitelist.py
```

This writes the combined symbols to `data/runtime/runtime_whitelist.json`,
which `load_crypto_whitelist()` automatically reads on the next cycle. For
continuous updates, schedule the script via cron, for example:

```
*/15 * * * * /usr/bin/python /path/to/tools/update_trending_whitelist.py
```

The main bot (`main.py` or `bot_runner.py`) already starts a background thread
that performs the same refresh every few minutes when it is running.

## Equity Reconciliation

Use `tools/reconcile_equity.py` to verify that the stored wallet balance matches the cumulative per-symbol PnL. Schedule the script to run once per day (e.g., via cron):

```
0 0 * * * /usr/bin/python /path/to/tools/reconcile_equity.py
```

Any discrepancy is logged to `data/logs/events.log` through the standard `Notifier`.
