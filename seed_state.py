"""Write the initial ladder pair when no state file exists.

Prices are mapped to ladder indexes. The file stores indexes only.
"""

import os
import sys

import ladder_bot as bot
import ladder_dryrun as ladders

# Confirmed starting pair. Not inferred from open orders.
_SEED_PRICES = {
    "NAIL": (33, 28),
    "KORU": (12.5, 10.3),
    "SOXL": (103, 86),
}


def main():
    existing = bot._load_state()
    if existing and existing.get("tickers"):
        print("OK skip")
        return 0
    state = bot._fresh_state()
    for ticker, prices in _SEED_PRICES.items():
        ladder = ladders._CFG[ticker]["ladder"]
        sell_idx = ladders._level(ladder, prices[0])
        buy_idx = ladders._level(ladder, prices[1])
        if not bot._valid_pair(ticker, sell_idx, buy_idx):
            print("ERROR config")
            return 1
        state["tickers"][ticker] = {
            "sell": sell_idx,
            "buy": buy_idx,
            "double_used": [],
        }
    bot._save_json(bot._STATE_PATH, bot._clean_state(state))
    print("OK seed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("ERROR unexpected")
        sys.exit(1)
