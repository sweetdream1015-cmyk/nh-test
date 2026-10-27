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

import gb_readonly_check as gb

# Ladders copied from the ticker files. KORU's step below 9.4 is 8.5
# (90% of 9.4, rounded to one decimal) instead of 8.6.
_NAIL_LADDER = [
    9.4, 10.3, 11.3, 12.5, 13.5, 15, 16.5, 18, 20, 22, 24, 26, 28, 30, 33,
    36, 39, 42, 46, 50, 55, 60, 66, 72, 79, 86, 94, 103, 113, 125, 135, 150,
    165, 180, 200, 220,
]
_KORU_LADDER = [
    7.2, 7.9, 8.5, 9.4, 10.3, 11.3, 12.5, 13.5, 15, 16.5, 18, 20, 22, 24, 26,
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


def _buy_qty(base, threshold, at_threshold=None):
    def qty(price):
        if at_threshold is not None and abs(price - threshold) < 0.051:
            return at_threshold
        if price <= threshold:
            return base * 2
        return base

    return qty


_CFG = {
    "NAIL": {"ladder": _NAIL_LADDER, "base": 10, "buy_qty": _buy_qty(10, 22)},
    "KORU": {"ladder": _KORU_LADDER, "base": 1, "buy_qty": _buy_qty(1, 9.4)},
    "SOXL": {
        "ladder": _SOXL_LADDER,
        "base": 1,
        "buy_qty": _buy_qty(1, 72, at_threshold=4),
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
    ladder = cfg["ladder"]
    buy_qty = cfg["buy_qty"]
    base = cfg["base"]
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
        index = _level(ladder, float(price))
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
    desired = [("sell", sell_idx, base), ("buy", buy_idx, buy_qty(ladder[buy_idx]))]
    if sell_idx + 1 < len(ladder):
        desired.append(("sell", sell_idx + 1, base))
    if buy_idx - 1 >= 0:
        desired.append(("buy", buy_idx - 1, buy_qty(ladder[buy_idx - 1])))

    actual = [("buy", index, qty) for index, qty in buys]
    actual.extend(("sell", index, qty) for index, qty in sells)
    third = buy_idx - 2
    if third >= 0:
        exempt = ("buy", third, buy_qty(ladder[third]))
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
