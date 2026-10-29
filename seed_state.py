"""Write the initial ladder pair when no state file exists.

Prices are mapped through each ticker's own ladder. The file stores indexes.
The stored sell index is the lower sell. The buy index is the upper buy.
A reset then sells that rung and the next one up, and buys that rung and the
next one down.

SEED_FORCE=KORU:13.5:11.3,SOXL:113:94 overwrites only those tickers.
Other tickers, handled fills, and exception_filled stay as they are.
KORU's ladder has extra rungs below 9.4, so its indexes are not the NAIL/SOXL indexes.
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


def _pair_from_prices(ticker, sell_px, buy_px):
    ladder = ladders._CFG[ticker]["ladder"]
    sell_idx = ladders._level(ladder, sell_px)
    buy_idx = ladders._level(ladder, buy_px)
    if not bot._valid_pair(ticker, sell_idx, buy_idx):
        return None
    return sell_idx, buy_idx


def _parse_force(raw):
    found = {}
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        bits = [item.strip() for item in piece.split(":")]
        if len(bits) != 3:
            return None
        ticker = bits[0].upper()
        if ticker not in ladders._CFG or ticker in found:
            return None
        sell_px = ladders._float(bits[1])
        buy_px = ladders._float(bits[2])
        if sell_px is None or buy_px is None:
            return None
        pair = _pair_from_prices(ticker, sell_px, buy_px)
        if pair is None:
            return None
        found[ticker] = pair
    return found or None


def _seed_all(state):
    for ticker, prices in _SEED_PRICES.items():
        pair = _pair_from_prices(ticker, prices[0], prices[1])
        if pair is None:
            return False
        state["tickers"][ticker] = {
            "sell": pair[0],
            "buy": pair[1],
            "double_used": [],
        }
    return True


def _force(raw):
    parsed = _parse_force(raw)
    if not parsed:
        print("ERROR config")
        return 1
    if os.path.isfile(bot._STATE_PATH):
        existing = bot._load_state()
        if existing is None:
            print("ERROR config")
            return 1
        state = bot._clean_state(existing)
    else:
        state = bot._fresh_state()
        if not _seed_all(state):
            print("ERROR config")
            return 1
    for ticker, (sell_idx, buy_idx) in parsed.items():
        prev = state["tickers"].get(ticker) or {}
        used = prev.get("double_used") if isinstance(prev, dict) else []
        state["tickers"][ticker] = {
            "sell": sell_idx,
            "buy": buy_idx,
            "double_used": list(used or []),
        }
    bot._save_json(bot._STATE_PATH, bot._clean_state(state))
    print("OK force")
    return 0


def main():
    raw = os.environ.get("SEED_FORCE", "").strip()
    if raw:
        return _force(raw)
    existing = bot._load_state()
    if existing and existing.get("tickers"):
        print("OK skip")
        return 0
    state = bot._fresh_state()
    if not _seed_all(state):
        print("ERROR config")
        return 1
    bot._save_json(bot._STATE_PATH, bot._clean_state(state))
    print("OK seed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("ERROR unexpected")
        sys.exit(1)
