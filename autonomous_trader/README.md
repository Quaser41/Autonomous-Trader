# Autonomous Trader

## Resetting Paper Trading Balance

To start a fresh paper-trading session:

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
