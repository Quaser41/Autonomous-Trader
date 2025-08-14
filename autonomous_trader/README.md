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
