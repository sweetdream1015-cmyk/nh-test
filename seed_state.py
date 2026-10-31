"""Write the initial ladder pair when no state file exists.

Prices are mapped through each ticker's own ladder. The file stores indexes.
The stored sell index is the lower sell. The buy index is the upper buy.
A reset then sells that rung and the next one up, and buys that rung and the
next one down.

SEED_FORCE=KORU:13.5:11.3,SOXL:113:94 overwrites only those tickers.
Other tickers and handled fills stay as they are. double_used on a forced
ticker is kept unless SEED_FLAGS replaces it.
KORU's ladder has extra rungs below 9.4, so its indexes are not the NAIL/SOXL indexes.

SEED_FLAGS sets marks only. It never places or cancels.
Clauses are separated by ';':
  exception_filled=true|false
  TICKER.double_used=price,price   (replaces that ticker's list; empty clears)
Example: exception_filled=true;SOXL.double_used=72
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


def _load_or_seed():
    if os.path.isfile(bot._STATE_PATH):
        existing = bot._load_state()
        if existing is None:
            return None
        return bot._clean_state(existing)
    state = bot._fresh_state()
    if not _seed_all(state):
        return None
    return state


def _apply_force(state, parsed):
    for ticker, (sell_idx, buy_idx) in parsed.items():
        prev = state["tickers"].get(ticker) or {}
        used = prev.get("double_used") if isinstance(prev, dict) else []
        state["tickers"][ticker] = {
            "sell": sell_idx,
            "buy": buy_idx,
            "double_used": list(used or []),
        }


def _parse_flags(raw):
    """Parse SEED_FLAGS. None means the text is not valid.

    Returns (exception_filled or None, {ticker: [indexes]}).
    """
    exception_filled = None
    seen_exception = False
    used = {}
    for part in raw.split(";"):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            return None
        key, value = piece.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key.lower() == "exception_filled":
            if seen_exception:
                return None
            seen_exception = True
            if value.lower() == "true":
                exception_filled = True
            elif value.lower() == "false":
                exception_filled = False
            else:
                return None
            continue
        suffix = ".double_used"
        if not key.lower().endswith(suffix):
            return None
        ticker = key[: -len(suffix)].strip().upper()
        if ticker not in ladders._CFG or ticker in used:
            return None
        indexes = []
        if value:
            for bit in value.split(","):
                price = ladders._float(bit.strip())
                if price is None:
                    return None
                index = ladders._level(ladders._CFG[ticker]["ladder"], price)
                if index is None or index in indexes:
                    return None
                indexes.append(index)
        used[ticker] = indexes
    if not seen_exception and not used:
        return None
    return exception_filled, used


def _apply_flags(state, flags):
    exception_filled, used = flags
    if exception_filled is not None:
        state["exception_filled"] = exception_filled
    for ticker, indexes in used.items():
        pair = state["tickers"].get(ticker)
        if not isinstance(pair, dict) or "sell" not in pair:
            return False
        pair["double_used"] = list(indexes)
    return True


def _update(force_raw, flags_raw):
    parsed = _parse_force(force_raw) if force_raw else {}
    if force_raw and not parsed:
        print("ERROR config")
        return 1
    flags = _parse_flags(flags_raw) if flags_raw else None
    if flags_raw and flags is None:
        print("ERROR config")
        return 1
    state = _load_or_seed()
    if state is None:
        print("ERROR config")
        return 1
    if parsed:
        _apply_force(state, parsed)
    if flags is not None and not _apply_flags(state, flags):
        print("ERROR config")
        return 1
    bot._save_json(bot._STATE_PATH, bot._clean_state(state))
    print("OK force" if force_raw else "OK flags")
    return 0


def main():
    force_raw = os.environ.get("SEED_FORCE", "").strip()
    flags_raw = os.environ.get("SEED_FLAGS", "").strip()
    if force_raw or flags_raw:
        return _update(force_raw, flags_raw)
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
