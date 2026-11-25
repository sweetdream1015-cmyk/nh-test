"""Order-capable ladder bot. Live trading is off unless LIVE_TRADING is exactly on.

No new order is sent in the pre-reset guards. Each one is an
America/New_York window: 60 minutes before an anchor through 10
minutes after it. The anchors are 19:00 ET (one hour before the
20:00 after-hours close) and 03:00 ET (one hour before the 04:00
premarket open), so the windows are 18:00-19:10 ET and 02:00-03:10 ET.
US standard time is 08:00-09:10 and 16:00-17:10 KST. US daylight time
is 07:00-08:10 and 15:00-16:10 KST. Saturday KST stays open only
through the end of Friday's US session (20:00 ET). Sunday, and Monday
before 09:10 KST, are closed. Those runs log "skip: time guard" and exit.
Intraday follow-up runs in the US session and in the KST day session.
A live reset places whole tickers only, at most 6 new orders. A ticker
that does not fit is left uncancelled and logged DEFERRED for a later run.
Each standing exception uses a free slot in that same run when one remains,
even if that ticker's ladder is deferred. A successful place is remembered
for that reset slot, so a later pass does not send it again while the broker
inquiry still omits it. Open-order and already-filled checks still apply.
A ladder order this bot just placed is stored on its state for 3 hours:
ticker, side, price, quantity, time, and the order number when the broker
returns one. Intraday, adjust, and reset treat that order as still open
until the inquiry shows it, the 3 hours pass, or the next reset slot
starts. A late inquiry cannot send a second copy. The retired SOXL sell
of 150 at 75 is not replaced. Saved double_used is not cleared.
An owner comment on issue #18 whose body is
`cancel-dup NAIL BUY 28 10` cancels one matching open order, and only
when the inquiry shows two or more identical orders. It uses the same
time guards and the same full-cancel call as adjust.
An owner comment on that same issue whose body is `reset`, `reset day`,
or `reset premarket` runs this reset for the slot the clock is in.
`day` is the 09:10 slot and `premarket` is the 17:10 slot. A bare
`reset` uses whichever of those is open. The slot's reset_done mark
makes a second run the same day log OK skip and send nothing. The
comment run saves state in the ladder-state artifact, because an
issue comment cannot write the Actions cache. Each save stores saved_at
and seq. The next run keeps the copy with the greater stamp, so that
artifact wins when the cache was not updated.
A scheduled intraday run whose slot is already open, outside the time
guards, and not marked done places that reset itself when the ladder
book is empty or an earlier pass of the slot already finished a ticker.
That covers a cron GitHub never started. It does not cancel a book
that already has ladder orders; those stay on the intraday path.
Deferred work is retried in this process about 2 minutes later, at most
3 extra passes, and not into a blocked window or out of the slot.
Reset keeps the standing orders until each is fully filled, and places
them again after each broker cancel-all. A partial fill reduces the next
order to the shares still left. The SOXL sell of 150 at 75 is retired.
Reset, intraday, and adjust do not place it. soxl-rebuild cancels an
open one and does not replace it. The SOXL buy is 400 at 18.7
(soxl-buy-400-18-7-v2). Reset places it again after each cancel-all
until 400 shares have filled. An open match is kept and is not sent
again. The previous id soxl-buy-400-18-7 does not block this order.
Intraday, adjust, and cancel-dup leave it alone. soxl-rebuild does not
cancel it and does not place it. The KORU buy is 100 at 4.2, sent to
the buy endpoint, and it is not the ladder's own 2-share buy at that
rung. The retired buys stay retired: 100 at 75 on 2026-10-31, 200 at
38, and 200 at 37.5 filled 2026-11-21. exception_filled still names
soxl-sell-150-75 when that old sell was marked, and that mark does not
place an order. The KORU buy and the 400 buy keep their own counts
under exception_orders. A broker reject of one standing order is logged
and does not stop the other tickers.
SOXL ladder buys and sells are 20. There is no doubled first buy.
KORU buys and sells 1. A first KORU buy at 6 or below is 2, then 1.
NAIL is unchanged. Saved double_used is kept as written.
A manual adjust sets one ticker's sell and buy anchors, or steps that
ticker once, then cancels only that ticker's ladder orders that do not
match and places the missing ones. Preview prints those prices and
quantities and does not save state or send orders. Adjust does not
place the retired SOXL sell at 75. An open buy of 400 at 18.7 is left
alone. The KORU buy at 4.2 and the SOXL buy of 400 at 18.7 are placed
only by reset. Intraday, adjust, and cancel-dup leave the standing
orders alone and do not treat them as a ladder rung or a duplicate.
Other tickers' ladders are left alone.
Adjust may send orders in the same US and KST sessions as intraday.
Reset applies an unhandled confirmed fill at the current anchor before
it restores the book, so a fill during a pre-reset guard is not quoted
again after the wipe.
Inquiry plus, when live and allowed, limit buy, limit sell, and full cancel.
The modify endpoint is never called. Prints counts, OK/ERROR codes, and
on a broker reject the ticker, side, price, and redacted rsp_cd/rsp_msg.
A reject stops the rest of that ticker only. Other tickers still run.
A rejected standing order is logged and does not stop those tickers
or the rest of the reset. The process logs ERROR reject and exits
non-zero when a ladder order was rejected.
A stop that is not a broker reject logs ERROR unexpected, a fixed reason
(remain, inquiry, cancel, place, exists, or select), and the ticker when that
stop is inside one ticker. An uncaught exception logs only its type
name. Those lines omit order ids, prices, quantities, accounts, and
exception text.

Spec fields used:
- POST /oauth2/token
- POST /n2/acctinfo Output_0 acct_type 01
  NHPLUG_ACCOUNT_SUFFIX, when set, selects the type-01 row whose account
  digits contain that suffix (201-12 and 20112 are the same). Unset keeps
  the first type-01 row. No match places nothing.
- POST /gbstock/inquiry/v1/unexecuted
  open rows: ost_cns_dit 2; executions: ost_cns_dit 1
  oss_sby_dit_cd 1 sell / 2 buy, fc_orr_uit_pr, orr_qty, ny_cns_orr_qty,
  cns_qty, orr_no, orr_dt, fc_sec_trd_nat_cd
  Open orders are read for KST today, the US Eastern date, and the
  previous four weekdays. A blank remaining quantity is not zero.
- POST /gbstock/order/v1/buy and /sell
  fc_sec_trd_nat_cd 200, iem_cd ticker, orr_qty, fc_orr_uit_pr,
  ahi_nmn_pr_tp_cd from the US Eastern clock: premarket 61, regular
  hours 00 (지정가), after-hours 62. The KST day session is 63.
  ORDER_TYPE overrides that when it is 00, 61, 62, or 63.
  Spec requires fc_orr_uit_pr for 00, 61, 62, and 63. There is no
  separate price field for 00. Buy also sends wtm_cur_knd_cd 1.
  The standing KORU buy and the SOXL buy of 400 at 18.7 use the buy
  body and do send it. The retired SOXL sell at 75 is not sent.
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
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import gb_readonly_check as gb
import ladder_dryrun as ladders

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
# Pre-reset quiet windows on the America/New_York clock. Each window is
# 60 minutes before the anchor through 10 minutes after it (end exclusive).
# 19:00 ET is one hour before the 20:00 after-hours close, the broker's
# daily cancel-all. 03:00 ET is one hour before the 04:00 premarket open.
# Standard time, KST = ET+14h: 08:00-09:10 and 16:00-17:10.
# Daylight time, KST = ET+13h: 07:00-08:10 and 15:00-16:10.
_GUARD_BEFORE = 60
_GUARD_AFTER = 10
_GUARD_ANCHORS_ET = ((19, 0), (3, 0))
BUY_URL = gb.LIVE + "/gbstock/order/v1/buy"
SELL_URL = gb.LIVE + "/gbstock/order/v1/sell"
CANCEL_URL = gb.LIVE + "/gbstock/order/v1/cancel"
_ORDER_URLS = {BUY_URL, SELL_URL, CANCEL_URL}
# Ladder orders only. Standing exceptions do not use this cap.
_HARD_MAX = {"NAIL": 40, "KORU": 4, "SOXL": 20}
_MAX_NEW = 6
# Extra reset passes after a deferral. Three tickers need only a couple;
# the bound stops a stuck deferral from chaining.
_MAX_FOLLOWUPS = 3
_FOLLOWUP_GAP = 120
# Broker open-order inquiry can omit a new order for more than an hour.
# Remember our own places for this long, then trust the inquiry again.
_RECENT_GRACE = 3 * 60 * 60
_RECENT_MAX = 40
_wants_reset_followup = False
# Standing orders, replaced after every broker cancel-all until each
# target is filled. The SOXL sell of 150 at 75 is retired: it is not
# in _EXCEPTIONS, nothing places it, and soxl-rebuild cancels an open
# one. Retired buys that must not be reseeded as a new order: buy 100
# at 75 on 2026-10-31, buy 200 at 38, buy 200 at 37.5 filled 2026-11-21
# (soxl-buy-200-37-5). The old SOXL buy id soxl-buy-400-18-7 is not
# reused, so a filled or resting record under that id does not block
# the new order. The KORU buy is not the ladder's 2-share rung.
_RETIRED_SOXL_SELL = {
    "id": "soxl-sell-150-75",
    "ticker": "SOXL",
    "side": "sell",
    "price": 75,
    "qty": 150,
}
# Fresh id. soxl-buy-400-18-7 was retired after rsp_cd 16156 and must
# not be used again. Reset places this buy until 400 shares fill.
_SOXL_BUY_400_ID = "soxl-buy-400-18-7-v2"
_EXCEPTIONS = (
    {
        "id": "koru-buy-100-4.2",
        "ticker": "KORU",
        "side": "buy",
        "price": 4.2,
        "qty": 100,
    },
    {
        "id": _SOXL_BUY_400_ID,
        "ticker": "SOXL",
        "side": "buy",
        "price": 18.7,
        "qty": 400,
    },
)
_EXCEPTION_BY_ID = {item["id"]: item for item in _EXCEPTIONS}
_EXCEPTION_PRICE = 75
_EXCEPTION_QTY = 150
_EXCEPTION_ID = "soxl-sell-150-75"
_EXCEPTION_TOL = 0.051
# State written by this ladder. Older files omit it and store KORU on the
# pre-shared map (7.2 at index 0, generated rungs below that).
_LADDER_ID = "shared"
_KORU_LEGACY = (
    7.2, 7.9, 8.6, 9.4, 10.3, 11.3, 12.5, 13.5, 15, 16.5, 18, 20, 22, 24, 26,
    28, 30, 33, 36, 39, 42, 46, 50, 55, 60, 66, 72,
)
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


_ORDER_TYPES = {"00", "61", "62", "63"}


def _order_type_override():
    """Return the ORDER_TYPE variable, '' when unset, or None when invalid."""
    raw = os.environ.get("ORDER_TYPE", "").strip()
    if not raw:
        return ""
    if raw in _ORDER_TYPES:
        return raw
    return None


def _as_kst(now):
    if now.tzinfo is None:
        return now.replace(tzinfo=KST)
    return now.astimezone(KST)


def _as_et(now):
    if now.tzinfo is None:
        return now.replace(tzinfo=ET)
    return now.astimezone(ET)


def _minutes(now):
    return now.hour * 60 + now.minute


def _us_bucket(now_et):
    """US session from America/New_York. None outside extended hours.

    Premarket is 04:00 inclusive to 09:30 exclusive. Regular hours are
    09:30 inclusive to 16:00 exclusive and use 지정가 00. After-hours
    is 16:00 inclusive to 20:00 exclusive, matching the intraday window
    end.
    """
    if now_et.weekday() >= 5:
        return None
    minutes = _minutes(now_et)
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "pre"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "regular"
    if 16 * 60 <= minutes < 20 * 60:
        return "post"
    return None


def _kst_day(now_kst):
    """Korean daytime session, 09:10 inclusive through 16:59 inclusive."""
    if now_kst.weekday() >= 5:
        return False
    minutes = _minutes(now_kst)
    return 9 * 60 + 10 <= minutes <= 16 * 60 + 59


def _session(now_kst, now_et=None, mode=None):
    """Return (order type or None, skip line or "").

    Codes from the gbstock spec (order_type_check): 00 is 지정가, a
    limit with no session name, and is the regular-session order.
    61 is premarket, 62 is after-hours, 63 is the KST day session.
    The broker rejects 61 outside premarket. fc_orr_uit_pr is the
    price on all four codes. Reset and intraday share this map.
    """
    override = _order_type_override()
    if override is None:
        return None, "ERROR config"
    if override:
        return override, ""
    now_kst = _as_kst(now_kst)
    now_et = _as_et(now_et) if now_et is not None else now_kst.astimezone(ET)
    bucket = _us_bucket(now_et)
    day = _kst_day(now_kst)
    # The 09:10 reset wants day-session orders (63). While US after-hours
    # is still open those would be the wrong book, so the reset waits.
    if mode == "reset" and day and bucket == "post":
        return None, "skip: day session not open"
    if bucket == "post":
        return "62", ""
    if bucket == "pre":
        return "61", ""
    if bucket == "regular":
        return "00", ""
    if day:
        return "63", ""
    if mode == "reset" and _reset_slot(now_kst) == "17":
        return None, "skip: premarket not open"
    return None, "skip: session closed"


def _order_type(now, now_et=None, mode=None):
    """Session limit code. Regular hours are 지정가 00."""
    return _session(now, now_et, mode)[0]


def _session_skip(now_kst, now_et, mode):
    code, reason = _session(now_kst, now_et, mode)
    if code:
        return ""
    return reason or "skip: session closed"


def _off_session_day(now_kst):
    """Closed calendar days that are not a live US or KST session.

    Saturday KST is still Friday in New York until 20:00 ET, so that
    morning follows the same extended-hours clock as the order type.
    The rest of Saturday, all of Sunday, and Monday before the 09:10
    KST day session are closed. That Monday open is the Korean day
    session, so it stays on the KST clock through US daylight time.
    """
    weekday = now_kst.weekday()
    if weekday == 5:
        now_et = now_kst.astimezone(ET)
        return not (now_et.weekday() == 4 and _in_us_session(now_et))
    if weekday == 6:
        return True
    if weekday == 0 and _minutes(now_kst) < 9 * 60 + 10:
        return True
    return False


def _in_order_guard(now):
    """True inside a pre-reset quiet window on the US Eastern clock.

    Local ET minutes, so the same window is an hour earlier in KST
    while the US is on daylight time. The end minute is exclusive.
    A spring-forward gap simply has no 02:00-02:59 ET; the 03:00-03:09
    portion still matches. The fold hour on a fall-back day is 01:00 ET
    and is outside this window.
    """
    minutes = _minutes(_as_et(now))
    for hour, minute in _GUARD_ANCHORS_ET:
        anchor = hour * 60 + minute
        if anchor - _GUARD_BEFORE <= minutes < anchor + _GUARD_AFTER:
            return True
    return False


def _orders_blocked(now):
    """True when a new order must not be sent.

    Quiet windows are America/New_York local time, 18:00 inclusive to
    19:10 exclusive and 02:00 inclusive to 03:10 exclusive. That is 60
    minutes before 19:00 ET through 10 minutes after it, and the same
    offsets around 03:00 ET. 19:00 is one hour before the 20:00
    after-hours close (the broker cancel-all). 03:00 is one hour before
    the 04:00 premarket open. Intraday follow-ups in those spans are
    left for the next reset. The end minute itself is allowed.

    US standard time is 08:00-09:10 and 16:00-17:10 KST. US daylight
    time is 07:00-08:10 and 15:00-16:10 KST. Saturday morning is
    included while Friday's US session is still open, because that
    morning is still Friday evening in New York.

    After Friday's US session ends at 20:00 ET, the rest of Saturday
    KST, all of Sunday, and Monday before 09:10 KST are blocked even
    outside those windows.
    """
    now_kst = _as_kst(now)
    if _off_session_day(now_kst):
        return True
    return _in_order_guard(now_kst)


def _reset_slot(now):
    """KST reset slot. None means outside the window.

    09:10 inclusive through 16:00 exclusive, then 17:10 through midnight.
    The bounds stay on the KST clock. The reset crons are fixed KST
    times, and the saved slot names are "09" and "17". The pre-reset
    order guard is separate and follows US Eastern time.
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
    """True when this mode may look at the book.

    Reset stays on the post-wipe KST slots. Intraday, adjust, cancel-dup,
    and soxl-rebuild follow the US extended session and the KST day
    session (09:10-16:59). Order type is 63 there, or 62 while US
    after-hours is still open. The US Eastern pre-reset guard is
    separate and still wins. That guard is 08:00-09:10 and 16:00-17:10
    KST in standard time, the quiet hour before the 10:00 KST broker
    cancel and the matching morning window.
    """
    if mode == "reset":
        return _in_reset_window(now_kst)
    if mode in ("intraday", "adjust", "cancel-dup", "soxl-rebuild"):
        return _in_us_session(now_et) or _kst_day(now_kst)
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
    return ladders._level_of(ticker, float(price))


def _split(ticker, orders, state=None):
    ignored = 0
    buys = []
    sells = []
    for order in orders:
        # 75 is not a rung. 4.2 is, and the 100-share buy there is still
        # not a ladder order. The ladder's own 1- or 2-share buy stays.
        if _counts_as_exception(ticker, order, state):
            ignored += 1
            continue
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
    if not _valid_pair(ticker, sell_idx, buy_idx):
        return None
    return {
        "buys": buys,
        "sells": sells,
        "ignored": ignored,
        "buy": buy_idx,
        "sell": sell_idx,
    }


def _in_double_zone(ticker, price):
    """True where the first buy is above base, so double_used can revert it.

    NAIL is 22 and at or below 20. KORU is at or below 6. SOXL has no
    doubled size, so a saved double_used list does not change its qty.
    """
    if ticker == "NAIL":
        return price <= 22
    if ticker == "KORU":
        return price <= 6
    return False


def _buy_computed(ticker, index, double_used):
    """First-buy size, or base once that level is already in double_used.

    SOXL is 20 on every rung. KORU is 1, or 2 on the first buy at 6 or
    below, then 1. NAIL is 20 at 22 and 40 at 20 or below, then 10 once
    that level is used.
    """
    price = ladders._price_at(ticker, index)
    if price is None:
        return None
    qty = _cfg(ticker)["buy_qty"](price)
    if index in set(double_used) and _in_double_zone(ticker, price):
        return _cfg(ticker)["base"]
    return qty


def _desired(ticker, sell_idx, buy_idx, mult, double_used=()):
    if not _valid_pair(ticker, sell_idx, buy_idx):
        return None
    sell_qty = ladders._sell_shares(_cfg(ticker))
    specs = [
        ("sell", sell_idx, sell_qty),
        ("sell", sell_idx + 1, sell_qty),
        ("buy", buy_idx, _buy_computed(ticker, buy_idx, double_used)),
        ("buy", buy_idx - 1, _buy_computed(ticker, buy_idx - 1, double_used)),
    ]
    out = []
    for side, index, computed in specs:
        price = ladders._price_at(ticker, index)
        qty = _sized(ticker, computed, mult)
        if price is None or qty is None:
            return None
        out.append({"side": side, "idx": index, "price": price, "qty": qty})
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
    buy_qty = _cfg(ticker)["buy_qty"]
    sell_qty = ladders._sell_shares(_cfg(ticker))
    side = fills[0]["side"]
    ordered = sorted(fills, key=lambda item: item["idx"], reverse=(side == "buy"))
    adds = []
    for _fill in ordered:
        if not buys or not sells:
            return None
        if side == "sell":
            sell_idx = max(order["idx"] for order in sells) + 1
            buy_idx = max(order["idx"] for order in buys) + 1
            if buy_idx >= min(order["idx"] for order in sells):
                return None
        else:
            buy_idx = min(order["idx"] for order in buys) - 1
            sell_idx = min(order["idx"] for order in sells) - 1
            if sell_idx <= max(order["idx"] for order in buys):
                return None
        sell_price = ladders._price_at(ticker, sell_idx)
        buy_price = ladders._price_at(ticker, buy_idx)
        if sell_price is None or buy_price is None:
            return None
        if side == "sell":
            pair = [("sell", sell_idx, sell_qty), ("buy", buy_idx, buy_qty(buy_price))]
        else:
            pair = [
                ("buy", buy_idx, buy_qty(buy_price)),
                ("sell", sell_idx, sell_qty),
            ]
        for add_side, index, computed in pair:
            qty = _sized(ticker, computed, mult)
            price = ladders._price_at(ticker, index)
            if qty is None or price is None:
                return None
            adds.append(
                {"side": add_side, "idx": index, "price": price, "qty": qty}
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
    filled = ladders._float(row.get("cns_qty"))
    cancelled = ladders._float(row.get("can_qty"))
    ordered = ladders._blank_number(row.get("orr_qty"))
    return {
        "ticker": ticker,
        "side": ladders._side(row),
        "price": price,
        "qty": qty,
        "ordered_qty": 0 if ordered is None else ordered,
        "filled_qty": 0 if filled is None else filled,
        "cancelled_qty": 0 if cancelled is None else cancelled,
        "idx": _level_of(ticker, price),
        "key": _fill_key(ticker, row),
        "orr_no": row.get("orr_no"),
        "orr_dt": _order_day(row.get("orr_dt")),
        "status": status,
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


def _legacy_tenth(raw):
    return raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _legacy_next_down(price):
    """One old KORU rung under `price`. That generator is not the live series."""
    base = price if isinstance(price, Decimal) else Decimal(str(price))
    primary = base / Decimal("1.1")
    if abs(primary - _legacy_tenth(primary)) == Decimal("0.05"):
        chosen = base * Decimal("0.9")
    else:
        chosen = primary
    rounded = _legacy_tenth(chosen)
    if rounded <= 0 or rounded >= base:
        return None
    return rounded


def _legacy_koru_price(index):
    """Price a pre-shared KORU index named. None when that index was not a rung."""
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    if 0 <= index < len(_KORU_LEGACY):
        return _KORU_LEGACY[index]
    if index > -1 or index < -80:
        return None
    current = Decimal(str(_KORU_LEGACY[0]))
    for _ in range(-index):
        current = _legacy_next_down(current)
        if current is None:
            return None
    return float(current)


def _shared_index_exact(price):
    """Shared index for `price` only when it is that rung, not a neighbor.

    The trading matcher allows 0.051, which is enough for 1.7 to look like
    1.65. Remapping uses this tighter check.
    """
    mapped = ladders._level_of("KORU", float(price))
    if mapped is None:
        return None
    rung = ladders._price_at("KORU", mapped)
    if rung is None or abs(float(rung) - float(price)) > 0.001:
        return None
    return mapped


def _legacy_koru_index(index):
    """Shared-ladder index for an old KORU index, or None if that price is gone."""
    price = _legacy_koru_price(index)
    if price is None:
        return None
    return _shared_index_exact(price)


def _migrate_koru_pair(pair):
    """Rewrite one old KORU pair onto the shared ladder.

    None means the sell or the buy is not a shared rung (the generated
    6.5 / 5.9 / 5.4 steps). The caller drops the pair so reset reviews
    KORU instead of trading the wrong prices.
    """
    if not isinstance(pair, dict):
        return None
    sell = _legacy_koru_index(pair.get("sell"))
    buy = _legacy_koru_index(pair.get("buy"))
    if not _valid_pair("KORU", sell, buy):
        return None
    used = []
    raw_used = pair.get("double_used")
    if isinstance(raw_used, list):
        for item in raw_used:
            if isinstance(item, bool):
                continue
            if isinstance(item, int):
                mapped = _legacy_koru_index(item)
            else:
                price = ladders._float(item)
                mapped = None if price is None else _shared_index_exact(price)
            if mapped is not None and mapped not in used:
                used.append(mapped)
    return {"sell": sell, "buy": buy, "double_used": used}


def _migrate_loaded(data):
    """Map a pre-shared file onto this ladder. NAIL and SOXL indexes stay."""
    if not isinstance(data, dict) or data.get("ladder_id") == _LADDER_ID:
        return data
    migrated = dict(data)
    raw = migrated.get("tickers")
    if isinstance(raw, dict) and "KORU" in raw:
        tickers = dict(raw)
        pair = _migrate_koru_pair(tickers.get("KORU"))
        if pair is None:
            tickers.pop("KORU", None)
        else:
            tickers["KORU"] = pair
        migrated["tickers"] = tickers
    migrated["ladder_id"] = _LADDER_ID
    return migrated


def _load_state():
    data = _load_json(_STATE_PATH)
    if not data or data.get("version") not in (1, 2):
        return None
    if not isinstance(data.get("handled"), list):
        return None
    return _migrate_loaded(data)


def _done_marker(slot, ticker, today):
    """Id kept in handled so an older run that drops reset_tickers still skips the ticker."""
    raw = "reset-done|%s|%s|%s" % (slot, ticker, today)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean_reset_tickers(raw):
    cleaned = {"09": {}, "17": {}}
    if not isinstance(raw, dict):
        return cleaned
    for slot in ("09", "17"):
        slot_raw = raw.get(slot)
        if not isinstance(slot_raw, dict):
            continue
        for name in ("NAIL", "KORU", "SOXL"):
            value = slot_raw.get(name)
            if isinstance(value, str) and len(value) == 8 and value.isdigit():
                cleaned[slot][name] = value
    return cleaned


def _pin_done_markers(handled, reset_tickers):
    wanted = []
    for slot in ("09", "17"):
        for ticker, day in (reset_tickers.get(slot) or {}).items():
            wanted.append(_done_marker(slot, ticker, day))
    rest = [key for key in handled if key not in wanted]
    rest.extend(wanted)
    return rest[-200:]


def _slot_done(state, slot, ticker, today):
    if slot not in ("09", "17") or not today:
        return False
    handled = state.get("handled") or []
    if _done_marker(slot, ticker, today) in handled:
        return True
    raw = state.get("reset_tickers")
    if not isinstance(raw, dict):
        return False
    slot_raw = raw.get(slot)
    if not isinstance(slot_raw, dict):
        return False
    return slot_raw.get(ticker) == today


def _mark_slot_done(state, slot, ticker, today):
    if slot not in ("09", "17") or ticker not in ("NAIL", "KORU", "SOXL"):
        return
    slot_map = state.setdefault("reset_tickers", {}).setdefault(slot, {})
    slot_map[ticker] = today
    marker = _done_marker(slot, ticker, today)
    handled = [key for key in state.get("handled", []) if isinstance(key, str)]
    if marker not in handled:
        handled.append(marker)
    state["handled"] = handled[-200:]


def _all_reset_done(state, plans, allow, slot, today):
    found = False
    for plan in plans:
        if plan["ticker"] not in allow or plan["review"]:
            continue
        found = True
        if not _slot_done(state, slot, plan["ticker"], today):
            return False
    return found


def _exception_this_run(plans, allow, state, slot, today, selected):
    """True when this batch may send the standing sell.

    A free slot is enough, including when SOXL's ladder is deferred.
    The caller still applies the open-order and already-filled checks
    before passing want_exception. `allow`, `state`, `slot`, `today`,
    and `selected` stay in the signature for that caller.
    """
    del allow, state, slot, today, selected
    for plan in plans:
        if plan["ticker"] == "SOXL":
            return True
    return False


def _reset_batch(plans, allow, state, slot, today, want_exception, exception_n=None):
    """Whole tickers only. A ticker that does not fit waits for a later run.

    `want_exception` True asks for one standing order, the historical
    call. `exception_n` asks for that many, and a short remaining slot
    count takes the first of them. `exception` is True only when every
    requested standing order fits. `exception_take` is how many do.
    """
    remaining = _MAX_NEW
    selected = []
    deferred = []
    for plan in plans:
        ticker = plan["ticker"]
        if ticker not in allow or plan["review"]:
            continue
        if _slot_done(state, slot, ticker, today):
            continue
        cost = len(plan["place"])
        if cost > _MAX_NEW:
            return {
                "selected": set(),
                "deferred": set(),
                "exception": False,
                "exception_take": 0,
                "blocked": True,
            }
        if cost > remaining:
            deferred.append(ticker)
            continue
        selected.append(ticker)
        remaining -= cost
    wanted = 0
    if want_exception:
        wanted = 1 if exception_n is None else exception_n
        if isinstance(wanted, bool) or not isinstance(wanted, int) or wanted < 1:
            wanted = 1
    take = 0
    if wanted and remaining >= 1 and _exception_this_run(
        plans, allow, state, slot, today, set(selected)
    ):
        take = min(wanted, remaining)
    return {
        "selected": set(selected),
        "deferred": set(deferred),
        "exception": take == wanted and wanted > 0,
        "exception_take": take,
        "blocked": False,
    }


def _clean_exception_resting(raw, exc_id=None):
    expected = _EXCEPTION_ID if exc_id is None else exc_id
    if not isinstance(raw, dict):
        return {}
    if raw.get("id") != expected:
        return {}
    day = raw.get("day")
    slot = raw.get("slot")
    if not isinstance(day, str) or len(day) != 8 or not day.isdigit():
        return {}
    if slot not in ("09", "17", ""):
        return {}
    cleaned = {"id": expected, "slot": slot, "day": day}
    at = raw.get("at")
    if isinstance(at, int) and not isinstance(at, bool) and at >= 0:
        cleaned["at"] = at
    return cleaned


def _exception_resting(state, slot, today, exc=None):
    """True when this bot already placed this standing order for this slot.

    The broker inquiry can omit a new order for a long time. The mark is
    only for the slot that placed it, so the next wipe's slot can send
    another if the order is still unfilled and not open. The SOXL sell
    keeps the top-level mark. Any other standing order keeps its own.
    """
    spec = _soxl_exc() if exc is None else exc
    if spec["id"] == _EXCEPTION_ID:
        raw = _clean_exception_resting(
            state.get("exception_resting") if isinstance(state, dict) else None
        )
    else:
        bucket = _read_exception_bucket(state, spec) or {}
        raw = _clean_exception_resting(bucket.get("resting"), spec["id"])
    if not raw or raw["day"] != today:
        return False
    if slot in ("09", "17"):
        return raw["slot"] == slot
    return True


def _mark_exception_resting(state, slot, today, now, exc=None):
    spec = _soxl_exc() if exc is None else exc
    mark = {
        "id": spec["id"],
        "slot": slot if slot in ("09", "17") else "",
        "day": today,
        "at": int(now.timestamp()),
    }
    if spec["id"] == _EXCEPTION_ID:
        state["exception_resting"] = mark
        return
    _ensure_exception_bucket(state, spec)["resting"] = mark


def _exception_due(state, opens, slot, today, exc=None):
    """True when this standing order is still open-work and not resting."""
    spec = _soxl_exc() if exc is None else exc
    if spec["id"] == _EXCEPTION_ID:
        return False
    if _exception_done_for(state, spec):
        return False
    if _exception_open(opens, spec, state):
        return False
    if _exception_resting(state, slot, today, spec):
        return False
    return True


def _reset_runnable(now):
    return not _orders_blocked(now) and _reset_slot(now) is not None


def _followup_allowed(now, gap=_FOLLOWUP_GAP):
    """True when another reset pass can start after `gap` seconds.

    The next pass has to stay in this slot and outside both wipe guards.
    """
    if not _reset_runnable(now):
        return False
    nxt = now + timedelta(seconds=gap)
    if not _reset_runnable(nxt):
        return False
    return _reset_slot(now) == _reset_slot(nxt)


def _bounded_reset_followups(once, sleep, now, max_extra=_MAX_FOLLOWUPS, gap=_FOLLOWUP_GAP):
    """Call `once` again while it still has deferred reset work.

    `once` returns (exit code, wants another pass). At most `max_extra`
    extra passes run, and none starts inside a blocked window or outside
    the current slot.
    """
    code, follow = once()
    extra = 0
    while follow and code == 0:
        if extra >= max_extra or not _followup_allowed(now(), gap):
            if extra >= max_extra:
                print("follow-up limit reached")
            else:
                print("follow-up stopped time guard")
            break
        extra += 1
        print("follow-up reset %s" % (extra,))
        sleep(gap)
        if not _reset_runnable(now()):
            print("follow-up stopped time guard")
            break
        code, follow = once()
    return code


def _fresh_state():
    return {
        "version": 2,
        "ladder_id": _LADDER_ID,
        "tickers": {},
        "handled": [],
        "exception_filled": False,
        "exception_id": "",
        "exception_filled_qty": 0,
        "exception_parts": {},
        "exception_resting": {},
        "recent_orders": [],
        "reset_done": {"09": "", "17": ""},
        "reset_tickers": {"09": {}, "17": {}},
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
        _note_http("token", status, payload)
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
    global _last_http
    rows = []
    prev = None
    cts = ""
    flag = ""
    _last_http = ""
    for _ in range(30):
        extra = {}
        if cts:
            extra["cts"] = cts
            if flag:
                extra["cts_flag"] = flag
        status, headers, payload = gb._post_bearer(url, gb._json_body(body), extra)
        if status != 200 or not isinstance(payload, dict):
            _note_http("unexecuted", status, payload, _http_date(body))
            return None
        page, state = gb._list_rows(payload, block)
        if page is None or state == "badtype":
            _note_http("unexecuted", status, payload, _http_date(body))
            return None
        new_cts = headers.get("cts", "").strip()
        new_flag = headers.get("cts_flag", "").strip()
        if prev is not None and new_cts and new_cts == prev:
            return rows
        rows.extend(page)
        rsp_cd, rsp_msg = gb._rsp(payload)
        if not gb._needs_next(new_flag, new_cts, rsp_cd, prev):
            if new_flag.upper() == "Y" and not new_cts:
                _note_http("unexecuted", status, payload, _http_date(body))
                return None
            if rsp_cd in gb._CONTINUE and not new_cts and new_flag.upper() != "N":
                _note_http("unexecuted", status, payload, _http_date(body))
                return None
            if state == "absent" and not rows and not gb._empty_ok(rsp_msg):
                return None
            return rows
        prev = new_cts
        cts = new_cts
        flag = new_flag
    return None


def _previous_trading_day(day):
    """The weekday before `day`. Saturday and Sunday fold back to Friday."""
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def _push_day(found, day):
    if day not in found:
        found.append(day)


def _weekdays_before(day, count):
    found = []
    cursor = day
    while len(found) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            found.append(cursor)
    return found


def _dates(now_kst):
    """Dates for open orders.

    The US session crosses midnight in Korea, so an order placed at
    04:05 KST on the 13th is still the 12th in New York. Query both
    calendars, plus the previous four weekdays, so a Friday order is
    still visible after a holiday weekend. Callers dedupe by order
    number. Fills stay on the short window in `_fill_dates`.
    """
    kst_day = _as_kst(now_kst).date()
    et_day = _as_kst(now_kst).astimezone(ET).date()
    found = []
    _push_day(found, kst_day)
    _push_day(found, et_day)
    for day in _weekdays_before(kst_day, 4):
        _push_day(found, day)
    for day in _weekdays_before(et_day, 1):
        _push_day(found, day)
    return [day.strftime("%Y%m%d") for day in found]


def _required_open_dates(now_kst):
    """The Korea date and the US date. An older weekday may fail without stopping the run."""
    kst_day = _as_kst(now_kst).date()
    et_day = _as_kst(now_kst).astimezone(ET).date()
    return {kst_day.strftime("%Y%m%d"), et_day.strftime("%Y%m%d")}


def _fill_dates(now_kst):
    """Today, yesterday, and the previous weekday. Not the open-order window.

    An older fill must not step a ladder the later reset already moved.
    """
    today = _as_kst(now_kst).date()
    found = []
    for day in (today, today - timedelta(days=1), _previous_trading_day(today)):
        _push_day(found, day)
    return [day.strftime("%Y%m%d") for day in found]


# Last open inquiry, for the cancel-dup log. Dates are YYYYMMDD and a
# row count. Drops are parsed ladder rows that were not treated as open.
_last_open_inquiry = {"dates": [], "drops": []}


def _inquiry(account, now_kst, kind):
    global _last_open_inquiry
    merged = []
    seen = set()
    drops = []
    queried = []
    dates = _dates(now_kst) if kind == "open" else _fill_dates(now_kst)
    required = _required_open_dates(now_kst) if kind == "open" else set(dates)
    for orr_dt in dates:
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
            if orr_dt not in required:
                line = _last_http or ("unexecuted date %s status none" % (orr_dt,))
                if line.startswith("ERROR http "):
                    line = "inquiry skip " + line[len("ERROR http "):]
                print(line)
                continue
            return None
        queried.append((orr_dt, len(rows)))
        for row in rows:
            if not isinstance(row, dict):
                continue
            parsed = _parse_row(row)
            if not parsed:
                continue
            if kind == "open":
                number = _order_number(parsed.get("orr_no"))
                if number is not None:
                    identity = (parsed["ticker"], parsed["side"], number)
                else:
                    identity = parsed["key"]
            else:
                identity = parsed["key"]
            if identity in seen:
                continue
            seen.add(identity)
            if parsed.get("status") == "2":
                if kind == "open":
                    drops.append(parsed)
                continue
            if kind == "open" and parsed["qty"] <= 0:
                drops.append(parsed)
                continue
            if kind == "fill" and (
                parsed["filled_qty"] <= 0 or parsed["cancelled_qty"] > 0
            ):
                continue
            merged.append(parsed)
    if kind == "open":
        _last_open_inquiry = {"dates": queried, "drops": drops}
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


_last_reject = None
_last_order_no = None


def _capture_reject(url, body, status, payload):
    """Remember a broker rejection. The log line never includes secrets."""
    global _last_reject
    rsp_cd, rsp_msg = gb._rsp(payload if isinstance(payload, dict) else {})
    act = ""
    ticker = ""
    price = ""
    if isinstance(body, dict):
        raw_act = body.get("act_no")
        if isinstance(raw_act, str):
            act = raw_act.strip()
        raw_ticker = body.get("iem_cd")
        if isinstance(raw_ticker, str):
            ticker = raw_ticker.strip()
        raw_price = body.get("fc_orr_uit_pr")
        if isinstance(raw_price, (int, float)) and not isinstance(raw_price, bool):
            price = raw_price
    if len(act) >= 4:
        rsp_cd = " ".join(rsp_cd.replace(act, "").split())
        rsp_msg = " ".join(rsp_msg.replace(act, "").split())
    side = {BUY_URL: "buy", SELL_URL: "sell", CANCEL_URL: "cancel"}.get(url, "")
    http = "" if status is None else status
    _last_reject = {
        "ticker": ticker,
        "side": side,
        "price": price,
        "http": http,
        "rsp_cd": rsp_cd,
        "rsp_msg": rsp_msg,
    }


def _log_reject():
    info = _last_reject
    if not info:
        return False
    print(
        "REJECT {ticker} {side} {price} http {http} rsp_cd {rsp_cd} rsp_msg {rsp_msg}".format(
            **info
        )
    )
    return True


def _post_order(url, body):
    global _last_reject, _last_order_no
    _last_reject = None
    _last_order_no = None
    if url not in _ORDER_URLS or _orders_blocked(_now_kst()):
        return None
    status, _headers, payload = gb._post_bearer(url, gb._json_body(body), {})
    if status == 401 or not _order_ok(status, payload):
        _capture_reject(url, body, status, payload)
        return None
    block = payload.get("Output_0") if isinstance(payload, dict) else None
    if isinstance(block, dict):
        _last_order_no = _order_number(block.get("orr_no"))
    return True


def _place(account, ticker, side, price, qty, live, counter):
    global _last_reject
    _last_reject = None
    if not live:
        return False
    index = _level_of(ticker, price)
    if index is None or qty < 1 or qty > _HARD_MAX[ticker]:
        return None
    kind = _order_type(_now_kst(), mode=os.environ.get("LADDER_MODE"))
    if kind is None:
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
        "ahi_nmn_pr_tp_cd": kind,
    }
    if side == "buy":
        body["wtm_cur_knd_cd"] = "1"
        return _post_order(BUY_URL, body)
    if side == "sell":
        return _post_order(SELL_URL, body)
    return None


def _cancel(account, ticker, order, live):
    global _last_reject
    _last_reject = None
    if not live:
        return False
    # A normal cancel is a ladder rung. soxl-rebuild also cancels SOXL
    # orders that are not rungs, such as the retired sell at 75.
    if order.get("idx") is None and order.get("rebuild_cancel") is not True:
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


def _soxl_exc():
    """Retired SOXL sell of 150 at 75. Callers that omit exc still see it.

    It is not placed. _exception_due and _exception_remaining are zero
    for this id. soxl-rebuild cancels an open row that matches it.
    """
    return _RETIRED_SOXL_SELL


def _near_price(price, target):
    if isinstance(price, bool) or not isinstance(price, (int, float)):
        return False
    return abs(float(price) - float(target)) < _EXCEPTION_TOL


def _near_exception(price):
    return _near_price(price, _EXCEPTION_PRICE)


def _ladder_sizes(exc):
    """Share counts the ladder itself uses at this standing price.

    None when the price is not a rung. KORU at 4.2 is 1, or 2 on the
    first buy. The 100-share order is neither of those.
    """
    index = _level_of(exc["ticker"], exc["price"])
    if index is None:
        return None
    sizes = set()
    base = _cfg(exc["ticker"])["base"]
    if isinstance(base, int) and not isinstance(base, bool) and base > 0:
        sizes.add(base)
    first = _cfg(exc["ticker"])["buy_qty"](float(exc["price"]))
    if isinstance(first, int) and not isinstance(first, bool) and first > 0:
        sizes.add(first)
    return sizes


def _clean_order_nos(raw):
    found = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        number = _order_number(item)
        if number is None or number in found:
            continue
        found.append(number)
    return found[-20:]


def _read_exception_bucket(state, exc):
    if not isinstance(state, dict):
        return None
    raw = state.get("exception_orders")
    if not isinstance(raw, dict):
        return None
    item = raw.get(exc["id"])
    return item if isinstance(item, dict) else None


def _ensure_exception_bucket(state, exc):
    book = state.setdefault("exception_orders", {})
    item = book.get(exc["id"])
    if not isinstance(item, dict):
        item = {
            "filled": False,
            "filled_qty": 0,
            "parts": {},
            "resting": {},
            "order_nos": [],
        }
        book[exc["id"]] = item
    return item


def _exception_order_nos(state, exc):
    if exc["id"] == _EXCEPTION_ID:
        return ()
    bucket = _read_exception_bucket(state, exc)
    if not bucket:
        return ()
    return tuple(_clean_order_nos(bucket.get("order_nos")))


def _add_exception_order_no(state, exc, number):
    """Remember one broker order number for a non-SOXL standing order.

    The SOXL sell is not a rung, so its order number is not needed to
    tell it from the ladder. The KORU buy shares 4.2 with a 2-share rung.
    """
    if not isinstance(state, dict) or exc["id"] == _EXCEPTION_ID:
        return
    if _order_number(number) is None:
        return
    bucket = _ensure_exception_bucket(state, exc)
    nos = _clean_order_nos(bucket.get("order_nos"))
    if number not in nos:
        nos.append(number)
    bucket["order_nos"] = nos[-20:]


def _match_open_exception(order, exc, state=None):
    """True when this open row is `exc`, not the ladder order at that price."""
    if not isinstance(order, dict):
        return False
    if order.get("ticker") != exc["ticker"] or order.get("side") != exc["side"]:
        return False
    if not _near_price(order.get("price"), exc["price"]):
        return False
    if not _positive_qty(order.get("qty", 0)):
        return False
    if exc["id"] == _EXCEPTION_ID:
        return True
    number = _order_number(order.get("orr_no"))
    if number is not None and number in _exception_order_nos(state, exc):
        return True
    sizes = _ladder_sizes(exc)
    if not sizes:
        return True
    qty = _share_count(order.get("qty", 0))
    ordered = _share_count(order.get("ordered_qty", 0))
    if ordered and ordered not in sizes:
        return True
    if qty and qty not in sizes:
        return True
    return False


def _positive_qty(qty):
    return not isinstance(qty, bool) and isinstance(qty, (int, float)) and qty > 0


def _share_count(qty):
    """Whole shares. Zero when `qty` is missing, negative, or not a number."""
    if not _positive_qty(qty):
        return 0
    whole = int(round(float(qty)))
    if abs(float(qty) - whole) > 1e-6 or whole < 1:
        return 0
    return whole


def _fill_key_ok(key):
    return (
        isinstance(key, str)
        and len(key) == 64
        and all(ch in "0123456789abcdef" for ch in key)
    )


def _exception_order(order, state=None):
    """True for a standing order. False for the ladder's own rung.

    A SOXL sell at 75 is never a rung. A SOXL buy at 18.7 is the standing
    order: that price is not a rung. A KORU buy at 4.2 is the standing
    order when its size is not the ladder's 1 or 2, or when its order
    number is one this bot stored for that buy. A partial that leaves 2
    shares uses the order number, or the original order size when the
    row still carries it.
    """
    if not isinstance(order, dict):
        return False
    ticker = order.get("ticker")
    for exc in _EXCEPTIONS:
        if ticker != exc["ticker"]:
            continue
        if _match_open_exception(order, exc, state):
            return True
    return False


def _counts_as_exception(ticker, order, state=None):
    """True for a standing order even when the row omits the ticker name."""
    if ticker not in ("SOXL", "KORU") or not isinstance(order, dict):
        return False
    if order.get("ticker") not in (None, ticker):
        return False
    probe = order if order.get("ticker") == ticker else dict(order, ticker=ticker)
    return _exception_order(probe, state)


def _exception_id_owned(state):
    return isinstance(state, dict) and state.get("exception_id") == _EXCEPTION_ID


def _clean_parts_map(raw):
    if not isinstance(raw, dict):
        return {}
    found = {}
    for key, qty in raw.items():
        count = _share_count(qty)
        if _fill_key_ok(key) and count > 0:
            found[key] = count
    if len(found) > 80:
        found = dict(list(found.items())[-80:])
    return found


def _clean_exception_parts(state):
    """Fill-key to shares already counted, only for this exception id."""
    if not _exception_id_owned(state):
        return {}
    return _clean_parts_map(state.get("exception_parts"))


def _raw_exception_qty(state):
    """Stored share count for this id. A dict is read at this id's key."""
    if not _exception_id_owned(state):
        return 0
    raw = state.get("exception_filled_qty", 0)
    if isinstance(raw, dict):
        return _share_count(raw.get(_EXCEPTION_ID, 0))
    return _share_count(raw)


def _stored_exception_qty(state):
    """Shares of this sell already counted. Any other exception id is zero."""
    if not _exception_id_owned(state):
        return 0
    parts = _clean_exception_parts(state)
    if parts:
        return sum(parts.values())
    return _raw_exception_qty(state)


def _exception_done(state):
    """True only when this sell of 150 at 75 is fully sold.

    Retired, and none of them block this sell: buy 100 at 75 on
    2026-10-31 (a bare true), buy 200 at 38 (soxl-buy-200-38), and buy
    200 at 37.5 filled 2026-11-21 (soxl-buy-200-37-5).
    """
    if not _exception_id_owned(state):
        return False
    if _stored_exception_qty(state) >= _EXCEPTION_QTY:
        return True
    return bool(state.get("exception_filled"))


def _bucket_qty(bucket):
    parts = _clean_parts_map(bucket.get("parts") if isinstance(bucket, dict) else None)
    if parts:
        return sum(parts.values())
    if not isinstance(bucket, dict):
        return 0
    return _share_count(bucket.get("filled_qty", 0))


def _exception_done_for(state, exc):
    """True when `exc` has reached its target. The SOXL sell uses the legacy fields."""
    if exc["id"] == _EXCEPTION_ID:
        return _exception_done(state)
    bucket = _read_exception_bucket(state, exc)
    if not bucket:
        return False
    if _bucket_qty(bucket) >= exc["qty"]:
        return True
    return bool(bucket.get("filled"))


def _stored_qty_for(state, exc):
    if exc["id"] == _EXCEPTION_ID:
        return _stored_exception_qty(state)
    bucket = _read_exception_bucket(state, exc)
    if not bucket:
        return 0
    return _bucket_qty(bucket)


def _exception_remaining(state, exc=None):
    """Shares still to send. Zero once this standing order is done."""
    spec = _soxl_exc() if exc is None else exc
    if spec["id"] == _EXCEPTION_ID:
        return 0
    if _exception_done_for(state, spec):
        return 0
    left = spec["qty"] - _stored_qty_for(state, spec)
    if left < 1:
        return 0
    return int(left)


def _exception_fill_shares(order, exc=None, state=None):
    """Filled shares on one inquiry row that belong to this standing order.

    The SOXL sell ignores a row that already has a ladder index, because
    75 is not a rung. The KORU buy is on a rung, so a 2-share fill there
    counts only when the order number or the original size says it is
    the 100-share order. A ladder fill of 2 at 4.2 stays a ladder fill.
    """
    spec = _soxl_exc() if exc is None else exc
    if not isinstance(order, dict):
        return 0
    if order.get("ticker") != spec["ticker"] or order.get("side") != spec["side"]:
        return 0
    if not _near_price(order.get("price"), spec["price"]):
        return 0
    if spec["id"] == _EXCEPTION_ID and order.get("idx") is not None:
        return 0
    cancelled = order.get("cancelled_qty", 0)
    if isinstance(cancelled, bool) or not isinstance(cancelled, (int, float)):
        cancelled = 0
    if cancelled > 0:
        return 0
    filled = _share_count(order.get("filled_qty", 0))
    if filled <= 0:
        return 0
    if spec["id"] == _EXCEPTION_ID:
        return filled
    number = _order_number(order.get("orr_no"))
    if number is not None and number in _exception_order_nos(state, spec):
        return filled
    sizes = _ladder_sizes(spec)
    if not sizes:
        return filled
    ordered = _share_count(order.get("ordered_qty", 0))
    if ordered and ordered not in sizes:
        return filled
    if filled not in sizes:
        return filled
    return 0


def _row_is_exception_fill(order, state=None):
    for exc in _EXCEPTIONS:
        if _exception_fill_shares(order, exc, state) > 0:
            return True
    return False


def _place_exception(account, live, counter, qty=None, exc=None):
    """Send one standing order. `qty` is the unfilled remainder.

    The SOXL body matches a ladder sell: no wtm_cur_knd_cd. The KORU
    body matches a ladder buy and does send that field. Neither share
    count is capped by _HARD_MAX.
    """
    global _last_reject
    _last_reject = None
    spec = _soxl_exc() if exc is None else exc
    if not live:
        return False
    send = spec["qty"] if qty is None else qty
    send = _share_count(send)
    if send < 1:
        return False
    if send > spec["qty"]:
        send = spec["qty"]
    kind = _order_type(_now_kst(), mode=os.environ.get("LADDER_MODE"))
    if kind is None:
        return None
    if counter[0] >= _MAX_NEW:
        return None
    counter[0] += 1
    body = {
        "act_no": account,
        "fc_sec_trd_nat_cd": "200",
        "iem_cd": spec["ticker"],
        "orr_qty": send,
        "fc_orr_uit_pr": round(float(spec["price"]), 2),
        "ahi_nmn_pr_tp_cd": kind,
    }
    if spec["side"] == "buy":
        body["wtm_cur_knd_cd"] = "1"
        return _post_order(BUY_URL, body)
    if spec["side"] == "sell":
        return _post_order(SELL_URL, body)
    return None


def _exception_open(orders, exc=None, state=None):
    """True when `exc` is already open. The default is the SOXL sell."""
    spec = _soxl_exc() if exc is None else exc
    return any(_match_open_exception(order, spec, state) for order in orders or [])


def _exception_filled(fills, handled):
    """True when `fills` has a new SOXL sell at 75.

    A buy at 75 or at 37.5 does not count. `handled` holds keys already
    counted. A cancelled row does not count. Fully sold is separate:
    that waits until the counted shares reach 150.
    """
    for order in fills or []:
        if _exception_fill_shares(order) <= 0:
            continue
        if order.get("key") in handled:
            continue
        return True
    return False


def _note_soxl_fills(state, fills):
    """Add new SOXL sells at 75 onto the legacy exception fields.

    The same fill key is counted once. A later inquiry that shows more
    shares on that key adds only the increase. Buys do not count. Old
    exception ids do not count. Done only when the total reaches 150.
    """
    if _exception_done(state):
        return
    parts = _clean_exception_parts(state)
    remembered = []
    changed = False
    for order in fills or []:
        qty = _exception_fill_shares(order)
        if qty <= 0:
            continue
        key = order.get("key")
        if not _fill_key_ok(key):
            continue
        prev = parts.get(key, 0)
        if qty <= prev:
            continue
        parts[key] = qty
        remembered.append(key)
        changed = True
    if not changed:
        return
    total = sum(parts.values())
    state["exception_id"] = _EXCEPTION_ID
    state["exception_filled_qty"] = total
    state["exception_parts"] = parts
    if total >= _EXCEPTION_QTY:
        state["exception_filled"] = True
    _remember(state, remembered)


def _note_named_fills(state, fills, exc):
    """Add new fills of one standing order onto its own bucket.

    The retired SOXL sell uses the legacy fields instead. The buy of
    400 at 18.7 uses this bucket, keyed by its own id.
    """
    done = _exception_done_for(state, exc)
    bucket = _read_exception_bucket(state, exc)
    parts = _clean_parts_map(None if bucket is None else bucket.get("parts"))
    remembered = []
    changed = False
    for order in fills or []:
        qty = _exception_fill_shares(order, exc, state)
        if qty <= 0:
            continue
        number = _order_number(order.get("orr_no"))
        if number is not None:
            _add_exception_order_no(state, exc, number)
        key = order.get("key")
        if not _fill_key_ok(key):
            continue
        remembered.append(key)
        if done:
            continue
        prev = parts.get(key, 0)
        if qty <= prev:
            continue
        parts[key] = qty
        changed = True
    if remembered:
        _remember(state, remembered)
    if done or not changed:
        return
    total = sum(parts.values())
    bucket = _ensure_exception_bucket(state, exc)
    bucket["filled_qty"] = total
    bucket["parts"] = parts
    bucket["filled"] = total >= exc["qty"]


def _note_exception_fills(state, fills):
    """Record fills for every standing order. Ladder fills are not included."""
    if not isinstance(state, dict):
        return
    _note_soxl_fills(state, fills)
    for exc in _EXCEPTIONS:
        if exc["id"] == _EXCEPTION_ID:
            continue
        _note_named_fills(state, fills, exc)


def _absorb_exception_order_ids(state, orders):
    """Store order numbers of standing rows that are already unambiguous."""
    if not isinstance(state, dict):
        return
    for order in orders or []:
        if not isinstance(order, dict):
            continue
        number = _order_number(order.get("orr_no"))
        if number is None:
            continue
        for exc in _EXCEPTIONS:
            if exc["id"] == _EXCEPTION_ID:
                continue
            if _match_open_exception(order, exc, state):
                _add_exception_order_no(state, exc, number)


def _clean_exception_orders(state):
    """Per-id progress for standing orders other than the SOXL sell."""
    raw = state.get("exception_orders") if isinstance(state, dict) else None
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for exc in _EXCEPTIONS:
        if exc["id"] == _EXCEPTION_ID:
            continue
        item = raw.get(exc["id"])
        if not isinstance(item, dict):
            continue
        parts = _clean_parts_map(item.get("parts"))
        qty = sum(parts.values()) if parts else _share_count(item.get("filled_qty", 0))
        done = qty >= exc["qty"] or bool(item.get("filled"))
        if done and qty < exc["qty"]:
            qty = exc["qty"]
        resting = {}
        if not done:
            resting = _clean_exception_resting(item.get("resting"), exc["id"])
        order_nos = _clean_order_nos(item.get("order_nos"))
        keep = done or qty > 0 or parts or resting or order_nos
        if not keep:
            continue
        cleaned[exc["id"]] = {
            "filled": done,
            "filled_qty": qty if done or qty > 0 else 0,
            "parts": parts if done or qty > 0 else {},
            "resting": resting,
            "order_nos": order_nos,
        }
    return cleaned


def _call_place_exception(actions, counter, qty, exc):
    """Place one standing order. The SOXL sell stays a two-argument call."""
    fn = actions["place_exception"]
    if exc["id"] == _EXCEPTION_ID:
        return fn(counter, qty)
    return fn(counter, qty, exc)


def _fresh_exception_open(actions, opens, exc, state=None):
    named = actions.get("exception_open_for")
    if named is not None:
        return named(exc["id"])
    if exc["id"] == _EXCEPTION_ID:
        return actions["exception_open"]()
    return _exception_open(opens, exc, state)


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
    if buy_idx >= sell_idx:
        return False
    return (
        ladders._price_at(ticker, sell_idx) is not None
        and ladders._price_at(ticker, sell_idx + 1) is not None
        and ladders._price_at(ticker, buy_idx) is not None
        and ladders._price_at(ticker, buy_idx - 1) is not None
    )


def _used_indexes(ticker, raw):
    found = []
    if not isinstance(raw, list):
        return found
    for item in raw:
        index = None
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            if ladders._price_at(ticker, item) is not None:
                index = item
        else:
            price = ladders._float(item)
            if price is not None:
                index = ladders._level_of(ticker, price)
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
    if ladders._price_at(ticker, third) is None:
        return False
    return extra == {("buy", third)}


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


def _order_day(value):
    """YYYYMMDD from a date, a dashed date, or a date with a time suffix."""
    if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
        text = str(int(value))
    else:
        text = ladders._text(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        return ""
    year = int(digits[:4])
    month = int(digits[4:6])
    day = int(digits[6:8])
    if 2020 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
        return digits[:8]
    return ""


def _order_number(value):
    """Broker order number, when the value is a non-negative integer."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value.is_integer() and value >= 0:
            return int(value)
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    elif isinstance(value, dict):
        return _order_number(value.get("orr_no"))
    return None


def _price_key(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number <= 0 or number != number or number == float("inf"):
        return None
    return round(number, 2)


def _clean_recent(raw):
    """Keep only ladder places this bot recorded. Drop anything else."""
    if not isinstance(raw, list):
        return []
    found = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ticker = item.get("ticker")
        side = item.get("side")
        if ticker not in ("NAIL", "KORU", "SOXL") or side not in ("buy", "sell"):
            continue
        price = _price_key(item.get("price"))
        qty = item.get("qty")
        at = item.get("at")
        if price is None:
            continue
        if isinstance(qty, bool) or not isinstance(qty, (int, float)):
            continue
        if abs(float(qty) - round(float(qty))) > 1e-9:
            continue
        qty_i = int(round(float(qty)))
        if qty_i < 1 or qty_i > _HARD_MAX[ticker]:
            continue
        if isinstance(at, bool) or not isinstance(at, int) or at < 0:
            continue
        slot = item.get("slot") or ""
        if not isinstance(slot, str) or slot not in ("", "09", "17"):
            slot = ""
        day = item.get("day") or ""
        if not isinstance(day, str) or not (
            day == "" or (len(day) == 8 and day.isdigit())
        ):
            day = ""
        idx = _level_of(ticker, price)
        if idx is None:
            continue
        cleaned = {
            "ticker": ticker,
            "side": side,
            "price": price,
            "qty": qty_i,
            "idx": idx,
            "at": at,
            "slot": slot,
            "day": day,
        }
        number = _order_number(item.get("orr_no"))
        if number is not None:
            cleaned["orr_no"] = number
        found.append(cleaned)
    return found[-_RECENT_MAX:]


def _clean_saved_at(raw):
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def _clean_seq(raw):
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def _stamp_state(state):
    """Mark one save. Old files with no seq start at 1."""
    cleaned = _clean_state(state if isinstance(state, dict) else {})
    cleaned["saved_at"] = int(time.time())
    cleaned["seq"] = _clean_seq(cleaned.get("seq")) + 1
    return cleaned


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
    reset_tickers = _clean_reset_tickers(state.get("reset_tickers"))
    done_exception = _exception_done(state)
    filled_qty = _stored_exception_qty(state)
    if done_exception and filled_qty < _EXCEPTION_QTY:
        filled_qty = _EXCEPTION_QTY
    keep_exception = done_exception or filled_qty > 0
    resting = {} if done_exception else _clean_exception_resting(
        state.get("exception_resting")
    )
    cleaned = {
        "version": 2,
        "ladder_id": _LADDER_ID,
        "tickers": tickers,
        "handled": _pin_done_markers(handled, reset_tickers),
        "exception_filled": done_exception,
        "exception_id": _EXCEPTION_ID if keep_exception else "",
        "exception_filled_qty": filled_qty if keep_exception else 0,
        "exception_parts": _clean_exception_parts(state) if keep_exception else {},
        "exception_resting": resting,
        "recent_orders": _clean_recent(state.get("recent_orders")),
        "saved_at": _clean_saved_at(state.get("saved_at")),
        "seq": _clean_seq(state.get("seq")),
        "reset_done": done,
        "reset_tickers": reset_tickers,
    }
    extra = _clean_exception_orders(state)
    if extra:
        cleaned["exception_orders"] = extra
    return cleaned


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
    price = ladders._price_at(ticker, pair["buy"])
    if price is None:
        return None
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


def _anchor_fill_step(ticker, pair, fills, handled, mult, state=None):
    """Step a reset pair when an unhandled fill is on the current anchor.

    The pre-reset guard does not place a new quote, and the broker
    cancel-all then leaves an empty book. Restoring the saved pair
    would quote the filled rung again. A fill the step rules accept
    moves the pair first. Anything else leaves the saved pair, so an
    odd fill cannot block the reset.
    """
    core = (
        ("sell", pair["sell"]),
        ("sell", pair["sell"] + 1),
        ("buy", pair["buy"]),
        ("buy", pair["buy"] - 1),
    )
    hit = []
    for side, index in core:
        found = _confirmed_fills(
            ticker, [{"side": side, "idx": index}], fills, handled, state
        )
        if not found:
            continue
        hit.append((side, index))
    if not hit:
        return pair
    sell_missing = [index for side, index in hit if side == "sell"]
    buy_missing = [index for side, index in hit if side == "buy"]
    stepped = _step_plan(ticker, pair, sell_missing, buy_missing, mult)
    if stepped is None:
        return pair
    return stepped[0]


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


def _confirmed_fills(ticker, missing, fills, handled, state=None):
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
            and not _row_is_exception_fill(row, state)
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


def _soxl_rung_price(order):
    idx = order.get("idx")
    if isinstance(idx, bool) or not isinstance(idx, int):
        idx = _level_of("SOXL", order.get("price"))
    if isinstance(idx, bool) or not isinstance(idx, int):
        return None
    return ladders._price_at("SOXL", idx)


def _soxl_pre_rebuild_book(opens, state):
    """True when the pre-20 SOXL book is still open at sell 36 / buy 30.

    That book is buys of 8 at 26 and 28 and sells of 4 at 33 and 36.
    Scheduled reset and intraday leave it for soxl-rebuild: no cancel,
    no new ladder order, no buy of 400 at 18.7, and no anchor step.
    A later book that already has a 20-share rung, or the new sell at
    39 or buy at 30, is not this hold. The retired sell at 75 is not a
    ladder rung and does not by itself hold the book.
    """
    if not isinstance(state, dict):
        return False
    pair = _stored_pair(state, "SOXL")
    if pair is None:
        return False
    if ladders._price_at("SOXL", pair["sell"]) != 36:
        return False
    if ladders._price_at("SOXL", pair["buy"]) != 30:
        return False
    sized = {}
    for order in opens or []:
        if not isinstance(order, dict) or order.get("ticker") != "SOXL":
            continue
        if order.get("side") not in ("buy", "sell"):
            continue
        if _match_open_exception(order, _soxl_exc(), state):
            continue
        if _exception_order(order, state):
            continue
        price = _soxl_rung_price(order)
        if price is None:
            continue
        qty = _share_count(order.get("qty", 0))
        if qty < 1:
            continue
        if qty == 20:
            return False
        sized[(order.get("side"), price)] = qty
    if not sized:
        return False
    if any(qty == 8 and side == "buy" for (side, _price), qty in sized.items()):
        return True
    if sized.get(("sell", 33)) == 4:
        return True
    old_core = (
        sized.get(("buy", 26)) in (4, 8)
        or sized.get(("buy", 28)) in (4, 8)
        or sized.get(("sell", 36)) in (4, 8)
    )
    new_rung = ("sell", 39) in sized or ("buy", 30) in sized
    return old_core and not new_rung


def _ladder_book_empty(opens, state=None):
    """True when no ladder order is open. Standing orders do not count."""
    if not isinstance(opens, list):
        return True
    for order in opens:
        if not isinstance(order, dict):
            continue
        if order.get("ticker") not in ("NAIL", "KORU", "SOXL"):
            continue
        if order.get("side") not in ("buy", "sell"):
            continue
        if _exception_order(order, state):
            continue
        if _match_open_exception(order, _soxl_exc(), state):
            continue
        qty = order.get("qty", 0)
        if isinstance(qty, bool) or not isinstance(qty, (int, float)):
            continue
        if qty > 0:
            return False
    return True


def _parse_reset_comment(raw=None):
    """`reset`, `reset day`, or `reset premarket`.

    None when that is not the whole comment. An empty string means this
    run is not a comment, so the clock picks the slot. `day` is slot 09
    and `premarket` is slot 17.
    """
    if raw is None:
        raw = os.environ.get("RESET_COMMENT", "")
    if not isinstance(raw, str):
        return None
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    if "\n" in text:
        return None
    parts = text.split()
    if parts == ["reset"]:
        return ""
    if parts == ["reset", "day"]:
        return "09"
    if parts == ["reset", "premarket"]:
        return "17"
    return None


def _reset_comment_block(now_kst):
    """A skip line when this reset comment must not trade. Empty means go on."""
    parsed = _parse_reset_comment()
    if parsed is None:
        return "reset skip parse"
    if parsed == "":
        return ""
    if parsed != _reset_slot(now_kst):
        return "reset skip slot"
    return ""


def _should_heal(event, now_kst, opens, state):
    """True when intraday should run the reset for the open slot.

    The cron for that slot did not mark reset_done. An empty ladder book
    is the missed reset after the broker wipe. A ticker already marked
    done today means an earlier pass still has deferred work, so this
    pass continues as reset and does not cancel the finished ticker.
    A book that already has ladder orders, with nothing done today, stays
    on the intraday path.
    """
    if not _clocked(event) or not _live_flag() or not isinstance(state, dict):
        return False
    now_kst = _as_kst(now_kst)
    # Stay out of an intraday run that was going to log OK skip. The 17:10
    # slot cannot place until premarket, which is also when intraday opens.
    if not _window_open("intraday", now_kst, now_kst.astimezone(ET)):
        return False
    slot = _reset_slot(now_kst)
    if slot is None:
        return False
    today = now_kst.strftime("%Y%m%d")
    done = state.get("reset_done")
    if isinstance(done, dict) and done.get(slot) == today:
        return False
    if _ladder_book_empty(opens, state):
        return True
    raw = state.get("reset_tickers")
    if not isinstance(raw, dict):
        return False
    slot_raw = raw.get(slot)
    if not isinstance(slot_raw, dict):
        return False
    return today in slot_raw.values()


def _clocked(event):
    """Cron and manual dispatch share the trading gates. Push never trades."""
    return event in ("schedule", "workflow_dispatch")


_UNEXPECTED_REASONS = ("remain", "inquiry", "cancel", "place", "exists", "select")


def _unexpected_line(reason, ticker=None):
    """One stable stop line. Never includes an order, account, or message."""
    if reason not in _UNEXPECTED_REASONS:
        reason = "error"
    if ticker in ("NAIL", "KORU", "SOXL"):
        return "ERROR unexpected %s %s" % (reason, ticker)
    return "ERROR unexpected %s" % (reason,)


def _unexpected_type(exc):
    """Type name only. Exception text can carry an account or token."""
    name = type(exc).__name__
    if (
        isinstance(name, str)
        and name.isascii()
        and name.isidentifier()
        and 1 <= len(name) <= 40
    ):
        return "ERROR unexpected %s" % (name,)
    return "ERROR unexpected"


def _may_trade(event, mode, now_kst, now_et, report_only):
    """Live orders are clocked runs only, inside the mode window, and past the KST guard."""
    return (
        _live_flag()
        and _clocked(event)
        and _window_open(mode, now_kst, now_et)
        and not report_only
        and not _orders_blocked(now_kst)
        and not _orders_blocked(_now_kst())
    )


def _rung_index(ticker, raw):
    price = ladders._float(raw)
    if price is None:
        return None
    return ladders._level_of(ticker, price)


def _parse_adjust():
    """One ticker and either both anchors or a single step. None is config."""
    ticker = os.environ.get("ADJUST_TICKER", "").strip().upper()
    sell_raw = os.environ.get("ADJUST_SELL", "").strip()
    buy_raw = os.environ.get("ADJUST_BUY", "").strip()
    step = os.environ.get("ADJUST_STEP", "").strip().lower()
    preview_raw = os.environ.get("ADJUST_PREVIEW", "").strip().lower()
    if ticker not in ("NAIL", "KORU", "SOXL"):
        return None
    if preview_raw not in ("true", "false"):
        return None
    if step == "none":
        step = ""
    if step == "step buy one":
        step = "buy"
    if step == "step sell one":
        step = "sell"
    if step not in ("", "buy", "sell"):
        return None
    if step and (sell_raw or buy_raw):
        return None
    spec = {"ticker": ticker, "step": step, "preview": preview_raw == "true"}
    if step:
        return spec
    if not sell_raw or not buy_raw:
        return None
    sell = _rung_index(ticker, sell_raw)
    buy = _rung_index(ticker, buy_raw)
    if not _valid_pair(ticker, sell, buy):
        return None
    spec["sell"] = sell
    spec["buy"] = buy
    return spec


def _adjust_pair(spec, stored):
    """Target pair. Explicit anchors keep double_used. A step uses the step rules."""
    if spec["step"] == "buy":
        return _step_buy(spec["ticker"], stored)
    if spec["step"] == "sell":
        return _step_sell(spec["ticker"], stored)
    return {
        "sell": spec["sell"],
        "buy": spec["buy"],
        "double_used": list(stored.get("double_used") or []),
    }


def _same_qty(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    return abs(float(left) - float(right)) < 1e-9


def _px(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return "%s" % (value,)


def _last_wipe(now):
    """Latest broker cancel-all at or before `now`, as a Unix time.

    The broker drops every open order when the US session closes at
    20:00 US Eastern. That is 10:00 KST in US standard time and 09:00 KST
    in US daylight time, so it follows the ET clock, not a fixed KST hour.
    """
    et = now.astimezone(ET)
    day = et.date() if et.hour >= 20 else et.date() - timedelta(days=1)
    edge = datetime(day.year, day.month, day.day, 20, 0, tzinfo=ET)
    return int(edge.timestamp())


def _recent_alive(orders, now):
    """Places still inside the grace window and placed after the last wipe.

    A place from before the broker's cancel-all is gone, whatever the
    grace window says, so it must never count as resting.
    """
    cutoff = max(int(now.timestamp()) - _RECENT_GRACE, _last_wipe(now))
    return [item for item in orders if item.get("at", 0) >= cutoff]


def _same_placed(item, order):
    """True when inquiry row `order` is the remembered place `item`."""
    if item.get("ticker") != order.get("ticker") or item.get("side") != order.get("side"):
        return False
    if not _same_qty(item.get("qty"), order.get("qty")):
        return False
    left = _price_key(item.get("price"))
    right = _price_key(order.get("price"))
    if left is not None and right is not None and abs(left - right) < 0.001:
        return True
    return (
        isinstance(item.get("idx"), int)
        and not isinstance(item.get("idx"), bool)
        and item.get("idx") == order.get("idx")
    )


def _unseen_recent(recent, opens, now):
    """Remembered places the inquiry does not already show.

    One visible order consumes one memory. A second memory stays, so a
    lagging inquiry cannot hide a copy this bot already sent.
    """
    alive = _recent_alive(_clean_recent(recent), now)
    pool = [order for order in opens if isinstance(order, dict)]
    used = set()
    unseen = []
    ordered = sorted(alive, key=lambda row: (row.get("at", 0), row.get("orr_no", 0)))
    for item in ordered:
        match = None
        for index, order in enumerate(pool):
            if index in used or not _same_placed(item, order):
                continue
            match = index
            break
        if match is None:
            unseen.append(item)
        else:
            used.add(match)
    return unseen


def _filled_recent(fills, item):
    """True when a fill at this rung means the place is no longer resting.

    A fill of a different order at the same rung does not count. The
    ladder buys a used level again at the base size, and that earlier
    fill must not erase the new order. Dropping it makes the next run
    treat the rung as missing.
    """
    place_no = _order_number(item.get("orr_no"))
    for row in fills or []:
        if not isinstance(row, dict):
            continue
        if row.get("ticker") != item.get("ticker") or row.get("side") != item.get("side"):
            continue
        if row.get("cancelled_qty", 0) > 0 or row.get("filled_qty", 0) <= 0:
            continue
        idx = row.get("idx")
        if not isinstance(idx, int) or isinstance(idx, bool):
            idx = _level_of(item.get("ticker"), row.get("price"))
        if idx != item.get("idx"):
            continue
        fill_no = _order_number(row.get("orr_no"))
        if place_no is not None and fill_no is not None and place_no != fill_no:
            continue
        return True
    return False


def _prepare_recent(state, opens, now, mode, slot, today, fills=()):
    """Orders still hidden from the inquiry.

    A reset drops places from any earlier slot. The broker wipe removed
    those, so they must not block the new book. Places from this slot
    stay until the inquiry shows them, a fill shows the rung is done,
    or the grace window ends.
    """
    recent = _recent_alive(_clean_recent(state.get("recent_orders")), now)
    if mode == "reset" and slot in ("09", "17"):
        recent = [
            item
            for item in recent
            if item.get("slot") == slot and item.get("day") == today
        ]
    hidden = _unseen_recent(recent, opens, now)
    return [item for item in hidden if not _filled_recent(fills, item)]


def _recent_levels(recent, ticker):
    found = set()
    for item in recent or []:
        if item.get("ticker") != ticker or item.get("side") not in ("buy", "sell"):
            continue
        idx = item.get("idx")
        if not isinstance(idx, int) or isinstance(idx, bool):
            continue
        found.add((item["side"], idx))
    return found


def _recent_has(recent, ticker, side, index):
    return (side, index) in _recent_levels(recent, ticker)


def _log_resting(ticker, side, price, qty):
    print("%s resting %s %s qty %s" % (ticker, side, _px(price), _px(qty)))


def _remember_placed(state, ticker, item, result, now):
    """Record one successful ladder place. The exception sell is not a rung."""
    price = _price_key(item.get("price"))
    side = item.get("side")
    qty = item.get("qty")
    if price is None or ticker not in ("NAIL", "KORU", "SOXL") or side not in ("buy", "sell"):
        return
    if isinstance(qty, bool) or not isinstance(qty, (int, float)):
        return
    mark = {
        "ticker": ticker,
        "side": side,
        "price": price,
        "qty": qty,
        "at": int(now.timestamp()),
        "slot": _reset_slot(now) or "",
        "day": now.strftime("%Y%m%d"),
    }
    number = _order_number(result)
    if number is not None:
        mark["orr_no"] = number
    idx = item.get("idx")
    if not isinstance(idx, int) or isinstance(idx, bool):
        idx = _level_of(ticker, price)
    if isinstance(idx, int) and not isinstance(idx, bool):
        mark["idx"] = idx
    current = list(state.get("recent_orders") or [])
    current.append(mark)
    state["recent_orders"] = _clean_recent(current)


def _forget_recent(state, ticker, order):
    """Drop the memory for the order number just cancelled.

    Another memory at the same rung stays. That is the copy the inquiry
    has not shown yet, and removing it would let the bot send a third.
    """
    number = _order_number(order.get("orr_no"))
    if number is None:
        return
    recent = list(state.get("recent_orders") or [])
    for index, item in enumerate(recent):
        if item.get("ticker") == ticker and item.get("orr_no") == number:
            del recent[index]
            state["recent_orders"] = recent
            return


def _book_delta(ticker, desired, orders, state=None):
    """Cancel ladder orders that are not in `desired`. Keep standing orders."""
    mine = [dict(order) for order in orders if order.get("ticker") == ticker]
    buys, sells, _ignored = _split(ticker, mine, state)
    pool = []
    for order in buys + sells:
        if _exception_order(order, state):
            continue
        pool.append(order)
    used = set()
    keep = []
    place = []
    for item in desired:
        found = None
        for index, order in enumerate(pool):
            if index in used:
                continue
            if order.get("side") != item["side"] or order.get("idx") != item["idx"]:
                continue
            if not _same_qty(order.get("qty"), item["qty"]):
                continue
            found = index
            break
        if found is None:
            place.append(item)
        else:
            used.add(found)
            keep.append(pool[found])
    cancel = [order for index, order in enumerate(pool) if index not in used]
    pooled = {id(order) for order in pool}
    ignored_orders = [order for order in mine if id(order) not in pooled]
    return cancel, place, keep, ignored_orders


def _print_adjust_plan(
    ticker, pair, cancel, place, keep, ignored_orders, exception_due=False, exception_qty=None
):
    sell = ladders._price_at(ticker, pair["sell"])
    buy = ladders._price_at(ticker, pair["buy"])
    print("%s anchor sell %s buy %s" % (ticker, _px(sell), _px(buy)))
    if exception_due:
        qty = _EXCEPTION_QTY if exception_qty is None else exception_qty
        print(
            "exception plan place sell %s qty %s"
            % (_px(_EXCEPTION_PRICE), _px(qty))
        )

    def _line(kind, side, price, qty):
        print(
            "%s plan %s %s %s qty %s"
            % (ticker, kind, side, _px(price), _px(qty))
        )

    for order in cancel:
        _line("cancel", order.get("side", ""), order.get("price"), order.get("qty"))
    for item in place:
        _line("place", item["side"], item["price"], item["qty"])
    for order in keep:
        _line("keep", order.get("side", ""), order.get("price"), order.get("qty"))
    for order in ignored_orders:
        _line("ignore", order.get("side", ""), order.get("price"), order.get("qty"))
    for name in ("NAIL", "KORU", "SOXL"):
        if name == ticker:
            continue
        if name == "SOXL" and exception_due:
            continue
        print("%s unchanged" % (name,))


def _run_adjust(event, now_kst, now_et, opens, _fills, state, actions):
    """Preview or sync one ticker. Fills do not move anchors.

    A SOXL sell fill at 75 still reduces the standing order.
    """
    mult = _mult()
    spec = _parse_adjust()
    if mult is None or spec is None or _order_type_override() is None:
        print("ERROR config")
        return 1
    if state is None:
        print("ERROR config")
        return 1
    working = _clean_state(state)
    ticker = spec["ticker"]
    stored = _stored_pair(working, ticker)
    if stored is None:
        print("NEEDS_REVIEW " + ticker)
        print("OK dry")
        return 0
    pair = _adjust_pair(spec, stored)
    if pair is None:
        print("NEEDS_REVIEW " + ticker)
        print("OK dry")
        return 0
    desired = _desired(
        ticker, pair["sell"], pair["buy"], mult, pair["double_used"]
    )
    if not desired:
        print("ERROR config")
        return 1
    cancel, place, keep, ignored_orders = _book_delta(ticker, desired, opens, working)
    preview = spec["preview"]
    today = now_kst.strftime("%Y%m%d")
    slot = _reset_slot(now_kst)
    working["recent_orders"] = _prepare_recent(
        working, opens, now_kst, "adjust", slot, today, _fills
    )
    covered = _recent_levels(working["recent_orders"], ticker)
    visible = set()
    for order in opens:
        if order.get("ticker") != ticker:
            continue
        if _exception_order(order, working):
            continue
        idx = order.get("idx")
        if not isinstance(idx, int) or isinstance(idx, bool):
            idx = _level_of(ticker, order.get("price"))
        if isinstance(idx, int) and not isinstance(idx, bool):
            visible.add((order.get("side"), idx))
    kept_place = []
    for item in place:
        level = (item["side"], item["idx"])
        if level in covered and level not in visible:
            _log_resting(ticker, item["side"], item["price"], item["qty"])
            continue
        kept_place.append(item)
    place = kept_place
    _absorb_exception_order_ids(working, opens)
    _note_exception_fills(working, _fills)
    exception_qty = _exception_remaining(working)
    exception_due = _exception_due(working, opens, slot, today)
    if not preview:
        if not _live_flag() or not _clocked(event):
            _print_adjust_plan(
                ticker, pair, cancel, place, keep, ignored_orders, exception_due, exception_qty
            )
            print("OK dry")
            return 0
        if ticker not in _allow_set():
            print("ERROR config")
            return 1
        if _orders_blocked(now_kst) or _orders_blocked(_now_kst()):
            print("skip: time guard")
            return 0
        if not _window_open("adjust", now_kst, now_et):
            print("OK skip")
            return 0
        kind, reason = _session(now_kst, now_et, "adjust")
        wall = _now_kst()
        wall_kind, wall_reason = _session(wall, wall.astimezone(ET), "adjust")
        if reason == "ERROR config" or wall_reason == "ERROR config":
            print("ERROR config")
            return 1
        if not kind or kind != wall_kind:
            print(reason or wall_reason or "skip: session closed")
            return 0
        if len(place) > _MAX_NEW:
            _print_adjust_plan(
                ticker, pair, cancel, place, keep, ignored_orders, exception_due, exception_qty
            )
            print("ERROR max_orders")
            return 1
        print("order type %s" % (kind,))
    else:
        kind, _reason = _session(now_kst, now_et, "adjust")
        if kind:
            print("order type %s" % (kind,))
    _print_adjust_plan(
        ticker, pair, cancel, place, keep, ignored_orders, exception_due, exception_qty
    )
    if preview:
        print("OK preview")
        return 0

    def _guard_stop():
        if _orders_blocked(now_kst) or _orders_blocked(_now_kst()):
            print("skip: time guard")
            return True
        return False

    if _guard_stop():
        return 0
    for order in cancel:
        if _guard_stop():
            return 0
        result = actions["cancel"](ticker, order)
        if result is None:
            if not _log_reject():
                print(_unexpected_line("cancel", ticker))
            else:
                print("ERROR reject")
            return 1
        if not result:
            print(_unexpected_line("cancel", ticker))
            return 1
        _forget_recent(working, ticker, order)
    keep_levels = {(order.get("side"), order.get("idx")) for order in keep}
    checked = set()
    for order in cancel:
        level = (order.get("side"), order.get("idx"))
        if level in keep_levels or level in checked:
            continue
        checked.add(level)
        still = actions["exists"](ticker, order.get("side"), order.get("idx"))
        if still is None:
            print(_unexpected_line("exists", ticker))
            return 1
        if still:
            print(_unexpected_line("remain", ticker))
            return 1
    counter = [0]
    for item in place:
        if _guard_stop():
            return 0
        if _recent_has(working.get("recent_orders"), ticker, item["side"], item["idx"]):
            continue
        exists = actions["exists"](ticker, item["side"], item["idx"])
        if exists is None:
            print(_unexpected_line("exists", ticker))
            return 1
        if exists:
            continue
        result = actions["place"](ticker, item, counter)
        if result is None:
            if not _log_reject():
                print(_unexpected_line("place", ticker))
            else:
                print("ERROR reject")
            return 1
        if not result:
            print(_unexpected_line("place", ticker))
            return 1
        _remember_placed(working, ticker, item, result, now_kst)
        actions["save_state"](_clean_state(working))
    if exception_due:
        if _guard_stop():
            return 0
        current = actions["exception_open"]()
        if current is None:
            print(_unexpected_line("inquiry", "SOXL"))
            return 1
        if not current:
            if exception_qty < 1:
                pass
            elif counter[0] >= _MAX_NEW:
                print("DEFERRED exception")
            else:
                result = actions["place_exception"](counter, exception_qty)
                if result is None:
                    if not _log_reject():
                        print(_unexpected_line("place", "SOXL"))
                    else:
                        print("ERROR reject")
                    return 1
                if not result:
                    print(_unexpected_line("place", "SOXL"))
                    return 1
                print("exception placed 1")
                _mark_exception_resting(working, slot, today, now_kst)
    working["tickers"][ticker] = pair
    actions["save_state"](_clean_state(working))
    print("OK live")
    return 0


def _parse_cancel_dup(raw=None):
    """`cancel-dup NAIL BUY 28 10`. None when that is not the whole text."""
    if raw is None:
        raw = os.environ.get("CANCEL_DUP", "")
    if not isinstance(raw, str):
        return None
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or "\n" in text:
        return None
    parts = text.split()
    if len(parts) != 5 or parts[0].lower() != "cancel-dup":
        return None
    ticker = parts[1].upper()
    side = parts[2].lower()
    if ticker not in ("NAIL", "KORU", "SOXL") or side not in ("buy", "sell"):
        return None
    token = parts[3]
    if token.startswith(("+", "-")) or token.count(".") > 1:
        return None
    if not token or token == "." or not all(ch.isdigit() or ch == "." for ch in token):
        return None
    if not parts[4].isdigit():
        return None
    qty = int(parts[4])
    if qty < 1 or qty > _HARD_MAX[ticker]:
        return None
    price = _price_key(ladders._float(token))
    if price is None:
        return None
    return {"ticker": ticker, "side": side, "price": price, "qty": qty}


def _dup_match(order, spec):
    if order.get("ticker") != spec["ticker"] or order.get("side") != spec["side"]:
        return False
    if not _same_qty(order.get("qty"), spec["qty"]):
        return False
    px = _price_key(order.get("price"))
    if px is None:
        return False
    return abs(px - float(spec["price"])) < 0.001


def _pick_duplicate(matches):
    """Newest identical order that has an order number.

    Later `orr_dt` wins. On the same day the higher `orr_no` is the one
    the broker assigned last.
    """
    ranked = []
    for order in matches:
        number = _order_number(order.get("orr_no"))
        if number is None:
            continue
        day = order.get("orr_dt")
        if not isinstance(day, str):
            day = ""
        ranked.append((day, number, order))
    if not ranked:
        return None
    ranked.sort()
    return ranked[-1][2]


def _log_cancel_book(ticker, opens):
    """Every open row for this ticker, then rows the book did not keep.

    Side, price, remaining qty, order number, and order date only.
    """
    meta = _last_open_inquiry if isinstance(_last_open_inquiry, dict) else {}
    dates = meta.get("dates") or []
    if dates:
        print(
            "cancel-dup inquiry "
            + " ".join("%s %s" % (day, count) for day, count in dates)
        )
    shown = []
    for order in opens:
        if isinstance(order, dict) and order.get("ticker") == ticker:
            shown.append(("open", order))
    for order in meta.get("drops") or []:
        if isinstance(order, dict) and order.get("ticker") == ticker:
            shown.append(("drop", order))
    if not shown:
        print("cancel-dup open %s none" % (ticker,))
        return

    def _key(item):
        order = item[1]
        return (
            order.get("orr_dt") or "",
            _order_number(order.get("orr_no")) or -1,
            item[0],
        )

    for kind, order in sorted(shown, key=_key):
        number = _order_number(order.get("orr_no"))
        side = order.get("side") if order.get("side") in ("buy", "sell") else "?"
        day = order.get("orr_dt")
        if not isinstance(day, str) or len(day) != 8 or not day.isdigit():
            day = "none"
        print(
            "cancel-dup %s %s %s %s qty %s orr_no %s orr_dt %s"
            % (
                kind,
                ticker,
                side,
                _px(order.get("price")),
                _px(order.get("qty")),
                number if number is not None else "none",
                day,
            )
        )


def _run_cancel_dup(event, now_kst, now_et, opens, state, actions):
    """Cancel one duplicate. Leave every other order alone."""
    spec = _parse_cancel_dup()
    if spec is None:
        print("cancel-dup skip parse")
        return 0
    if event not in ("schedule", "workflow_dispatch", "issue_comment"):
        print("OK dry")
        return 0
    if _orders_blocked(now_kst) or _orders_blocked(_now_kst()):
        print("skip: time guard")
        return 0
    if not _window_open("cancel-dup", now_kst, now_et):
        print("OK skip")
        return 0
    if not _live_flag():
        print("OK dry")
        return 0
    if spec["ticker"] not in _allow_set():
        print("cancel-dup skip ticker")
        return 0
    _log_cancel_book(spec["ticker"], opens)
    # The retired SOXL sell at 75 is not a ladder duplicate, even when
    # the unsold remainder is small enough to pass the hard-max parse.
    matches = [
        order
        for order in opens
        if isinstance(order, dict)
        and _dup_match(order, spec)
        and not _exception_order(order, state)
        and not _match_open_exception(order, _soxl_exc(), state)
    ]
    label = "%s %s %s qty %s" % (
        spec["ticker"],
        spec["side"],
        _px(spec["price"]),
        _px(spec["qty"]),
    )
    if len(matches) < 2:
        print("cancel-dup skip %s matches %s" % (label, len(matches)))
        return 0
    chosen = _pick_duplicate(matches)
    number = None if chosen is None else _order_number(chosen.get("orr_no"))
    if chosen is None or number is None:
        print("cancel-dup skip %s no order number" % (label,))
        return 0
    order = dict(chosen)
    if not isinstance(order.get("idx"), int) or isinstance(order.get("idx"), bool):
        order["idx"] = _level_of(spec["ticker"], order.get("price"))
    if order.get("idx") is None:
        print("cancel-dup skip %s not a rung" % (label,))
        return 0
    result = actions["cancel"](spec["ticker"], order)
    if result is None:
        if not _log_reject():
            print(_unexpected_line("cancel", spec["ticker"]))
        else:
            print("ERROR reject")
        return 1
    if not result:
        print(_unexpected_line("cancel", spec["ticker"]))
        return 1
    if state is not None:
        working = _clean_state(state)
        _forget_recent(working, spec["ticker"], order)
        actions["save_state"](_clean_state(working))
    print("cancel-dup cancelled %s orr_no %s" % (label, number))
    print("OK cancel-dup")
    return 0


def _rebuild_live():
    """Live SOXL rebuild is its own switch. LIVE_TRADING does not turn it on."""
    return os.environ.get("SOXL_REBUILD_LIVE") == "on"


def _kept_soxl_400(order, state):
    """True for the standing buy of 400 at 18.7. Rebuild leaves it open."""
    exc = _EXCEPTION_BY_ID.get(_SOXL_BUY_400_ID)
    if exc is None:
        return False
    return _match_open_exception(order, exc, state)


def _soxl_exception_lines(state, opens, slot, today):
    """Due SOXL standing orders this rebuild would place.

    The buy of 400 at 18.7 is placed only by reset. The KORU buy is
    not part of this rebuild.
    """
    lines = []
    due = []
    for exc in _EXCEPTIONS:
        if exc["ticker"] != "SOXL" or exc["id"] == _SOXL_BUY_400_ID:
            continue
        if not _exception_due(state, opens, slot, today, exc):
            continue
        left = _exception_remaining(state, exc)
        if left < 1:
            continue
        due.append((exc, left))
        lines.append(
            "exception plan place %s %s qty %s" % (exc["side"], _px(exc["price"]), left)
        )
    return due, lines


# Chosen core after the 11:55 KST buy of 4 at 30. That fill is already
# handled: rebuild keeps this pair and later resets must not step it.
_SOXL_REBUILD_SELL = 36
_SOXL_REBUILD_BUY = 30


def _soxl_rebuild_pair(stored):
    """Sell 36 / buy 30, keeping any saved double_used list."""
    sell = ladders._level_of("SOXL", _SOXL_REBUILD_SELL)
    buy = ladders._level_of("SOXL", _SOXL_REBUILD_BUY)
    if not _valid_pair("SOXL", sell, buy):
        return None
    used = []
    if isinstance(stored, dict):
        used = list(stored.get("double_used") or [])
    return {"sell": sell, "buy": buy, "double_used": used}


def _core_buy_fill_keys(fills, buy_idx):
    """Keys for the already-handled SOXL buy at the core anchor."""
    keys = []
    for row in fills or []:
        if not isinstance(row, dict):
            continue
        if row.get("ticker") != "SOXL" or row.get("side") != "buy":
            continue
        idx = row.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int):
            idx = _level_of("SOXL", row.get("price"))
        if idx != buy_idx:
            continue
        key = row.get("key")
        if isinstance(key, str) and key and key not in keys:
            keys.append(key)
    return keys


def _soxl_rebuild_targets(opens, state):
    """Every open SOXL order in this account except the buy of 400 at 18.7.

    The whole SOXL book is being replaced. That standing buy stays.
    An order does not need a ladder index. KORU and NAIL are not in
    this list.
    """
    targets = []
    for order in opens or []:
        if not isinstance(order, dict) or order.get("ticker") != "SOXL":
            continue
        if order.get("side") not in ("buy", "sell"):
            continue
        if _share_count(order.get("qty", 0)) < 1:
            continue
        if _kept_soxl_400(order, state):
            continue
        row = dict(order)
        row["rebuild_cancel"] = True
        targets.append(row)
    return targets


def _soxl_open_levels(orders, state):
    """SOXL ladder rungs still open. The buy of 400 at 18.7 is not one."""
    levels = set()
    for order in orders or []:
        if not isinstance(order, dict) or order.get("ticker") != "SOXL":
            continue
        if order.get("side") not in ("buy", "sell"):
            continue
        if _kept_soxl_400(order, state):
            continue
        idx = order.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int):
            idx = _level_of("SOXL", order.get("price"))
        if isinstance(idx, bool) or not isinstance(idx, int):
            continue
        levels.add((order.get("side"), idx))
    return levels


def _soxl_seen_fill_keys(fills):
    """SOXL fill keys already on the book. A manual buy is one of these.

    Holdings can change without a ladder fill. Remembering the key keeps
    the saved pair at sell 36 / buy 30. A fill that arrives later has a
    new key and can still step the anchor.
    """
    keys = []
    for row in fills or []:
        if not isinstance(row, dict) or row.get("ticker") != "SOXL":
            continue
        key = row.get("key")
        if _fill_key_ok(key) and key not in keys:
            keys.append(key)
    return keys


def _run_soxl_rebuild(event, now_kst, now_et, opens, fills, state, actions):
    """Cancel open SOXL orders and quote sell 36 / buy 30 at 20 shares.

    Every open SOXL order in this account is cancelled, including one
    that is not a ladder rung, except the buy of 400 at 18.7. That buy
    stays open and is not placed here. After the cancels, the open book
    is read again and a rung that is still open is not placed a second
    time. The saved pair stays sell 36 / buy 30.
    A manual buy is not a ladder fill and does not step that pair.
    KORU, NAIL, and the other account are not touched. Dry unless
    SOXL_REBUILD_LIVE is on. A dry run does not save state.
    """
    mult = _mult()
    if mult is None or _order_type_override() is None or state is None:
        print("ERROR config")
        return 1
    working = _clean_state(state)
    stored = _stored_pair(working, "SOXL")
    if stored is None:
        print("NEEDS_REVIEW SOXL")
        print("OK dry")
        return 0
    _note_exception_fills(working, fills)
    pair = _soxl_rebuild_pair(stored)
    if pair is None:
        print("ERROR config")
        return 1
    handled_fill = _soxl_seen_fill_keys(fills)
    desired = _desired("SOXL", pair["sell"], pair["buy"], mult, pair["double_used"])
    if not desired:
        print("ERROR config")
        return 1
    cancel = _soxl_rebuild_targets(opens, working)
    place = list(desired)
    today = now_kst.strftime("%Y%m%d")
    slot = _reset_slot(now_kst) or ""
    due, extra_lines = _soxl_exception_lines(working, opens, slot, today)
    sell = ladders._price_at("SOXL", pair["sell"])
    buy = ladders._price_at("SOXL", pair["buy"])
    print("SOXL anchor sell %s buy %s" % (_px(sell), _px(buy)))
    print("order type %s" % (_order_type(now_kst, now_et, "adjust") or "none",))

    def _line(kind, side, price, qty):
        print("SOXL plan %s %s %s qty %s" % (kind, side, _px(price), _px(qty)))

    for order in cancel:
        _line("cancel", order.get("side", ""), order.get("price"), order.get("qty"))
    for item in place:
        _line("place", item["side"], item["price"], item["qty"])
    for order in opens or []:
        if _kept_soxl_400(order, working):
            _line("ignore", order.get("side", ""), order.get("price"), order.get("qty"))
    for line in extra_lines:
        print(line)
    print("NAIL unchanged")
    print("KORU unchanged")
    live = (
        _rebuild_live()
        and event in ("schedule", "workflow_dispatch")
        and not _orders_blocked(now_kst)
        and not _orders_blocked(_now_kst())
        and _window_open("adjust", now_kst, now_et)
    )
    if not live:
        print("OK dry")
        return 0
    kind = _order_type(now_kst, now_et, "adjust")
    wall = _now_kst()
    wall_kind = _order_type(wall, wall.astimezone(ET), "adjust")
    if not kind or kind != wall_kind:
        print("skip: session closed")
        return 0
    if len(place) + len(due) > _MAX_NEW:
        print("ERROR max_orders")
        return 1
    counter = [0]
    failed = False
    cancelled_nos = set()
    for order in cancel:
        result = actions["cancel"]("SOXL", order)
        if not result:
            failed = True
            if not _log_reject():
                print(_unexpected_line("cancel", "SOXL"))
            continue
        number = _order_number(order.get("orr_no"))
        if number is not None:
            cancelled_nos.add(number)
    inquire = actions.get("fresh_opens")
    if callable(inquire):
        fresh = inquire()
        if fresh is None:
            print(_unexpected_line("inquiry", "SOXL"))
            return 1
    else:
        fresh = []
        for order in opens or []:
            number = _order_number(order.get("orr_no"))
            if number is not None and number in cancelled_nos:
                continue
            fresh.append(order)
    open_levels = _soxl_open_levels(fresh, working)
    placed_levels = set()
    for item in place:
        level = (item["side"], item["idx"])
        if level in open_levels or level in placed_levels:
            _log_resting("SOXL", item["side"], item["price"], item["qty"])
            continue
        result = actions["place"]("SOXL", item, counter)
        if not result:
            failed = True
            if not _log_reject():
                print(_unexpected_line("place", "SOXL"))
            continue
        placed_levels.add(level)
        _remember_placed(working, "SOXL", item, result, now_kst)
    placed_n = 0
    for exc, left in due:
        if counter[0] >= _MAX_NEW:
            print("DEFERRED exception")
            break
        result = _call_place_exception(actions, counter, left, exc)
        if not result:
            failed = True
            if not _log_reject():
                print(_unexpected_line("place", "SOXL"))
            break
        placed_n += 1
        _mark_exception_resting(working, slot if slot in ("09", "17") else "", today, now_kst, exc)
        number = _order_number(result)
        if number is not None:
            _add_exception_order_no(working, exc, number)
    if placed_n:
        print("exception placed %s" % (placed_n,))
    # Seen fills, including a manual buy, stay handled. The pair does not step.
    _remember(working, handled_fill)
    working["tickers"]["SOXL"] = pair
    actions["save_state"](_clean_state(working))
    still = _soxl_rebuild_targets(fresh, working)
    for order in still:
        print(
            "SOXL still open %s %s qty %s"
            % (order.get("side", ""), _px(order.get("price")), _px(order.get("qty")))
        )
    if failed or still:
        return 1
    print("OK live")
    return 0


def run(mode, event, now_kst, now_et, opens, fills, state, actions):
    """Pure orchestration. `actions` performs live calls or records dry intent."""
    global _wants_reset_followup
    _wants_reset_followup = False
    if mode == "cancel-dup":
        return _run_cancel_dup(event, now_kst, now_et, opens, state, actions)
    if mode == "soxl-rebuild":
        return _run_soxl_rebuild(event, now_kst, now_et, opens, fills, state, actions)
    mult = _mult()
    if mult is None or mode not in ("reset", "intraday", "adjust") or _order_type_override() is None:
        print("ERROR config")
        return 1
    if mode == "adjust":
        return _run_adjust(event, now_kst, now_et, opens, fills, state, actions)

    def _stop_for_guard():
        if _orders_blocked(now_kst) or _orders_blocked(_now_kst()):
            print("skip: time guard")
            return True
        return False

    if _stop_for_guard():
        return 0
    if mode == "intraday" and _should_heal(event, now_kst, opens, state):
        print("reset heal")
        mode = "reset"
    if mode == "reset":
        blocked = _reset_comment_block(now_kst)
        if blocked:
            print(blocked)
            return 0
    if _clocked(event) and not _window_open(mode, now_kst, now_et):
        print("OK skip")
        return 0
    report_only = state is None
    working = _fresh_state() if state is None else _clean_state(state)
    allow = _allow_set()
    live = _may_trade(event, mode, now_kst, now_et, report_only)
    slot = _reset_slot(now_kst) if mode == "reset" else None
    today = now_kst.strftime("%Y%m%d")
    if (
        _clocked(event)
        and _live_flag()
        and slot
        and not report_only
        and working.get("reset_done", {}).get(slot) == today
    ):
        print("OK skip")
        return 0
    kind, _reason = _session(now_kst, now_et, mode)
    wall_kst = _now_kst()
    wall_kind, wall_reason = _session(wall_kst, wall_kst.astimezone(ET), mode)
    if _reason == "ERROR config" or wall_reason == "ERROR config":
        print("ERROR config")
        return 1
    if not kind or kind != wall_kind:
        print(_reason or wall_reason or "skip: session closed")
        return 0
    print(f"order type {kind}")
    _absorb_exception_order_ids(working, opens)
    _note_exception_fills(working, fills)
    working["recent_orders"] = _prepare_recent(
        working, opens, now_kst, mode, slot, today, fills
    )

    hold_soxl = mode in ("reset", "intraday") and _soxl_pre_rebuild_book(opens, working)
    plans = []
    for ticker in ("NAIL", "KORU", "SOXL"):
        if ticker == "SOXL" and hold_soxl:
            print("SOXL skip old book")
            stored = None if report_only else _stored_pair(working, "SOXL")
            plans.append(
                {
                    "ticker": "SOXL",
                    "buys": 0,
                    "sells": 0,
                    "ignored": 0,
                    "place": [],
                    "cancel": [],
                    "review": False,
                    "indexes": stored,
                    "remember": [],
                    "hold": True,
                }
            )
            continue
        mine = [order for order in opens if order.get("ticker") == ticker]
        buys, sells, ignored = _split(ticker, [dict(order) for order in mine], working)
        recent_levels = _recent_levels(working.get("recent_orders"), ticker)
        covered_orders = list(buys + sells) + [
            {"side": side, "idx": idx} for side, idx in recent_levels
        ]
        review = False
        place = []
        cancel = []
        remember = []
        indexes = None
        stored = None if report_only else _stored_pair(working, ticker)
        if stored is None:
            review = True
        elif mode == "reset":
            pair = _anchor_fill_step(
                ticker, stored, fills, set(working["handled"]), mult, working
            )
            place = _desired(
                ticker, pair["sell"], pair["buy"], mult, pair["double_used"]
            ) or []
            if not place:
                review = True
                place = []
            elif ticker == "SOXL":
                # An open order that already matches side, rung, and qty
                # stays. The 18:11 reset must not send a second copy.
                cancel, place, _keep, _ignored = _book_delta(
                    "SOXL", place, opens, working
                )
                indexes = pair
            else:
                cancel = [
                    order for order in buys + sells if not _exception_order(order, working)
                ]
                indexes = pair
        else:
            missing = _core_missing(stored, covered_orders)
            if missing:
                found = _confirmed_fills(
                    ticker, missing, fills, set(working["handled"]), working
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
                        inquiry_levels = {
                            (order["side"], order["idx"]) for order in buys + sells
                        }
                        open_levels = inquiry_levels | recent_levels
                        place = []
                        for item in desired:
                            level = (item["side"], item["idx"])
                            if level not in open_levels:
                                place.append(item)
                                continue
                            if level in recent_levels and level not in inquiry_levels:
                                _log_resting(
                                    ticker, item["side"], item["price"], item["qty"]
                                )
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

    if live and mode == "reset" and slot:
        for name in ("NAIL", "KORU", "SOXL"):
            if _slot_done(working, slot, name, today):
                _mark_slot_done(working, slot, name, today)

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
            and not _exception_order(order, working)
        }
        for item in plan["place"]:
            if (item["side"], item["idx"]) not in existing:
                planned += 1
    due = []
    if live and mode == "reset":
        for exc in _EXCEPTIONS:
            if exc["ticker"] not in allow:
                continue
            # The pre-20 book is left for soxl-rebuild. Do not add the
            # 400 buy beside those 4- and 8-share orders.
            if hold_soxl and exc["id"] == _SOXL_BUY_400_ID:
                continue
            if _exception_due(working, opens, slot, today, exc):
                due.append(exc)
    want_exception = bool(due)
    if want_exception:
        planned += len(due)
    batch = None
    over = False
    if live and mode == "reset":
        batch = _reset_batch(
            plans,
            allow,
            working,
            slot,
            today,
            want_exception,
            len(due) if due else None,
        )
        over = batch["blocked"]
    elif live and planned > _MAX_NEW:
        over = True
    if over:
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
    rejected = False

    def _save_progress():
        if live:
            actions["save_state"](_clean_state(working))

    def _note_reject():
        """Log one broker reject and keep the other tickers going."""
        nonlocal rejected
        if not _log_reject():
            return False
        rejected = True
        return True

    def _finish_reject():
        _save_progress()
        print("ERROR reject")
        return 1

    def _abort_unexpected(reason, ticker=None):
        print(_unexpected_line(reason, ticker))
        if rejected:
            _save_progress()
            print("ERROR reject")
        return 1

    def _guard_stop():
        if not _stop_for_guard():
            return None
        if rejected:
            return _finish_reject()
        return 0

    for plan in plans:
        placed = 0
        cancelled = 0
        ticker_failed = False
        deferred = batch is not None and plan["ticker"] in batch["deferred"]
        already = (
            batch is not None
            and plan["ticker"] in allow
            and not plan["review"]
            and plan["ticker"] not in batch["selected"]
            and not deferred
        )
        touch = (
            live
            and plan["ticker"] in allow
            and not plan["review"]
            and not deferred
            and not already
        )
        if touch:
            stopped = _guard_stop()
            if stopped is not None:
                return stopped
        if touch and mode == "reset":
            for order in plan["cancel"]:
                stopped = _guard_stop()
                if stopped is not None:
                    return stopped
                result = actions["cancel"](plan["ticker"], order)
                if result is None:
                    if _note_reject():
                        ticker_failed = True
                        break
                    return _abort_unexpected("cancel", plan["ticker"])
                if result:
                    cancelled += 1
                    sent_any = True
                    _forget_recent(working, plan["ticker"], order)
            # Orders still open after the cancels are not a broker reject.
            # Stop the run, as before. None means the fresh query failed.
            if not ticker_failed and plan["cancel"]:
                cleared = actions["confirm_clear"](plan["ticker"])
                if cleared is None:
                    return _abort_unexpected("inquiry", plan["ticker"])
                if not cleared:
                    return _abort_unexpected("remain", plan["ticker"])
        if touch and not ticker_failed:
            for item in plan["place"]:
                stopped = _guard_stop()
                if stopped is not None:
                    return stopped
                if _recent_has(
                    working.get("recent_orders"),
                    plan["ticker"],
                    item["side"],
                    item["idx"],
                ):
                    if mode == "reset":
                        _log_resting(
                            plan["ticker"], item["side"], item["price"], item["qty"]
                        )
                    continue
                exists = actions["exists"](plan["ticker"], item["side"], item["idx"])
                if exists is None:
                    return _abort_unexpected("exists", plan["ticker"])
                if exists:
                    continue
                result = actions["place"](plan["ticker"], item, counter)
                if result is None:
                    if _note_reject():
                        ticker_failed = True
                        break
                    return _abort_unexpected("place", plan["ticker"])
                if result:
                    placed += 1
                    sent_any = True
                    _remember_placed(
                        working, plan["ticker"], item, result, now_kst
                    )
                    if live:
                        actions["save_state"](_clean_state(working))
            # A partial ladder must not advance. The next run retries it.
            if not ticker_failed and mode == "intraday" and plan["remember"]:
                _remember(working, plan["remember"])
                if plan["indexes"] is not None:
                    working["tickers"][plan["ticker"]] = plan["indexes"]
            if not ticker_failed and mode == "reset" and not plan.get("hold"):
                _remember(
                    working,
                    [
                        row["key"]
                        for row in fills
                        if row.get("ticker") == plan["ticker"]
                        and isinstance(row.get("key"), str)
                    ],
                )
                if slot:
                    _mark_slot_done(working, slot, plan["ticker"], today)
        place_n = 0 if already else len(plan["place"])
        cancel_n = 0 if already or mode != "reset" else len(plan["cancel"])
        _report(
            plan["ticker"],
            plan["buys"],
            plan["sells"],
            plan["ignored"],
            place_n,
            cancel_n,
            plan["review"],
            placed,
            cancelled,
        )
        if deferred:
            print("DEFERRED " + plan["ticker"])
        elif already:
            print(plan["ticker"] + " skip done")

    take_n = 0 if batch is None else batch.get("exception_take", 0)
    chosen = due[:take_n]
    held = due[take_n:]
    settled_exception = not due
    if due and not chosen:
        print("DEFERRED exception")
        settled_exception = False
    elif chosen:
        stopped = _guard_stop()
        if stopped is not None:
            return stopped
        placed_n = 0
        settled_exception = True
        for exc in chosen:
            current = _fresh_exception_open(actions, opens, exc, working)
            if current is None:
                if placed_n:
                    _save_progress()
                if exc["id"] == _EXCEPTION_ID:
                    return _abort_unexpected("inquiry")
                return _abort_unexpected("inquiry", exc["ticker"])
            if current:
                continue
            left = _exception_remaining(working, exc)
            if left < 1:
                continue
            if counter[0] >= _MAX_NEW:
                print("ERROR max_orders")
                if rejected:
                    _save_progress()
                    print("ERROR reject")
                return 1
            result = _call_place_exception(actions, counter, left, exc)
            if result is None:
                if _log_reject():
                    # One standing order was rejected. Log it and keep
                    # going. Other tickers, and a later pass, still run.
                    print("exception skip %s" % (exc["id"],))
                    continue
                if placed_n:
                    _save_progress()
                return _abort_unexpected("place", exc["ticker"])
            elif result:
                sent_any = True
                placed_n += 1
                _mark_exception_resting(working, slot, today, now_kst, exc)
                number = _order_number(result)
                if number is not None:
                    _add_exception_order_no(working, exc, number)
            else:
                settled_exception = False
        if held:
            print("DEFERRED exception")
            settled_exception = False
        if placed_n:
            print("exception placed %s" % (placed_n,))

    if (
        live
        and mode == "reset"
        and slot
        and settled_exception
        and _all_reset_done(working, plans, allow, slot, today)
    ):
        working["reset_done"][slot] = today
    if rejected:
        return _finish_reject()
    if report_only:
        print("OK report_only")
    else:
        if live:
            actions["save_state"](_clean_state(working))
        if sent_any:
            print("OK live")
        else:
            print("OK dry")
    if live and mode == "reset" and slot and batch is not None:
        pending = bool(batch["deferred"]) or not settled_exception
        if pending and _followup_allowed(now_kst):
            _wants_reset_followup = True
            print("FOLLOWUP reset")
        elif pending:
            print("follow-up stopped time guard")
    return 0


_last_http = ""


def _http_date(body):
    raw = body.get("orr_dt") if isinstance(body, dict) else ""
    if isinstance(raw, str) and len(raw) == 8 and raw.isdigit():
        return "date " + raw
    return ""


def _endpoint_name(kind):
    return {
        "token": "/oauth2/token",
        "acctinfo": "/n2/acctinfo",
        "unexecuted": "/gbstock/inquiry/v1/unexecuted",
    }.get(kind, "http")


def _note_http(kind, status, payload, detail=""):
    """Remember one broker failure. The line has no token, key, or account."""
    global _last_http
    rsp_cd, rsp_msg = gb._rsp(payload if isinstance(payload, dict) else {})
    status_text = "none" if status is None else str(status)
    extra = (" " + detail) if detail else ""
    _last_http = "ERROR http %s%s status %s rsp_cd %s rsp_msg %s" % (
        _endpoint_name(kind),
        extra,
        status_text,
        rsp_cd or "none",
        rsp_msg or "none",
    )


def _die_http():
    print(_last_http or "ERROR http")
    return 1


def _main_once():
    mode = os.environ.get("LADDER_MODE", "")
    event = os.environ.get("GITHUB_EVENT_NAME", "push")
    now_kst = _now_kst()
    now_et = datetime.now(ET)
    if mode == "cancel-dup":
        if _parse_cancel_dup() is None:
            print("cancel-dup skip parse")
            return 0, False
        if event not in ("schedule", "workflow_dispatch", "issue_comment"):
            print("OK dry")
            return 0, False
    elif mode == "soxl-rebuild":
        if _order_type_override() is None:
            print("ERROR config")
            return 1, False
    elif mode not in ("reset", "intraday", "adjust") or _order_type_override() is None:
        print("ERROR config")
        return 1, False
    adjust = _parse_adjust() if mode == "adjust" else None
    if mode == "adjust" and adjust is None:
        print("ERROR config")
        return 1, False
    rebuild_dry = mode == "soxl-rebuild" and not _rebuild_live()
    if _orders_blocked(now_kst) and not (adjust and adjust["preview"]) and not rebuild_dry:
        print("skip: time guard")
        return 0, False
    if mode == "reset":
        # A comment that is not exactly reset / reset day / reset premarket,
        # or that names the other slot, stops before any broker call.
        blocked = _reset_comment_block(now_kst)
        if blocked:
            print(blocked)
            return 0, False
    if _clocked(event) and mode == "reset":
        slot = _reset_slot(now_kst)
        if slot is None:
            print("OK skip")
            return 0, False
        loaded = _load_state()
        done = (loaded or {}).get("reset_done") or {}
        if _live_flag() and done.get(slot) == now_kst.strftime("%Y%m%d"):
            print("OK skip")
            return 0, False
    elif _clocked(event) and adjust and adjust["preview"]:
        pass
    elif _clocked(event) and mode == "soxl-rebuild" and not _rebuild_live():
        pass
    elif _clocked(event) and not _window_open(mode, now_kst, now_et):
        print("OK skip")
        return 0, False
    key = os.environ.get("NHPLUG_APP_KEY", "")
    secret = os.environ.get("NHPLUG_APP_SECRET", "")
    if not key.strip() or not secret.strip() or (
        mode != "cancel-dup" and _mult() is None
    ):
        print("ERROR config")
        return 1, False
    gb._key = key
    gb._secret = secret
    gb._secrets.extend((key, secret))
    gb._issue_token = _quiet_issue
    gb._emit = lambda name, status, payload: gb._rsp(payload)
    token = _load_token() or _quiet_issue(key, secret)
    if not token:
        return _die_http(), False
    gb._token = token
    gb._secrets.append(token)

    status, _headers, payload = gb._post_bearer(gb.ACCTINFO_URL, gb._json_body({}), {})
    if status != 200:
        _note_http("acctinfo", status, payload)
        return _die_http(), False
    account = gb._select_account(payload)
    if not account:
        # A requested suffix that is not in acctinfo must not fall through
        # to another account. Unset suffix keeps the historical stop line.
        if gb._account_suffix():
            print("ERROR account not found")
            return 1, False
        print(_unexpected_line("select"))
        return 1, False
    if account not in gb._secrets:
        gb._secrets.append(account)

    opens = _inquiry(account, now_kst, "open")
    fills = _inquiry(account, now_kst, "fill")
    if opens is None or fills is None:
        return _die_http(), False

    state = _load_state()
    if mode == "cancel-dup":
        live = (
            _live_flag()
            and event in ("schedule", "workflow_dispatch", "issue_comment")
            and _window_open("cancel-dup", now_kst, now_et)
            and not _orders_blocked(now_kst)
            and not _orders_blocked(_now_kst())
        )
    elif mode == "soxl-rebuild":
        # SOXL_REBUILD_LIVE is the switch. LIVE_TRADING must not gate the
        # cancel and place calls, or a live rebuild plans and then stops.
        live = (
            _rebuild_live()
            and event in ("schedule", "workflow_dispatch")
            and _window_open("soxl-rebuild", now_kst, now_et)
            and not _orders_blocked(now_kst)
            and not _orders_blocked(_now_kst())
        )
    else:
        live = _may_trade(event, mode, now_kst, now_et, state is None)

    def place(ticker, item, count):
        result = _place(
            account, ticker, item["side"], item["price"], item["qty"], live, count
        )
        if result is True and isinstance(_last_order_no, int):
            return {"ok": True, "orr_no": _last_order_no}
        return result

    def cancel(ticker, order):
        return _cancel(account, ticker, order, live)

    def exists(ticker, side, index):
        fresh = _inquiry(account, now_kst, "open")
        if fresh is None:
            return None
        for order in fresh:
            if _exception_order(order, state):
                continue
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
            return None
        for order in fresh:
            if order.get("ticker") != ticker or order.get("idx") is None:
                continue
            if _exception_order(order, state):
                continue
            return False
        return True

    def exception_open():
        fresh = _inquiry(account, now_kst, "open")
        if fresh is None:
            return None
        return _exception_open(fresh)

    def exception_open_for(exc_id):
        fresh = _inquiry(account, now_kst, "open")
        if fresh is None:
            return None
        exc = _EXCEPTION_BY_ID.get(exc_id)
        if exc is None:
            return False
        return _exception_open(fresh, exc, state)

    def place_exception(count, qty=None, exc=None):
        if not live:
            return False
        result = _place_exception(account, True, count, qty, exc)
        if result is True and isinstance(_last_order_no, int):
            return {"ok": True, "orr_no": _last_order_no}
        return result

    def fresh_opens():
        return _inquiry(account, now_kst, "open")

    def save_state(data):
        if state is not None and not live:
            return
        if state is None and mode == "cancel-dup":
            return
        _save_json(_STATE_PATH, _stamp_state(data))

    actions = {
        "place": place,
        "cancel": cancel,
        "exists": exists,
        "confirm_clear": confirm_clear,
        "exception_open": exception_open,
        "exception_open_for": exception_open_for,
        "place_exception": place_exception,
        "fresh_opens": fresh_opens,
        "save_state": save_state,
    }
    code = run(mode, event, now_kst, now_et, opens, fills, state, actions)
    return code, _wants_reset_followup


def main():
    """One reset, then at most a few in-process retries when tickers were deferred."""
    return _bounded_reset_followups(_main_once, time.sleep, _now_kst)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(_unexpected_type(exc))
        sys.exit(1)
