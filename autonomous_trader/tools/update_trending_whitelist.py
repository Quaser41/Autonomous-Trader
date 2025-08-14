#!/usr/bin/env python3
"""Update runtime whitelist using combined trending sources.

Fetches symbols from CoinMarketCap, DEXTools and Reddit via
``fetch_all_trending_validated`` and writes them to
``data/runtime/runtime_whitelist.json`` using ``save_whitelist``.

This utility can be run manually or scheduled via cron to keep the bot's
tradable universe fresh:

    $ python tools/update_trending_whitelist.py

Example cron entry to refresh every 15 minutes:

    */15 * * * * /usr/bin/python /path/to/tools/update_trending_whitelist.py
"""
from utils.trending_feed import fetch_all_trending_validated, save_whitelist


def main() -> None:
    symbols = fetch_all_trending_validated()
    if symbols:
        save_whitelist(symbols)
    else:
        print("[TREND] No trending symbols found.")


if __name__ == "__main__":
    main()
