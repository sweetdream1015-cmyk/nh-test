"""Order-capable ladder bot. Live trading is off unless LIVE_TRADING is exactly on.

No new order is sent on weekends or in the KST pre-reset windows
08:00-09:10 and 16:00-17:10. Those runs log "skip: time guard" and exit.
Inquiry plus, when live and allowed, limit buy, limit sell, and full cancel.
The modify endpoint is never called. Prints counts and OK/ERROR codes only.

Spec fields used:
- POST /oauth2/token
- POST /n2/acctinfo Output_0 acct_type 01
- POST /gbstock/inquiry/v1/unexecuted
  open rows: ost_cns_dit 2; executions: ost_cns_dit 1
  oss_sby_dit_cd 1 sell / 2 buy, fc_orr_uit_pr, orr_qty, ny_cns_orr_qty,
  cns_qty, orr_no, orr_dt, fc_sec_trd_nat_cd
- POST /gbstock/order/v1/buy and /sell
  fc_sec_trd_nat_cd 200, iem_cd ticker, orr_qty, fc_orr_uit_pr,
  ahi_nmn_pr_tp_cd 00 (limit). Buy also sends wtm_cur_knd_cd 1.
- POST /gbstock/order/v1/cancel
  org_orr_no, all_pat_dit_cd 1 (full cancel)
"""

import hashlib
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gb_readonly_check as gb
import ladder_dryrun as ladders

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
BUY_URL = gb.LIVE + "/gbstock/order/v1/buy"
SELL_URL = gb.LIVE + "/gbstock/order/v1/sell"
CANCEL_URL = gb.LIVE + "/gbstock/order/v1/cancel"
_ORDER_URLS = {BUY_URL, SELL_URL, CANCEL_URL}
_HARD_MAX = {"NAIL": 20, "KORU": 4, "SOXL": 4}
_MAX_NEW = 6
_TOKEN_PATH = os.environ.get("NHPLUG_TOKEN_PATH", ".nhplug-token/token.json")
_STATE_PATH = os.environ.get("LADDER_STATE_PATH", ".ladder-state/state.json")


def _cfg(ticker):
    return ladders._CFG[ticker]


def _mult():
    raw = os.environ.get("MAX_QTY_MULT", "")
    if raw.strip() == "":
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0 or value != value or value == float("inf"):
        return None
    return value


def _allow_set():
    raw = os.environ.get("LIVE_TICKERS", "")
    found = set()
    for part in raw.replace(",", " ").split():
        name = part.strip().upper()
        if name in ladders._CFG:
            found.add(name)
    return found


def _live_flag():
    return os.environ.get("LIVE_TRADING") == "on"


def _now_kst():
    return datetime.now(KST)


def _orders_blocked(now):
    """True when a new order must not be sent.

    Weekends, and weekday 08:00 inclusive to 09:10 exclusive plus 16:00
    inclusive to 17:10 exclusive. The broker wipes resting orders at
    09:00 and 17:00, so those spans are too late to place and too early
    for the 09:10 and 17:10 resets. 09:10 and 17:10 themselves are allowed.
    """
    if now.weekday() >= 5:
        return True
    minutes = now.hour * 60 + now.minute
    if 8 * 60 <= minutes < 9 * 60 + 10:
        return True
    if 16 * 60 <= minutes < 17 * 60 + 10:
        return True
    return False


def _reset_slot(now):
    """KST reset slot after the broker wipe. None means outside the window.

    09:10 inclusive through 16:00 exclusive, then 17:10 through midnight.
    16:00-17:10 is not a slot: orders placed then would be wiped at 17:00
    or would land before the 17:10 reset.
    """
    if now.weekday() >= 5:
        return None
    minutes = now.hour * 60 + now.minute
    if 9 * 60 + 10 <= minutes < 16 * 60:
        return "09"
    if minutes >= 17 * 60 + 10:
        return "17"
    return None


def _in_reset_window(now):
    return _reset_slot(now) is not None


def _in_us_session(now):
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 4 * 60 <= minutes < 20 * 60


def _window_open(mode, now_kst, now_et):
    if mode == "reset":
        return _in_reset_window(now_kst)
    if mode == "intraday":
        return _in_us_session(now_et)
    return False


def _sized(ticker, computed, mult):
    if mult is None:
        return None
    try:
        qty = int(computed) if abs(mult - 1.0) < 1e-9 else int(computed * mult)
    except (TypeError, ValueError, OverflowError):
        return None
    if qty < 1 or qty > _HARD_MAX[ticker]:
        return None
    return qty


def _level_of(ticker, price):
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        return None
    return ladders._level(_cfg(ticker)["ladder"], float(price))


def _split(ticker, orders):
    ignored = 0
    buys = []
    sells = []
    for order in orders:
        index = _level_of(ticker, order.get("price"))
        side = order.get("side")
        if index is None or side not in ("buy", "sell"):
            ignored += 1
            continue
        bucket = buys if side == "buy" else sells
        bucket.append(order)
        order["idx"] = index
    return buys, sells, ignored


def _anchors(ticker, orders):
    buys, sells, ignored = _split(ticker, orders)
    if not buys or not sells:
        return None
    buy_idx = max(order["idx"] for order in buys)
    sell_idx = min(order["idx"] for order in sells)
    ladder = _cfg(ticker)["ladder"]
    if not (0 <= buy_idx < sell_idx < len(ladder) - 1):
        return None
    if buy_idx - 1 < 0:
        return None
    return {
        "buys": buys,
        "sells": sells,
        "ignored": ignored,
        "buy": buy_idx,
        "sell": sell_idx,
    }


def _in_double_zone(ticker, price):
    if ticker == "NAIL":
        return price <= 22
    if ticker == "KORU":
        return price <= 9.4
    if ticker == "SOXL":
        return price <= 72
    return False


def _buy_computed(ticker, index, double_used):
    ladder = _cfg(ticker)["ladder"]
    price = ladder[index]
    qty = _cfg(ticker)["buy_qty"](price)
    if index in set(double_used) and _in_double_zone(ticker, price):
        return _cfg(ticker)["base"]
    return qty


def _desired(ticker, sell_idx, buy_idx, mult, double_used=()):
    if not _valid_pair(ticker, sell_idx, buy_idx):
        return None
    ladder = _cfg(ticker)["ladder"]
    base = _cfg(ticker)["base"]
    specs = [
        ("sell", sell_idx, base),
        ("sell", sell_idx + 1, base),
        ("buy", buy_idx, _buy_computed(ticker, buy_idx, double_used)),
        ("buy", buy_idx - 1, _buy_computed(ticker, buy_idx - 1, double_used)),
    ]
    out = []
    for side, index, computed in specs:
        qty = _sized(ticker, computed, mult)
        if qty is None:
            return None
        out.append({"side": side, "idx": index, "price": ladder[index], "qty": qty})
    return out


def _same_level(left, right):
    return left.get("side") == right.get("side") and left.get("idx") == right.get("idx")


def _has_level(orders, side, index):
    return any(order.get("side") == side and order.get("idx") == index for order in orders)


def intraday_adds(ticker, open_orders, fills, mult):
    """Adds after confirmed fills. None means the ticker needs review."""
    if not fills:
        return []
    if {item["side"] for item in fills} == {"buy", "sell"}:
        return None
    buys, sells, _ignored = _split(ticker, [dict(order) for order in open_orders])
    ladder = _cfg(ticker)["ladder"]
    buy_qty = _cfg(ticker)["buy_qty"]
    base = _cfg(ticker)["base"]
    side = fills[0]["side"]
    ordered = sorted(fills, key=lambda item: item["idx"], reverse=(side == "buy"))
    adds = []
    for _fill in ordered:
        if not buys or not sells:
            return None
        if side == "sell":
            sell_idx = max(order["idx"] for order in sells) + 1
            buy_idx = max(order["idx"] for order in buys) + 1
            if sell_idx >= len(ladder) or buy_idx >= len(ladder):
                return None
            if buy_idx >= min(order["idx"] for order in sells):
                return None
            pair = [("sell", sell_idx, base), ("buy", buy_idx, buy_qty(ladder[buy_idx]))]
        else:
            buy_idx = min(order["idx"] for order in buys) - 1
            sell_idx = min(order["idx"] for order in sells) - 1
            if buy_idx < 0 or sell_idx < 0:
                return None
            if sell_idx <= max(order["idx"] for order in buys):
                return None
            pair = [
                ("buy", buy_idx, buy_qty(ladder[buy_idx])),
                ("sell", sell_idx, base),
            ]
        for add_side, index, computed in pair:
            qty = _sized(ticker, computed, mult)
            if qty is None:
                return None
            adds.append(
                {"side": add_side, "idx": index, "price": ladder[index], "qty": qty}
            )
        sells.append({"side": "sell", "idx": sell_idx})
        buys.append({"side": "buy", "idx": buy_idx})
    unique = []
    for item in adds:
        if not _has_level(unique, item["side"], item["idx"]):
            unique.append(item)
    return unique


def _fill_key(ticker, row):
    raw = "|".join(
        (
            ticker,
            ladders._text(row.get("oss_sby_dit_cd")),
            ladders._text(row.get("orr_dt")),
            ladders._text(row.get("orr_no")),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_row(row):
    ticker = ladders._identify(row)
    if not ticker:
        return None
    country = ladders._text(row.get("fc_sec_trd_nat_cd"))
    if country and country != "200":
        return None
    price = ladders._float(row.get("fc_orr_uit_pr"))
    qty = ladders._open_qty(row)
    status = ladders._text(row.get("oss_ato_orr_sts_cd"))
    if status == "2":
        return None
    filled = ladders._float(row.get("cns_qty"))
    cancelled = ladders._float(row.get("can_qty"))
    return {
        "ticker": ticker,
        "side": ladders._side(row),
        "price": price,
        "qty": qty,
        "filled_qty": 0 if filled is None else filled,
        "cancelled_qty": 0 if cancelled is None else cancelled,
        "idx": _level_of(ticker, price),
        "key": _fill_key(ticker, row),
        "orr_no": row.get("orr_no"),
    }


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save_json(path, data):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    os.chmod(path, 0o600)


def _load_state():
    data = _load_json(_STATE_PATH)
    if not data or data.get("version") not in (1, 2):
        return None
    if not isinstance(data.get("handled"), list):
        return None
    return data


def _fresh_state():
    return {
        "version": 2,
        "tickers": {},
        "handled": [],
        "exception_filled": False,
        "reset_done": {"09": "", "17": ""},
    }


def _load_token():
    data = _load_json(_TOKEN_PATH)
    if not data:
        return ""
    token = data.get("access_token")
    expires = data.get("expires_at")
    if not isinstance(token, str) or not token.strip():
        return ""
    if not isinstance(expires, (int, float)) or expires <= time.time() + 60:
        return ""
    return token


def _save_token(token, expires_in):
    _save_json(
        _TOKEN_PATH,
        {"access_token": token, "expires_at": time.time() + int(expires_in) - 300},
    )


def _quiet_issue(key, secret):
    query = urllib.parse.urlencode(
        (
            ("appkey", key),
            ("appsecretkey", secret),
            ("grant_type", "client_credentials"),
            ("scope", "oob"),
        )
    )
    status, _headers, payload = gb._post(
        gb.TOKEN_URL + "?" + query,
        b"",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status != 200 or not isinstance(payload, dict):
        return ""
    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return ""
    expires_in = payload.get("expires_in", 86400)
    if not isinstance(expires_in, int):
        expires_in = 86400
    gb._secrets.append(token)
    _save_token(token, expires_in)
    return token


def _pages(url, body, block):
    rows = []
    prev = None
    cts = ""
    flag = ""
    for _ in range(30):
        extra = {}
        if cts:
            extra["cts"] = cts
            if flag:
                extra["cts_flag"] = flag
        status, headers, payload = gb._post_bearer(url, gb._json_body(body), extra)
        if status != 200 or not isinstance(payload, dict):
            return None
        page, state = gb._list_rows(payload, block)
        if page is None or state == "badtype":
            return None
        new_cts = headers.get("cts", "").strip()
        new_flag = headers.get("cts_flag", "").strip()
        if prev is not None and new_cts and new_cts == prev:
            return rows
        rows.extend(page)
        rsp_cd, rsp_msg = gb._rsp(payload)
        if not gb._needs_next(new_flag, new_cts, rsp_cd, prev):
            if new_flag.upper() == "Y" and not new_cts:
                return None
            if rsp_cd in gb._CONTINUE and not new_cts and new_flag.upper() != "N":
                return None
            if state == "absent" and not rows and not gb._empty_ok(rsp_msg):
                return None
            return rows
        prev = new_cts
        cts = new_cts
        flag = new_flag
    return None


def _dates(now_kst):
    today = now_kst.date()
    return [(today - timedelta(days=offset)).strftime("%Y%m%d") for offset in (0, 1)]


def _inquiry(account, now_kst, kind):
    merged = []
    seen = set()
    for orr_dt in _dates(now_kst):
        rows = _pages(
            gb.UNEXECUTED_URL,
            {
                "orr_dt": orr_dt,
                "act_no": account,
                "oss_sby_dit_cd": "0",
                "sot_dit": "0",
                "ost_cns_dit": "2" if kind == "open" else "1",
            },
            "Output_0",
        )
        if rows is None:
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            parsed = _parse_row(row)
            if not parsed:
                continue
            identity = parsed["key"]
            if identity in seen:
                continue
            seen.add(identity)
            if kind == "open" and parsed["qty"] <= 0:
                continue
            if kind == "fill" and (
                parsed["filled_qty"] <= 0 or parsed["cancelled_qty"] > 0
            ):
                continue
            merged.append(parsed)
    return merged


def _order_ok(status, payload):
    if status != 200 or not isinstance(payload, dict):
        return False
    block = payload.get("Output_0")
    if not isinstance(block, dict):
        return False
    number = block.get("orr_no")
    if isinstance(number, bool):
        return False
    if isinstance(number, int):
        return True
    return isinstance(number, str) and number.strip().isdigit()


def _post_order(url, body):
    if url not in _ORDER_URLS or _orders_blocked(_now_kst()):
        return None
    status, _headers, payload = gb._post_bearer(url, gb._json_body(body), {})
    if status == 401:
        return None
    if not _order_ok(status, payload):
        return None
    return True


def _place(account, ticker, side, price, qty, live, counter):
    if not live:
        return False
    index = _level_of(ticker, price)
    if index is None or qty < 1 or qty > _HARD_MAX[ticker]:
        return None
    if counter[0] >= _MAX_NEW:
        return None
    counter[0] += 1
    body = {
        "act_no": account,
        "fc_sec_trd_nat_cd": "200",
        "iem_cd": ticker,
        "orr_qty": int(qty),
        "fc_orr_uit_pr": round(float(price), 2),
        "ahi_nmn_pr_tp_cd": "00",
    }
    if side == "buy":
        body["wtm_cur_knd_cd"] = "1"
        return _post_order(BUY_URL, body)
    if side == "sell":
        return _post_order(SELL_URL, body)
    return None


def _cancel(account, ticker, order, live):
    if not live:
        return False
    if order.get("idx") is None:
        return None
    number = order.get("orr_no")
    if isinstance(number, bool) or number is None:
        return None
    if not isinstance(number, int):
        text = ladders._text(number)
        if not text.isdigit():
            return None
        number = int(text)
    body = {
        "act_no": account,
        "org_orr_no": number,
        "fc_sec_trd_nat_cd": "200",
        "iem_cd": ticker,
        "all_pat_dit_cd": "1",
    }
    return _post_order(CANCEL_URL, body)


def _place_exception(account, live, counter):
    if not live:
        return False
    if counter[0] >= _MAX_NEW:
        return None
    counter[0] += 1
    body = {
        "act_no": account,
        "fc_sec_trd_nat_cd": "200",
        "iem_cd": "SOXL",
        "orr_qty": 100,
        "fc_orr_uit_pr": 75,
        "ahi_nmn_pr_tp_cd": "00",
        "wtm_cur_knd_cd": "1",
    }
    return _post_order(BUY_URL, body)


def _exception_open(orders):
    for order in orders:
        if order.get("ticker") != "SOXL" or order.get("side") != "buy":
            continue
        price = order.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            continue
        if abs(float(price) - 75) < 0.051 and order.get("qty", 0) > 0:
            return True
    return False


def _exception_filled(fills, handled):
    for order in fills:
        if order.get("ticker") != "SOXL" or order.get("side") != "buy":
            continue
        if order.get("key") in handled:
            continue
        price = order.get("price")
        if not isinstance(price, (int, float)) or abs(float(price) - 75) >= 0.051:
            continue
        if order.get("cancelled_qty", 0) > 0:
            continue
        if order.get("filled_qty", 0) > 0 and order.get("idx") is None:
            return True
    return False


def _report(ticker, buys, sells, ignored, place, cancel, review, placed, cancelled):
    print(f"{ticker} open buys {buys}")
    print(f"{ticker} open sells {sells}")
    print(f"{ticker} ignored {ignored}")
    print(f"{ticker} would_place {place}")
    print(f"{ticker} would_cancel {cancel}")
    print(f"{ticker} placed {placed}")
    print(f"{ticker} cancelled {cancelled}")
    if review:
        print(f"NEEDS_REVIEW {ticker}")
    else:
        print(f"{ticker} ok {'yes' if place == 0 and cancel == 0 else 'no'}")


def _valid_pair(ticker, sell_idx, buy_idx):
    if isinstance(sell_idx, bool) or isinstance(buy_idx, bool):
        return False
    if not isinstance(sell_idx, int) or not isinstance(buy_idx, int):
        return False
    ladder = _cfg(ticker)["ladder"]
    return 0 <= buy_idx < sell_idx < len(ladder) - 1 and buy_idx >= 1


def _used_indexes(ticker, raw):
    ladder = _cfg(ticker)["ladder"]
    found = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        index = None
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and 0 <= item < len(ladder):
            index = item
        else:
            price = ladders._float(item)
            if price is not None:
                index = ladders._level(ladder, price)
        if index is not None and index not in found:
            found.append(index)
    return found


def _stored_pair(state, ticker):
    raw = state.get("tickers", {})
    if not isinstance(raw, dict):
        return None
    pair = raw.get(ticker)
    if not isinstance(pair, dict):
        return None
    sell_idx = pair.get("sell")
    buy_idx = pair.get("buy")
    if not _valid_pair(ticker, sell_idx, buy_idx):
        return None
    return {
        "sell": sell_idx,
        "buy": buy_idx,
        "double_used": _used_indexes(ticker, pair.get("double_used")),
    }


def _baseline_ok(ticker, buys, sells, anchor):
    """True when the open book is the inferred pair, plus at most one B-2 buy."""
    if anchor is None:
        return False
    core = {
        ("sell", anchor["sell"]),
        ("sell", anchor["sell"] + 1),
        ("buy", anchor["buy"]),
        ("buy", anchor["buy"] - 1),
    }
    actual = {(order["side"], order["idx"]) for order in buys + sells}
    if not core <= actual:
        return False
    extra = actual - core
    if not extra:
        return True
    third = anchor["buy"] - 2
    return third >= 0 and extra == {("buy", third)}


def _next_pair(ticker, buys, sells, adds):
    buy_idx = [order["idx"] for order in buys]
    sell_idx = [order["idx"] for order in sells]
    for item in adds:
        if item["side"] == "buy":
            buy_idx.append(item["idx"])
        else:
            sell_idx.append(item["idx"])
    if not buy_idx or not sell_idx:
        return None
    pair = {"sell": min(sell_idx), "buy": max(buy_idx)}
    if not _valid_pair(ticker, pair["sell"], pair["buy"]):
        return None
    return pair


def _clean_state(state):
    tickers = {}
    raw = state.get("tickers", {})
    if isinstance(raw, dict):
        for name, pair in raw.items():
            stored = _stored_pair({"tickers": {name: pair}}, name)
            if stored is not None:
                tickers[name] = stored
    handled = []
    for key in state.get("handled", []):
        if (
            isinstance(key, str)
            and len(key) == 64
            and all(ch in "0123456789abcdef" for ch in key)
        ):
            if key not in handled:
                handled.append(key)
    done = {"09": "", "17": ""}
    raw_done = state.get("reset_done")
    if isinstance(raw_done, dict):
        for slot in ("09", "17"):
            value = raw_done.get(slot, "")
            if isinstance(value, str) and len(value) == 8 and value.isdigit():
                done[slot] = value
    return {
        "version": 2,
        "tickers": tickers,
        "handled": handled[-200:],
        "exception_filled": bool(state.get("exception_filled")),
        "reset_done": done,
    }


def _step_sell(ticker, pair):
    sell_idx = pair["sell"] + 1
    buy_idx = pair["buy"] + 1
    if not _valid_pair(ticker, sell_idx, buy_idx):
        return None
    return {
        "sell": sell_idx,
        "buy": buy_idx,
        "double_used": list(pair.get("double_used") or []),
    }


def _step_buy(ticker, pair):
    used = list(pair.get("double_used") or [])
    price = _cfg(ticker)["ladder"][pair["buy"]]
    if _in_double_zone(ticker, price) and pair["buy"] not in used:
        used.append(pair["buy"])
    sell_idx = pair["sell"] - 1
    buy_idx = pair["buy"] - 1
    if not _valid_pair(ticker, sell_idx, buy_idx):
        return None
    return {"sell": sell_idx, "buy": buy_idx, "double_used": used}


def _step_plan(ticker, pair, sell_missing, buy_missing, mult):
    """Move S and B by one per confirmed core fill. None means review."""
    if sell_missing and buy_missing:
        return None
    current = {
        "sell": pair["sell"],
        "buy": pair["buy"],
        "double_used": list(pair.get("double_used") or []),
    }
    if sell_missing:
        missing = set(sell_missing)
        if missing == {current["sell"]}:
            steps = 1
        elif missing == {current["sell"], current["sell"] + 1}:
            steps = 2
        else:
            return None
        for _ in range(steps):
            current = _step_sell(ticker, current)
            if current is None:
                return None
    else:
        missing = set(buy_missing)
        if missing == {current["buy"]}:
            steps = 1
        elif missing == {current["buy"], current["buy"] - 1}:
            steps = 2
        else:
            return None
        for _ in range(steps):
            current = _step_buy(ticker, current)
            if current is None:
                return None
    desired = _desired(
        ticker, current["sell"], current["buy"], mult, current["double_used"]
    )
    if not desired:
        return None
    return current, desired


def _core_missing(anchors, orders):
    expected = [
        ("sell", anchors["sell"]),
        ("sell", anchors["sell"] + 1),
        ("buy", anchors["buy"]),
        ("buy", anchors["buy"] - 1),
    ]
    missing = []
    for side, index in expected:
        if not _has_level(orders, side, index):
            missing.append({"side": side, "idx": index})
    return missing


def _confirmed_fills(ticker, missing, fills, handled):
    found = []
    for item in missing:
        matches = [
            row
            for row in fills
            if row.get("ticker") == ticker
            and row.get("side") == item["side"]
            and row.get("idx") == item["idx"]
            and row.get("key") not in handled
            and row.get("filled_qty", 0) > 0
            and row.get("cancelled_qty", 0) <= 0
        ]
        if len(matches) != 1:
            return None
        found.append(matches[0])
    return found


def _remember(state, keys):
    handled = [key for key in state.get("handled", []) if isinstance(key, str)]
    for key in keys:
        if key not in handled:
            handled.append(key)
    state["handled"] = handled[-200:]


def _may_trade(event, mode, now_kst, now_et, report_only):
    """Live orders are schedule-only, inside the mode window, and past the KST guard."""
    return (
        _live_flag()
        and event == "schedule"
        and _window_open(mode, now_kst, now_et)
        and not report_only
        and not _orders_blocked(now_kst)
        and not _orders_blocked(_now_kst())
    )


def run(mode, event, now_kst, now_et, opens, fills, state, actions):
    """Pure orchestration. `actions` performs live calls or records dry intent."""
    mult = _mult()
    if mult is None or mode not in ("reset", "intraday"):
        print("ERROR config")
        return 1

    def _stop_for_guard():
        if _orders_blocked(now_kst) or _orders_blocked(_now_kst()):
            print("skip: time guard")
            return True
        return False

    if _stop_for_guard():
        return 0
    if event == "schedule" and not _window_open(mode, now_kst, now_et):
        print("OK skip")
        return 0
    report_only = state is None
    working = _fresh_state() if state is None else _clean_state(state)
    allow = _allow_set()
    live = _may_trade(event, mode, now_kst, now_et, report_only)
    slot = _reset_slot(now_kst) if mode == "reset" else None
    today = now_kst.strftime("%Y%m%d")
    if (
        event == "schedule"
        and _live_flag()
        and slot
        and not report_only
        and working.get("reset_done", {}).get(slot) == today
    ):
        print("OK skip")
        return 0
    if _exception_filled(fills, set(working["handled"])):
        working["exception_filled"] = True

    plans = []
    for ticker in ("NAIL", "KORU", "SOXL"):
        mine = [order for order in opens if order.get("ticker") == ticker]
        buys, sells, ignored = _split(ticker, [dict(order) for order in mine])
        review = False
        place = []
        cancel = []
        remember = []
        indexes = None
        stored = None if report_only else _stored_pair(working, ticker)
        if stored is None:
            review = True
        elif mode == "reset":
            place = _desired(
                ticker, stored["sell"], stored["buy"], mult, stored["double_used"]
            ) or []
            if not place:
                review = True
                place = []
            else:
                cancel = list(buys + sells)
                indexes = stored
        else:
            missing = _core_missing(stored, buys + sells)
            if missing:
                found = _confirmed_fills(
                    ticker, missing, fills, set(working["handled"])
                )
                if found is None:
                    review = True
                else:
                    sell_missing = [
                        item["idx"] for item in missing if item["side"] == "sell"
                    ]
                    buy_missing = [
                        item["idx"] for item in missing if item["side"] == "buy"
                    ]
                    stepped = _step_plan(
                        ticker, stored, sell_missing, buy_missing, mult
                    )
                    if stepped is None:
                        review = True
                    else:
                        new_pair, desired = stepped
                        open_levels = {(order["side"], order["idx"]) for order in buys + sells}
                        place = [
                            item
                            for item in desired
                            if (item["side"], item["idx"]) not in open_levels
                        ]
                        indexes = new_pair
                        remember = [item["key"] for item in found]
        if not review and mode == "reset" and indexes is not None:
            working["tickers"][ticker] = indexes
        plans.append(
            {
                "ticker": ticker,
                "buys": len(buys),
                "sells": len(sells),
                "ignored": ignored,
                "place": [] if review else place,
                "cancel": [] if review else cancel,
                "review": review,
                "indexes": None if review else indexes,
                "remember": [] if review else remember,
            }
        )

    planned = 0
    for plan in plans:
        if plan["ticker"] not in allow or plan["review"]:
            continue
        if mode == "reset":
            planned += len(plan["place"])
            continue
        existing = {
            (order.get("side"), order.get("idx"))
            for order in opens
            if order.get("ticker") == plan["ticker"]
        }
        for item in plan["place"]:
            if (item["side"], item["idx"]) not in existing:
                planned += 1
    want_exception = (
        live
        and "SOXL" in allow
        and not working["exception_filled"]
        and not _exception_open(opens)
    )
    if want_exception:
        planned += 1
    if live and planned > _MAX_NEW:
        for plan in plans:
            _report(
                plan["ticker"],
                plan["buys"],
                plan["sells"],
                plan["ignored"],
                len(plan["place"]),
                len(plan["cancel"]) if mode == "reset" else 0,
                plan["review"],
                0,
                0,
            )
        print("ERROR max_orders")
        return 1

    if live and _stop_for_guard():
        return 0
    if live and mode == "reset":
        actions["save_state"](_clean_state(working))

    counter = [0]
    sent_any = False
    for plan in plans:
        placed = 0
        cancelled = 0
        touch = live and plan["ticker"] in allow and not plan["review"]
        if touch and _stop_for_guard():
            return 0
        if touch and mode == "reset":
            for order in plan["cancel"]:
                if _stop_for_guard():
                    return 0
                result = actions["cancel"](plan["ticker"], order)
                if result is None:
                    print("ERROR unexpected")
                    return 1
                if result:
                    cancelled += 1
                    sent_any = True
            if plan["cancel"] and not actions["confirm_clear"](plan["ticker"]):
                print("ERROR unexpected")
                return 1
        if touch:
            for item in plan["place"]:
                if _stop_for_guard():
                    return 0
                exists = actions["exists"](plan["ticker"], item["side"], item["idx"])
                if exists is None:
                    print("ERROR unexpected")
                    return 1
                if exists:
                    continue
                result = actions["place"](plan["ticker"], item, counter)
                if result is None:
                    print("ERROR unexpected")
                    return 1
                if result:
                    placed += 1
                    sent_any = True
            if mode == "intraday" and plan["remember"]:
                _remember(working, plan["remember"])
                if plan["indexes"] is not None:
                    working["tickers"][plan["ticker"]] = plan["indexes"]
            if mode == "reset":
                _remember(
                    working,
                    [
                        row["key"]
                        for row in fills
                        if row.get("ticker") == plan["ticker"]
                        and isinstance(row.get("key"), str)
                    ],
                )
        _report(
            plan["ticker"],
            plan["buys"],
            plan["sells"],
            plan["ignored"],
            len(plan["place"]),
            len(plan["cancel"]) if mode == "reset" else 0,
            plan["review"],
            placed,
            cancelled,
        )

    if want_exception:
        if _stop_for_guard():
            return 0
        current = actions["exception_open"]()
        if current is None:
            print("ERROR unexpected")
            return 1
        if not current:
            if counter[0] >= _MAX_NEW:
                print("ERROR max_orders")
                return 1
            result = actions["place_exception"](counter)
            if result is None:
                print("ERROR unexpected")
                return 1
            if result:
                sent_any = True

    allowed = [plan for plan in plans if plan["ticker"] in allow]
    if (
        live
        and mode == "reset"
        and slot
        and allowed
        and all(not plan["review"] for plan in allowed)
    ):
        working["reset_done"][slot] = today
    if report_only:
        print("OK report_only")
    else:
        if live:
            actions["save_state"](_clean_state(working))
        if sent_any:
            print("OK live")
        else:
            print("OK dry")
    return 0


def _die_http():
    print("ERROR http")
    return 1


def main():
    mode = os.environ.get("LADDER_MODE", "")
    event = os.environ.get("GITHUB_EVENT_NAME", "push")
    now_kst = _now_kst()
    now_et = datetime.now(ET)
    if mode not in ("reset", "intraday"):
        print("ERROR config")
        return 1
    if _orders_blocked(now_kst):
        print("skip: time guard")
        return 0
    if event == "schedule" and mode == "reset":
        slot = _reset_slot(now_kst)
        if slot is None:
            print("OK skip")
            return 0
        loaded = _load_state()
        done = (loaded or {}).get("reset_done") or {}
        if _live_flag() and done.get(slot) == now_kst.strftime("%Y%m%d"):
            print("OK skip")
            return 0
    elif event == "schedule" and not _window_open(mode, now_kst, now_et):
        print("OK skip")
        return 0
    key = os.environ.get("NHPLUG_APP_KEY", "")
    secret = os.environ.get("NHPLUG_APP_SECRET", "")
    if not key.strip() or not secret.strip() or _mult() is None:
        print("ERROR config")
        return 1
    gb._key = key
    gb._secret = secret
    gb._secrets.extend((key, secret))
    gb._issue_token = _quiet_issue
    gb._emit = lambda name, status, payload: gb._rsp(payload)
    token = _load_token() or _quiet_issue(key, secret)
    if not token:
        return _die_http()
    gb._token = token
    gb._secrets.append(token)

    status, _headers, payload = gb._post_bearer(gb.ACCTINFO_URL, gb._json_body({}), {})
    if status != 200:
        return _die_http()
    account = gb._select_account(payload)
    if not account:
        print("ERROR unexpected")
        return 1

    opens = _inquiry(account, now_kst, "open")
    fills = _inquiry(account, now_kst, "fill")
    if opens is None or fills is None:
        return _die_http()

    state = _load_state()
    live = _may_trade(event, mode, now_kst, now_et, state is None)

    def place(ticker, item, count):
        return _place(
            account, ticker, item["side"], item["price"], item["qty"], live, count
        )

    def cancel(ticker, order):
        return _cancel(account, ticker, order, live)

    def exists(ticker, side, index):
        fresh = _inquiry(account, now_kst, "open")
        if fresh is None:
            return None
        for order in fresh:
            if (
                order.get("ticker") == ticker
                and order.get("side") == side
                and order.get("idx") == index
            ):
                return True
        return False

    def confirm_clear(ticker):
        fresh = _inquiry(account, now_kst, "open")
        if fresh is None:
            return False
        for order in fresh:
            if order.get("ticker") == ticker and order.get("idx") is not None:
                return False
        return True

    def exception_open():
        fresh = _inquiry(account, now_kst, "open")
        if fresh is None:
            return None
        return _exception_open(fresh)

    def place_exception(count):
        if not live:
            return False
        return _place_exception(account, True, count)

    def save_state(data):
        if state is not None and not live:
            return
        _save_json(_STATE_PATH, _clean_state(data))

    actions = {
        "place": place,
        "cancel": cancel,
        "exists": exists,
        "confirm_clear": confirm_clear,
        "exception_open": exception_open,
        "place_exception": place_exception,
        "save_state": save_state,
    }
    return run(mode, event, now_kst, now_et, opens, fills, state, actions)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("ERROR unexpected")
        sys.exit(1)
