"""Standing TQQQ buys for the second NH account (201-12) only.

Twenty limit buys, 10 shares each, re-placed at each reset until that
level has filled 10 shares. Partial fills reduce the next order to the
shares still left. A level that has filled 10 is never sent again.

This does not cancel, modify, or replace any other order. It does not
read or write ladder state. Live orders require TQQQ_LIVE=on and a
clocked run (schedule or workflow_dispatch). Otherwise the run prints
the planned prices and quantities and places nothing.

NHPLUG_ACCOUNT_SUFFIX must be 201-12 (digits 20112). Any other value,
or an acctinfo list that does not contain that account, prints
"ERROR account not found" and places nothing. The ladder's own
selector is unchanged when that variable is unset.
"""

import os
import time
from decimal import Decimal

import gb_readonly_check as gb
import ladder_bot as bot

# Nearest to the market first. Each level is 10 shares (200 in total).
_PRICES = (
    "43.00",
    "41.54",
    "40.12",
    "38.75",
    "37.43",
    "36.16",
    "34.93",
    "33.74",
    "32.59",
    "31.48",
    "30.41",
    "29.37",
    "28.37",
    "27.40",
    "26.47",
    "25.57",
    "24.70",
    "23.86",
    "23.04",
    "22.26",
)
_QTY = 10
_TICKER = "TQQQ"
_ISIN = "US74347X8314"
_SUFFIX = "20112"
_PROGRAM = "tqqq-buy"
_MAX_NEW = 6
_PRICE_GAP = 0.02


def _state_path():
    return os.environ.get("TQQQ_STATE_PATH", ".tqqq-state/state.json")


def _token_path():
    return os.environ.get("TQQQ_TOKEN_PATH", ".tqqq-token/token.json")


def _ladder_path(path):
    text = path.replace("\\", "/")
    return "ladder-state" in text or text.rstrip("/").endswith(".ladder-state/state.json")


def _suffix_is_target():
    return gb._account_suffix() == _SUFFIX


def _level_for(price):
    """One of the 20 prices, or None. A gap smaller than the book stays unique."""
    if isinstance(price, bool) or price is None:
        return None
    if isinstance(price, str):
        try:
            number = float(Decimal(price.strip()))
        except Exception:
            number = None
    elif isinstance(price, (int, float)):
        number = float(price)
    else:
        number = None
    if number is None or not number > 0:
        return None
    found = None
    for level in _PRICES:
        if abs(number - float(level)) < _PRICE_GAP:
            if found is not None:
                return None
            found = level
    return found


def _fresh():
    levels = {}
    for price in _PRICES:
        levels[price] = {
            "filled_qty": 0,
            "parts": {},
            "filled": False,
            "resting": {},
        }
    return {"version": 1, "program": _PROGRAM, "levels": levels, "seq": 0, "saved_at": 0}


def _clean_resting(raw):
    if not isinstance(raw, dict):
        return {}
    day = raw.get("day")
    slot = raw.get("slot")
    if not isinstance(day, str) or len(day) != 8 or not day.isdigit():
        return {}
    if slot not in ("09", "17"):
        return {}
    cleaned = {"day": day, "slot": slot}
    qty = bot._share_count(raw.get("qty", 0))
    if qty:
        cleaned["qty"] = qty
    return cleaned


def _clean(state):
    raw_levels = state.get("levels") if isinstance(state, dict) else None
    if not isinstance(raw_levels, dict):
        raw_levels = {}
    levels = {}
    for price in _PRICES:
        item = raw_levels.get(price)
        if not isinstance(item, dict):
            item = {}
        parts = bot._clean_parts_map(item.get("parts"))
        qty = sum(parts.values()) if parts else bot._share_count(item.get("filled_qty", 0))
        if qty > _QTY:
            qty = _QTY
        done = qty >= _QTY
        resting = {} if done else _clean_resting(item.get("resting"))
        levels[price] = {
            "filled_qty": _QTY if done else qty,
            "parts": parts if qty else {},
            "filled": done,
            "resting": resting,
        }
    seq = state.get("seq") if isinstance(state, dict) else 0
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        seq = 0
    saved = state.get("saved_at") if isinstance(state, dict) else 0
    if isinstance(saved, bool) or not isinstance(saved, int) or saved < 0:
        saved = 0
    return {
        "version": 1,
        "program": _PROGRAM,
        "levels": levels,
        "seq": seq,
        "saved_at": saved,
    }


def _load_state():
    if _ladder_path(_state_path()):
        return _fresh()
    data = bot._load_json(_state_path())
    if not isinstance(data, dict) or data.get("program") != _PROGRAM:
        return _fresh()
    return _clean(data)


def _save_state(state):
    path = _state_path()
    if _ladder_path(path):
        print("ERROR unexpected")
        return False
    stamped = _clean(state)
    stamped["saved_at"] = int(time.time())
    stamped["seq"] = stamped["seq"] + 1
    bot._save_json(path, stamped)
    state.clear()
    state.update(stamped)
    return True


def _note_fills(state, rows):
    """Count TQQQ buy fills at a level. The same fill key is not added twice."""
    for order in rows or []:
        if not isinstance(order, dict):
            continue
        if order.get("ticker") != _TICKER or order.get("side") != "buy":
            continue
        price = _level_for(order.get("price"))
        if price is None:
            continue
        cancelled = order.get("cancelled_qty", 0)
        if isinstance(cancelled, bool) or not isinstance(cancelled, (int, float)):
            cancelled = 0
        if cancelled > 0:
            continue
        qty = bot._share_count(order.get("filled_qty", 0))
        if qty <= 0:
            continue
        key = order.get("key")
        if not bot._fill_key_ok(key):
            continue
        bucket = state["levels"][price]
        if bucket["filled"]:
            continue
        parts = bucket["parts"]
        prev = parts.get(key, 0)
        if qty <= prev:
            continue
        parts[key] = qty
        total = sum(parts.values())
        if total > _QTY:
            total = _QTY
        bucket["filled_qty"] = total
        bucket["parts"] = parts
        bucket["filled"] = total >= _QTY
        if bucket["filled"]:
            bucket["resting"] = {}


def _remainder(bucket):
    if bucket.get("filled") or bucket.get("filled_qty", 0) >= _QTY:
        return 0
    left = _QTY - int(bucket.get("filled_qty", 0))
    if left < 1:
        return 0
    if left > _QTY:
        return _QTY
    return left


def _open_orders_at(opens, price):
    found = []
    for order in opens or []:
        if not isinstance(order, dict):
            continue
        if order.get("ticker") != _TICKER or order.get("side") != "buy":
            continue
        if _level_for(order.get("price")) != price:
            continue
        qty = bot._share_count(order.get("qty", 0))
        if qty < 1:
            continue
        found.append(qty)
    return found


def _classify(state, opens, slot, today, price):
    """filled, open, resting, or place. Open orders are never cancelled."""
    bucket = state["levels"][price]
    left = _remainder(bucket)
    if left < 1:
        return "filled", 0
    opened = _open_orders_at(opens, price)
    if opened:
        # Same price and side is already working. Leave it alone, including
        # when that open quantity is the unfilled remainder.
        return "open", opened[0]
    resting = bucket.get("resting") or {}
    if resting.get("day") == today and resting.get("slot") == slot:
        return "resting", left
    return "place", left


def resolve_account(payload):
    """The 201-12 production account, or "" after ERROR account not found.

    Does not place an order. A missing or ambiguous match fails closed.
    """
    if not _suffix_is_target():
        print("ERROR account not found")
        return ""
    account = gb._select_account(payload)
    if not account or _SUFFIX not in gb._digits(account):
        print("ERROR account not found")
        return ""
    return account


def _comment_block():
    raw = os.environ.get("TQQQ_COMMENT", "")
    if not isinstance(raw, str):
        return "tqqq skip parse"
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    if text.startswith("tqqq-reset"):
        return ""
    return "tqqq skip parse"


def _live_now(event, now):
    return (
        os.environ.get("TQQQ_LIVE") == "on"
        and event in ("schedule", "workflow_dispatch")
        and bot._window_open("reset", now, now.astimezone(bot.ET))
        and not bot._orders_blocked(now)
        and not bot._orders_blocked(bot._now_kst())
    )


def _due(state, opens, slot, today):
    """Levels that still need an order, highest price first."""
    due = []
    for price in _PRICES:
        kind, qty = _classify(state, opens, slot, today, price)
        if kind == "filled":
            print("TQQQ filled %s" % price)
            continue
        if kind == "open":
            print("TQQQ skip open %s qty %s" % (price, qty))
            continue
        if kind == "resting":
            print("TQQQ resting %s qty %s" % (price, qty))
            continue
        due.append((price, qty))
    return due


def _trade(now, opens, fills, state, place, live, slot):
    today = now.strftime("%Y%m%d")
    _note_fills(state, list(fills or []) + list(opens or []))
    due = _due(state, opens, slot, today)
    if not live:
        for price, qty in due:
            print("TQQQ plan buy %s qty %s" % (price, qty))
        print("OK dry")
        return 0, False, state
    sending = due[:_MAX_NEW]
    deferred = due[_MAX_NEW:]
    for price, qty in sending:
        print("TQQQ plan buy %s qty %s" % (price, qty))
    for price, qty in deferred:
        print("TQQQ defer buy %s qty %s" % (price, qty))
    for price, qty in sending:
        if bot._orders_blocked(now) or bot._orders_blocked(bot._now_kst()):
            print("skip: time guard")
            return 0, False, state
        result = place(price, qty)
        if result == "max_orders":
            print("ERROR max_orders")
            print("FOLLOWUP reset")
            return 1, True, state
        if result is not True:
            if not bot._log_reject():
                print("ERROR reject")
            else:
                print("ERROR reject")
            return 1, False, state
        state["levels"][price]["resting"] = {
            "slot": slot,
            "day": today,
            "qty": qty,
        }
        print("TQQQ placed buy %s qty %s" % (price, qty))
    if deferred:
        print("DEFERRED TQQQ %s" % (len(deferred),))
        print("FOLLOWUP reset")
        print("OK live")
        return 0, True, state
    if sending:
        print("OK live")
    else:
        print("OK dry")
    return 0, False, state


def _loop(once, sleep, now_fn, gap):
    code, follow = once()
    extra = 0
    while follow:
        if extra >= bot._MAX_FOLLOWUPS or not bot._followup_allowed(now_fn(), gap):
            if extra >= bot._MAX_FOLLOWUPS:
                print("follow-up limit reached")
            else:
                print("follow-up stopped time guard")
            break
        extra += 1
        print("follow-up reset %s" % (extra,))
        sleep(gap)
        if not bot._reset_runnable(now_fn()):
            print("follow-up stopped time guard")
            break
        code, follow = once()
    return code


def execute(now_fn, acctinfo, inquiry, place_for, sleep=None, gap=bot._FOLLOWUP_GAP):
    """Reset passes. `place_for(account)` returns place(price, qty).

    place returns True, "max_orders", or anything else for a reject.
    Nothing is sent before the account check succeeds.
    """
    if sleep is None:
        sleep = time.sleep

    def once():
        now = now_fn()
        event = os.environ.get("GITHUB_EVENT_NAME", "push")
        blocked = _comment_block()
        if blocked:
            print(blocked)
            return 0, False
        if bot._orders_blocked(now) or bot._orders_blocked(bot._now_kst()):
            print("skip: time guard")
            return 0, False
        if not _suffix_is_target():
            print("ERROR account not found")
            return 1, False
        slot = bot._reset_slot(now)
        if slot is None:
            print("OK skip")
            return 0, False
        now_et = now.astimezone(bot.ET)
        kind, reason = bot._session(now, now_et, "reset")
        wall = bot._now_kst()
        wall_kind, wall_reason = bot._session(wall, wall.astimezone(bot.ET), "reset")
        if reason == "ERROR config" or wall_reason == "ERROR config":
            print("ERROR config")
            return 1, False
        if not kind or kind != wall_kind:
            print(reason or wall_reason or "skip: session closed")
            return 0, False
        print("order type %s" % (kind,))
        fetched = acctinfo()
        if not isinstance(fetched, tuple) or len(fetched) != 2:
            print("ERROR http")
            return 1, False
        status, payload = fetched
        if status != 200 or not isinstance(payload, dict):
            print("ERROR http")
            return 1, False
        account = resolve_account(payload)
        if not account:
            return 1, False
        opens = inquiry(account, "open")
        fills = inquiry(account, "fill")
        if opens is None or fills is None:
            print("ERROR http")
            return 1, False
        state = _load_state()
        live = _live_now(event, now)
        code, follow, state = _trade(
            now, opens, fills, state, place_for(account), live, slot
        )
        if live:
            _save_state(state)
        return code, follow

    return _loop(once, sleep, now_fn, gap)


def _is_max_orders():
    info = bot._last_reject or {}
    text = "%s %s" % (info.get("rsp_cd", ""), info.get("rsp_msg", ""))
    lowered = text.lower()
    markers = (
        "max_orders",
        "max order",
        "order limit",
        "order count",
        "too many order",
        "주문가능건수",
        "주문건수",
        "건수초과",
        "주문한도",
        "최대주문",
    )
    return any(marker in lowered or marker in text for marker in markers)


def _broker_place(account, price, qty):
    """One TQQQ limit buy. None on reject, "max_orders" on a count limit."""
    send = bot._share_count(qty)
    if send < 1 or send > _QTY:
        return None
    if _level_for(price) != price:
        return None
    kind = bot._order_type(bot._now_kst(), mode="reset")
    if not kind:
        return None
    body = {
        "act_no": account,
        "fc_sec_trd_nat_cd": "200",
        "iem_cd": _TICKER,
        "orr_qty": send,
        "fc_orr_uit_pr": round(float(Decimal(price)), 2),
        "ahi_nmn_pr_tp_cd": kind,
        "wtm_cur_knd_cd": "1",
    }
    result = bot._post_order(bot.BUY_URL, body)
    if result is True:
        return True
    if _is_max_orders():
        return "max_orders"
    return None


def _load_token_file(path):
    data = bot._load_json(path)
    if not isinstance(data, dict):
        return ""
    token = data.get("access_token")
    expires = data.get("expires_at")
    if not isinstance(token, str) or not token.strip():
        return ""
    if not isinstance(expires, (int, float)) or expires <= time.time() + 60:
        return ""
    return token


def _load_token():
    """Reuse a valid token. Do not write the ladder token file."""
    ladder = _load_token_file(bot._TOKEN_PATH)
    if ladder:
        return ladder
    return _load_token_file(_token_path())


def _issue_token(key, secret):
    saved = bot._TOKEN_PATH
    bot._TOKEN_PATH = _token_path()
    try:
        return bot._quiet_issue(key, secret)
    finally:
        bot._TOKEN_PATH = saved


def _acctinfo():
    status, _headers, payload = gb._post_bearer(gb.ACCTINFO_URL, gb._json_body({}), {})
    return status, payload


def _inquiry(account, kind):
    return bot._inquiry(account, bot._now_kst(), kind)


def _clock_block(now):
    """A skip line before any broker call, or "" when this reset may continue."""
    if bot._orders_blocked(now):
        return "skip: time guard"
    if bot._reset_slot(now) is None:
        return "OK skip"
    now_et = now.astimezone(bot.ET)
    kind, reason = bot._session(now, now_et, "reset")
    if reason == "ERROR config":
        return "ERROR config"
    if not kind:
        return reason or "skip: session closed"
    return ""


def main():
    if _comment_block():
        print(_comment_block())
        return 0
    if not _suffix_is_target():
        print("ERROR account not found")
        return 1
    early = _clock_block(bot._now_kst())
    if early:
        print(early)
        return 1 if early == "ERROR config" else 0
    key = os.environ.get("NHPLUG_APP_KEY", "")
    secret = os.environ.get("NHPLUG_APP_SECRET", "")
    if not isinstance(key, str) or not isinstance(secret, str):
        print("ERROR config")
        return 1
    if not key.strip() or not secret.strip():
        print("ERROR config")
        return 1
    gb._key = key
    gb._secret = secret
    gb._secrets.extend((key, secret))
    gb._issue_token = _issue_token
    gb._emit = lambda name, status, payload: gb._rsp(payload)
    token = _load_token() or _issue_token(key, secret)
    if not token:
        print(bot._last_http or "ERROR http")
        return 1
    gb._token = token
    gb._secrets.append(token)

    def place_for(account):
        def place(price, qty):
            return _broker_place(account, price, qty)

        return place

    return execute(bot._now_kst, _acctinfo, _inquiry, place_for)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(bot._unexpected_type(exc))
        raise SystemExit(1)
