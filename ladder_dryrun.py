"""Dry-run ladder check. Inquiry calls only; never places, cancels, or modifies.

Reads open orders and compares them to the desired two-sell / two-buy set.
Prints counts per ticker. Does not print prices, quantities, order numbers,
account numbers, balances, bodies, or ISINs.

Call pattern matches gb_readonly_check.py:
- POST /oauth2/token
- POST /n2/acctinfo, acct_type 01 (Output_0)
- POST /gbstock/inquiry/v1/balance (Output_1, not printed)
- POST /gbstock/inquiry/v1/unexecuted ost_cns_dit 2 (Output_0)

Unexecuted Output_0 uses iem_cd for the instrument, oss_sby_dit_cd
(1 sell, 2 buy), fc_orr_uit_pr, orr_qty, and ny_cns_orr_qty.
"""

import os
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import gb_readonly_check as gb

# Ladders copied from the ticker files. KORU adds 8.6, 7.9, and 7.2 below
# 9.4. Rungs below 7.2 are not stored on the list: each one is about 10%
# under the previous rung and uses a negative index, so 7.2 stays index 0.
_NAIL_LADDER = [
    9.4, 10.3, 11.3, 12.5, 13.5, 15, 16.5, 18, 20, 22, 24, 26, 28, 30, 33,
    36, 39, 42, 46, 50, 55, 60, 66, 72, 79, 86, 94, 103, 113, 125, 135, 150,
    165, 180, 200, 220,
]
_KORU_LADDER = [
    7.2, 7.9, 8.6, 9.4, 10.3, 11.3, 12.5, 13.5, 15, 16.5, 18, 20, 22, 24, 26,
    28, 30, 33, 36, 39, 42, 46, 50, 55, 60, 66, 72,
]
_SOXL_LADDER = [
    9.4, 10.3, 11.3, 12.5, 13.5, 15, 16.5, 18, 20, 22, 24, 26, 28, 30, 33,
    36, 39, 42, 46, 50, 55, 60, 66, 72, 79, 86, 94, 103, 113, 125, 135, 150,
    165, 180, 200, 220,
]

# iem_cd on unexecuted rows is the ISIN. Never print these values.
_ISIN = {
    "US25490K5965": "NAIL",
    "US25461A3876": "KORU",
    "US25459W4583": "SOXL",
}


def _buy_qty(base, threshold, at_threshold=None, low_at=None, low_qty=None):
    """First-buy size. Above `threshold` is `base`.

    Exactly `threshold` is `at_threshold` when that is set. At or below
    `low_at` (default `threshold`) is `low_qty` (default `base * 2`).
    A price between those two stays at `base`.
    """

    def qty(price):
        if at_threshold is not None and abs(price - threshold) < 0.051:
            return at_threshold
        floor = threshold if low_at is None else low_at
        if price <= floor:
            return base * 2 if low_qty is None else low_qty
        return base

    return qty


def _sell_shares(cfg):
    """Share count for each sell rung.

    Tickers without `sell_qty` sell `base`. `base` is also the ordinary
    buy size and the size of a level already in double_used.
    """
    if "sell_qty" in cfg:
        return cfg["sell_qty"]
    return cfg["base"]


_CFG = {
    # 22 is 20 (2x). At or below 20 is 40 (4x). Above 22 stays 10.
    "NAIL": {
        "ladder": _NAIL_LADDER,
        "base": 10,
        "buy_qty": _buy_qty(10, 22, at_threshold=20, low_at=20, low_qty=40),
    },
    "KORU": {"ladder": _KORU_LADDER, "base": 1, "buy_qty": _buy_qty(1, 9.4)},
    # Sells are 2. base stays 1: buys above 72, and a level already in
    # double_used, are 1. First buy at 72, and at 66 or below, is 4.
    "SOXL": {
        "ladder": _SOXL_LADDER,
        "base": 1,
        "sell_qty": 2,
        "buy_qty": _buy_qty(1, 72, at_threshold=4, low_at=66, low_qty=4),
    },
}


def _text(value):
    if isinstance(value, str):
        return "".join(ch for ch in value if ord(ch) >= 32).strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _float(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _level(ladder, price):
    for index, level in enumerate(ladder):
        if abs(level - price) < 0.051:
            return index
    return None


def _as_decimal(price):
    if isinstance(price, Decimal):
        return price
    return Decimal(str(price))


def _round_tenth(raw):
    return raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _tenth_unclear(raw):
    """True when `raw` sits exactly halfway between two one-decimal prices."""
    return abs(raw - _round_tenth(raw)) == Decimal("0.05")


def _next_rung(price, up):
    """Next unknown price, about 10% from `price`, as one decimal.

    The primary step is `price * 1.1` upward or `price / 1.1` downward.
    A value exactly halfway between tenths is unclear, so that case uses
    `price * 1.1` upward or `price * 0.9` downward, then the same rounding.
    """
    base = _as_decimal(price)
    if up:
        primary = base * Decimal("1.1")
        fallback = primary
    else:
        primary = base / Decimal("1.1")
        fallback = base * Decimal("0.9")
    chosen = fallback if _tenth_unclear(primary) else primary
    rounded = _round_tenth(chosen)
    if rounded <= 0:
        return None
    if up and rounded <= base:
        return None
    if not up and rounded >= base:
        return None
    return rounded


def _down_price(start, steps):
    """`steps` rungs under `start`. None when the price cannot keep falling."""
    if isinstance(steps, bool) or not isinstance(steps, int):
        return None
    if steps < 1 or steps > 80:
        return None
    current = _as_decimal(start)
    for _ in range(steps):
        nxt = _next_rung(current, up=False)
        if nxt is None:
            return None
        current = nxt
    return float(current)


def _price_at(ticker, index):
    """Coded ladder price, or a generated KORU price below 7.2.

    Index 0 is the first coded rung. Negative indexes walk down from it
    and do not shift the coded indexes stored in ladder state.
    """
    if ticker not in _CFG or isinstance(index, bool) or not isinstance(index, int):
        return None
    ladder = _CFG[ticker]["ladder"]
    if 0 <= index < len(ladder):
        return ladder[index]
    if index < 0 and ticker == "KORU":
        return _down_price(ladder[0], -index)
    return None


def _level_of(ticker, price):
    """Ladder index for `price`, including KORU rungs generated below 7.2."""
    if ticker not in _CFG:
        return None
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    ladder = _CFG[ticker]["ladder"]
    found = _level(ladder, float(price))
    if found is not None:
        return found
    if ticker != "KORU" or float(price) >= ladder[0]:
        return None
    current = _as_decimal(ladder[0])
    target = float(price)
    for step in range(1, 81):
        nxt = _next_rung(current, up=False)
        if nxt is None:
            return None
        rung = float(nxt)
        if abs(rung - target) < 0.051:
            return -step
        if rung < target - 0.051:
            return None
        current = nxt
    return None


def _identify(row):
    iem = _text(row.get("iem_cd")).upper()
    if iem in _CFG:
        return iem
    if iem in _ISIN:
        return _ISIN[iem]
    ticker = _text(row.get("tck_iem_cd")).upper()
    if ticker in _CFG:
        return ticker
    return ""


def _side(row):
    code = _text(row.get("oss_sby_dit_cd"))
    if code == "1":
        return "sell"
    if code == "2":
        return "buy"
    return ""


def _open_qty(row):
    if row.get("ny_cns_orr_qty") is not None:
        remaining = _float(row.get("ny_cns_orr_qty"))
        if remaining is None or remaining <= 0:
            return 0
        return int(round(remaining))
    ordered = _float(row.get("orr_qty"))
    if ordered is None or ordered <= 0:
        return 0
    return int(round(ordered))


def _quiet_emit(name, status, payload):
    rsp_cd, rsp_msg = gb._rsp(payload)
    if status != 200:
        print(f"{name} HTTP {status}")
    return rsp_cd, rsp_msg


def plan(ticker, orders):
    """Compare open orders to the desired pair. `orders` are side/price/qty."""
    cfg = _CFG[ticker]
    buy_qty = cfg["buy_qty"]
    sell_qty = _sell_shares(cfg)
    ignored = 0
    buys = []
    sells = []
    for order in orders:
        price = order.get("price")
        qty = order.get("qty")
        side = order.get("side")
        if not isinstance(price, (int, float)) or isinstance(price, bool):
            ignored += 1
            continue
        index = _level_of(ticker, float(price))
        if index is None or side not in ("buy", "sell") or not isinstance(qty, int) or qty <= 0:
            ignored += 1
            continue
        if side == "buy":
            buys.append((index, qty))
        else:
            sells.append((index, qty))

    result = {
        "open_buys": len(buys),
        "open_sells": len(sells),
        "ignored": ignored,
        "would_place": 0,
        "would_cancel": 0,
        "ok": False,
    }
    if not buys or not sells:
        return result

    buy_idx = max(index for index, _qty in buys)
    sell_idx = min(index for index, _qty in sells)
    buy_price = _price_at(ticker, buy_idx)
    sell_price = _price_at(ticker, sell_idx)
    if buy_price is None or sell_price is None:
        return result
    desired = [("sell", sell_idx, sell_qty), ("buy", buy_idx, buy_qty(buy_price))]
    up_price = _price_at(ticker, sell_idx + 1)
    if up_price is not None:
        desired.append(("sell", sell_idx + 1, sell_qty))
    down_price = _price_at(ticker, buy_idx - 1)
    if down_price is not None:
        desired.append(("buy", buy_idx - 1, buy_qty(down_price)))

    actual = [("buy", index, qty) for index, qty in buys]
    actual.extend(("sell", index, qty) for index, qty in sells)
    third = buy_idx - 2
    third_price = _price_at(ticker, third)
    if third_price is not None:
        exempt = ("buy", third, buy_qty(third_price))
        if exempt in actual:
            actual.remove(exempt)

    wanted = {}
    have = {}
    for item in desired:
        wanted[item] = wanted.get(item, 0) + 1
    for item in actual:
        have[item] = have.get(item, 0) + 1
    place = 0
    cancel = 0
    for item in set(wanted) | set(have):
        place += max(0, wanted.get(item, 0) - have.get(item, 0))
        cancel += max(0, have.get(item, 0) - wanted.get(item, 0))
    result["would_place"] = place
    result["would_cancel"] = cancel
    result["ok"] = place == 0 and cancel == 0
    return result


def _rows_for(ticker, rows):
    orders = []
    for row in rows:
        if not isinstance(row, dict) or _identify(row) != ticker:
            continue
        country = _text(row.get("fc_sec_trd_nat_cd"))
        if country and country != "200":
            continue
        orders.append(
            {
                "side": _side(row),
                "price": _float(row.get("fc_orr_uit_pr")),
                "qty": _open_qty(row),
            }
        )
    return orders


def _print_ticker(ticker, orders):
    result = plan(ticker, orders)
    print(f"{ticker} open buys {result['open_buys']}")
    print(f"{ticker} open sells {result['open_sells']}")
    print(f"{ticker} ignored {result['ignored']}")
    print(f"{ticker} would_place {result['would_place']}")
    print(f"{ticker} would_cancel {result['would_cancel']}")
    print(f"{ticker} ok {'yes' if result['ok'] else 'no'}")


def main():
    gb._emit = _quiet_emit
    key = os.environ.get("NHPLUG_APP_KEY", "")
    secret = os.environ.get("NHPLUG_APP_SECRET", "")
    if not key.strip() or not secret.strip():
        print("secrets missing")
        return 1
    gb._key = key
    gb._secret = secret
    gb._secrets.extend((key, secret))

    token = gb._issue_token(key, secret)
    if not token:
        return 1
    gb._token = token

    status, _, payload = gb._post_bearer(gb.ACCTINFO_URL, gb._json_body({}), {})
    if status is None:
        print("acctinfo request failed")
        return 1
    if status != 200:
        print(f"acctinfo HTTP {status}")
        return 1
    account = gb._select_account(payload)
    if not account:
        return 1

    balance = gb._paged(
        "balance",
        gb.BALANCE_URL,
        {
            "act_no": account,
            "qut_iqr_dit_cd": "9",
            "fc_sec_trd_nat_cd": "200",
            "cur_cd": "USD",
        },
        "Output_1",
    )
    if balance is None:
        return 1

    unexecuted = gb._paged(
        "unexecuted",
        gb.UNEXECUTED_URL,
        {
            "orr_dt": datetime.now(gb._KST).strftime("%Y%m%d"),
            "act_no": account,
            "oss_sby_dit_cd": "0",
            "sot_dit": "0",
            "ost_cns_dit": "2",
        },
        "Output_0",
    )
    if unexecuted is None:
        return 1

    for ticker in ("NAIL", "KORU", "SOXL"):
        _print_ticker(ticker, _rows_for(ticker, unexecuted))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("request failed")
        sys.exit(1)
