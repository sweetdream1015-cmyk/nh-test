"""Session order types across DST, and broker-reject logging. No live calls."""

import io
import json
import os
import sys
from datetime import datetime, timezone

import gb_readonly_check as gb
import ladder_bot as bot
import ladder_dryrun as ladders

_ORIG_NOW = bot._now_kst
_ORIG_BLOCKED = bot._orders_blocked
_ORIG_POST = gb._post_bearer


def _kst(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=bot.KST)


def _et_of(moment):
    return moment.astimezone(bot.ET)


def _idx(ticker, price):
    return ladders._level(ladders._CFG[ticker]["ladder"], price)


def _capture(fn):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        result = fn()
    finally:
        sys.stdout = old
    return result, buf.getvalue()


def test_session_table():
    cases = [
        # The failed run: 05:16 KST is 16:16 EDT, after the cash close.
        (_kst(2026, 10, 30, 5, 16), "intraday", "62"),
        (_kst(2026, 10, 30, 5, 16), "reset", "62"),
        (_kst(2026, 10, 30, 5, 0), "intraday", "62"),
        (_kst(2026, 10, 30, 7, 59), "intraday", "62"),
        # Daylight time: 17:10 KST is 04:10 EDT, premarket is already open.
        (_kst(2026, 10, 29, 17, 10), "reset", "61"),
        (_kst(2026, 10, 29, 17, 15), "intraday", "61"),
        (_kst(2026, 10, 29, 22, 29), "intraday", "61"),
        # 09:30 ET cash open. No separate regular-session code in the repo.
        (_kst(2026, 10, 29, 22, 30), "intraday", "61"),
        (_kst(2026, 10, 30, 4, 59), "intraday", "61"),
        # 09:10 KST daylight time is 20:10 ET, after-hours already over.
        (_kst(2026, 10, 29, 9, 10), "reset", "63"),
        (_kst(2026, 10, 29, 9, 10), "intraday", "63"),
        (_kst(2026, 10, 29, 12, 0), "intraday", "63"),
        (_kst(2026, 10, 29, 15, 59), "intraday", "63"),
        (_kst(2026, 10, 29, 16, 59), "intraday", "63"),
        (_kst(2026, 10, 29, 9, 5), "intraday", None),
        (_kst(2026, 10, 29, 16, 59), "reset", "63"),
        # Standard time starts Sun Nov 1. Monday 17:10 KST is 03:10 EST.
        (_kst(2026, 11, 2, 17, 10), "reset", None),
        (_kst(2026, 11, 2, 17, 59), "reset", None),
        (_kst(2026, 11, 2, 18, 0), "reset", "61"),
        (_kst(2026, 11, 2, 23, 29), "intraday", "61"),
        (_kst(2026, 11, 2, 23, 30), "intraday", "61"),
        # Monday 09:10 KST is still Sunday evening in New York. Not after-hours.
        (_kst(2026, 11, 2, 9, 10), "reset", "63"),
        # Tuesday 06:16 KST is Monday 16:16 EST.
        (_kst(2026, 11, 3, 6, 16), "intraday", "62"),
        (_kst(2026, 11, 3, 5, 16), "intraday", "61"),
        # 09:10 KST standard time overlaps after-hours. Reset waits. Intraday uses 62.
        (_kst(2026, 11, 3, 9, 10), "reset", None),
        (_kst(2026, 11, 3, 9, 10), "intraday", "62"),
        (_kst(2026, 11, 3, 9, 59), "intraday", "62"),
        (_kst(2026, 11, 3, 10, 0), "reset", "63"),
        (_kst(2026, 11, 3, 10, 0), "intraday", "63"),
        (_kst(2026, 11, 3, 9, 30), "intraday", "62"),
        (_kst(2026, 11, 3, 15, 0), "intraday", "63"),
        (_kst(2026, 11, 2, 17, 30), "intraday", None),
    ]
    for moment, mode, expected in cases:
        got = bot._order_type(moment, _et_of(moment), mode)
        assert got == expected, (moment.isoformat(), mode, got, expected)
    assert bot._session_skip(_kst(2026, 11, 2, 17, 10), _et_of(_kst(2026, 11, 2, 17, 10)), "reset") == (
        "skip: premarket not open"
    )
    assert bot._session_skip(_kst(2026, 11, 3, 9, 10), _et_of(_kst(2026, 11, 3, 9, 10)), "reset") == (
        "skip: day session not open"
    )
    early = _kst(2026, 10, 29, 9, 5)
    assert bot._session_skip(early, _et_of(early), "intraday") == "skip: session closed"
    between = _kst(2026, 11, 2, 17, 30)
    assert bot._session_skip(between, _et_of(between), "intraday") == "skip: session closed"


def test_order_type_override():
    os.environ["ORDER_TYPE"] = "62"
    try:
        assert bot._order_type(_kst(2026, 10, 29, 17, 10), mode="reset") == "62"
    finally:
        os.environ.pop("ORDER_TYPE", None)
    os.environ["ORDER_TYPE"] = "99"
    try:
        assert bot._order_type(_kst(2026, 10, 29, 17, 10)) is None
    finally:
        os.environ.pop("ORDER_TYPE", None)


def _arm(moment):
    bot._now_kst = lambda: moment


def _run(mode, moment, state, opens, fills, place):
    world = {"saved": None, "placed": []}

    def save_state(data):
        world["saved"] = json.loads(json.dumps(data))

    def cancel(_ticker, _order):
        return True

    def exists(_ticker, _side, _index):
        return False

    def confirm_clear(_ticker):
        return True

    def exception_open():
        return False

    def place_exception(_counter):
        return False

    actions = {
        "place": place,
        "cancel": cancel,
        "exists": exists,
        "confirm_clear": confirm_clear,
        "exception_open": exception_open,
        "place_exception": place_exception,
        "save_state": save_state,
    }
    _arm(moment)
    code, out = _capture(
        lambda: bot.run(
            mode,
            "workflow_dispatch",
            moment,
            _et_of(moment),
            opens,
            fills,
            state,
            actions,
        )
    )
    return code, out, world


def test_premarket_wait_and_retry():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL"
    os.environ.pop("ORDER_TYPE", None)
    state = bot._fresh_state()
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [],
    }
    state = bot._clean_state(state)
    early = _kst(2026, 11, 2, 17, 10)
    calls = {"n": 0}

    def place(_ticker, _item, _counter):
        calls["n"] += 1
        return True

    code, out, world = _run("reset", early, state, [], [], place)
    assert code == 0, out
    assert calls["n"] == 0
    assert "skip: premarket not open" in out
    assert "order type" not in out
    assert world["saved"] is None
    opened = _kst(2026, 11, 2, 18, 0)
    code, out, world = _run("reset", opened, state, [], [], place)
    assert code == 0, out
    assert "order type 61" in out
    assert calls["n"] == 4
    assert world["saved"]["reset_tickers"]["17"]["NAIL"] == "20261102"


def test_reject_redaction_and_intraday_state():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,SOXL"
    os.environ["LADDER_MODE"] = "intraday"
    os.environ.pop("ORDER_TYPE", None)
    secret = "supersecret-token-value"
    account = "00111222333444"
    gb._secrets.append(secret)
    bot._orders_blocked = lambda _now: False

    def fake_post(_url, _body, _extra):
        return (
            400,
            {},
            {
                "rsp_cd": "IGW00400",
                "rsp_msg": "rejected " + secret + " act " + account,
                "access_token": secret,
            },
        )

    gb._post_bearer = fake_post
    body = {
        "act_no": account,
        "iem_cd": "NAIL",
        "fc_orr_uit_pr": 39.0,
        "orr_qty": 10,
        "ahi_nmn_pr_tp_cd": "62",
    }
    result = bot._post_order(bot.SELL_URL, body)
    assert result is None
    info = bot._last_reject
    assert info["ticker"] == "NAIL"
    assert info["side"] == "sell"
    assert info["price"] == 39.0
    assert info["http"] == 400
    assert info["rsp_cd"] == "IGW00400"
    assert secret not in info["rsp_msg"]
    assert account not in info["rsp_msg"]
    assert "rejected" in info["rsp_msg"]
    _code, logged = _capture(bot._log_reject)
    assert "REJECT NAIL sell 39.0 http 400 rsp_cd IGW00400" in logged
    assert secret not in logged
    assert account not in logged

    moment = _kst(2026, 10, 30, 5, 16)
    nail_key = "11" * 32
    soxl_key = "22" * 32
    state = bot._fresh_state()
    state["exception_filled"] = True
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [],
    }
    state["tickers"]["SOXL"] = {
        "sell": _idx("SOXL", 113),
        "buy": _idx("SOXL", 94),
        "double_used": [],
    }
    state = bot._clean_state(state)
    opens = [
        {"ticker": "NAIL", "side": "sell", "price": 36, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 26, "qty": 10},
        {"ticker": "SOXL", "side": "sell", "price": 113, "qty": 1},
        {"ticker": "SOXL", "side": "sell", "price": 125, "qty": 1},
        {"ticker": "SOXL", "side": "buy", "price": 86, "qty": 1},
    ]
    fills = [
        {
            "ticker": "NAIL",
            "side": "sell",
            "idx": _idx("NAIL", 33),
            "key": nail_key,
            "filled_qty": 10,
            "cancelled_qty": 0,
        },
        {
            "ticker": "SOXL",
            "side": "buy",
            "idx": _idx("SOXL", 94),
            "key": soxl_key,
            "filled_qty": 1,
            "cancelled_qty": 0,
        },
    ]

    def place(ticker, item, _counter):
        if ticker == "SOXL":
            bot._last_reject = {
                "ticker": "SOXL",
                "side": item["side"],
                "price": item["price"],
                "http": 400,
                "rsp_cd": "E62",
                "rsp_msg": "after-hours type required",
            }
            return None
        return True

    code, out, world = _run("intraday", moment, state, opens, fills, place)
    assert code == 1, out
    assert "order type 62" in out
    assert "REJECT SOXL sell 103 http 400 rsp_cd E62" in out
    assert "ERROR reject" in out
    assert "ERROR unexpected" not in out
    saved = world["saved"]
    assert saved["tickers"]["NAIL"]["sell"] == _idx("NAIL", 36)
    assert saved["tickers"]["NAIL"]["buy"] == _idx("NAIL", 30)
    assert nail_key in saved["handled"]
    assert saved["tickers"]["SOXL"]["sell"] == _idx("SOXL", 113)
    assert saved["tickers"]["SOXL"]["buy"] == _idx("SOXL", 94)
    assert soxl_key not in saved["handled"]

    # The rejected ticker did not move. A later run must not duplicate the
    # NAIL orders that already landed, and must still be able to place SOXL.
    booked = list(opens) + [
        {"ticker": "NAIL", "side": "sell", "price": 39, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 30, "qty": 10},
    ]
    again = {"n": 0}

    def place_again(ticker, item, _counter):
        again["n"] += 1
        assert ticker == "SOXL"
        assert (item["side"], item["price"]) in (("sell", 103), ("buy", 79))
        return True

    code, out, world = _run("intraday", moment, saved, booked, fills, place_again)
    assert code == 0, out
    assert again["n"] == 2
    assert world["saved"]["tickers"]["SOXL"]["sell"] == _idx("SOXL", 103)
    assert soxl_key in world["saved"]["handled"]
    assert nail_key in world["saved"]["handled"]


def test_partial_leg_does_not_advance():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL"
    os.environ.pop("ORDER_TYPE", None)
    moment = _kst(2026, 10, 30, 5, 16)
    nail_key = "33" * 32
    state = bot._fresh_state()
    state["exception_filled"] = True
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [],
    }
    state = bot._clean_state(state)
    opens = [
        {"ticker": "NAIL", "side": "sell", "price": 36, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 26, "qty": 10},
    ]
    fills = [
        {
            "ticker": "NAIL",
            "side": "sell",
            "idx": _idx("NAIL", 33),
            "key": nail_key,
            "filled_qty": 10,
            "cancelled_qty": 0,
        }
    ]
    seen = []

    def place(_ticker, item, _counter):
        seen.append((item["side"], item["price"]))
        if len(seen) == 1:
            return True
        bot._last_reject = {
            "ticker": "NAIL",
            "side": item["side"],
            "price": item["price"],
            "http": 400,
            "rsp_cd": "E62",
            "rsp_msg": "session mismatch",
        }
        return None

    code, out, world = _run("intraday", moment, state, opens, fills, place)
    assert code == 1, out
    assert seen == [("sell", 39), ("buy", 30)]
    assert "REJECT NAIL buy 30" in out
    assert world["saved"]["tickers"]["NAIL"]["sell"] == _idx("NAIL", 33)
    assert world["saved"]["tickers"]["NAIL"]["buy"] == _idx("NAIL", 28)
    assert nail_key not in world["saved"]["handled"]


def test_crons_kept():
    root = os.path.dirname(os.path.abspath(__file__))
    text = open(
        os.path.join(root, ".github/workflows/ladder-reset.yml"), encoding="utf-8"
    ).read()
    assert '- cron: "10 0 * * 1-5"' in text
    assert '- cron: "10 8 * * 1-5"' in text
    assert '- cron: "0 9 * * 1-5"' in text
    assert '- cron: "0 1 * * 1-5"' in text


def _restore_guards():
    bot._orders_blocked = _ORIG_BLOCKED
    bot._now_kst = _ORIG_NOW
    gb._post_bearer = _ORIG_POST
    os.environ.pop("ORDER_TYPE", None)


def _run_tracking(moment, state, opens, fills, place_fn=None):
    world = {"saved": None, "placed": [], "cancelled": [], "exceptions": []}

    def save_state(data):
        world["saved"] = json.loads(json.dumps(data))

    def cancel(ticker, order):
        world["cancelled"].append((ticker, order.get("side"), order.get("price")))
        return True

    def exists(_ticker, _side, _index):
        return False

    def confirm_clear(_ticker):
        return True

    def exception_open():
        return False

    def place_exception(_counter):
        world["exceptions"].append((100, 75))
        return True

    def place(ticker, item, counter):
        if place_fn is None:
            result = True
        else:
            result = place_fn(ticker, item, counter)
        if result:
            world["placed"].append((ticker, item["side"], item["price"], item["qty"]))
        return result

    actions = {
        "place": place,
        "cancel": cancel,
        "exists": exists,
        "confirm_clear": confirm_clear,
        "exception_open": exception_open,
        "place_exception": place_exception,
        "save_state": save_state,
    }
    _arm(moment)
    code, out = _capture(
        lambda: bot.run(
            "intraday",
            "workflow_dispatch",
            moment,
            _et_of(moment),
            opens,
            fills,
            state,
            actions,
        )
    )
    return code, out, world


def _fill(ticker, side, price, key, qty):
    return {
        "ticker": ticker,
        "side": side,
        "idx": _idx(ticker, price),
        "key": key,
        "filled_qty": qty,
        "cancelled_qty": 0,
    }


def test_day_session_window():
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL"
    noon = _kst(2026, 10, 29, 12, 0)
    last = _kst(2026, 10, 29, 15, 59)
    late = _kst(2026, 10, 29, 16, 30)
    morning_guard = _kst(2026, 10, 29, 8, 30)
    wipe = _kst(2026, 10, 29, 9, 5)
    weekend = _kst(2026, 10, 31, 12, 0)
    est_overlap = _kst(2026, 11, 3, 9, 30)
    est_day = _kst(2026, 11, 3, 10, 0)
    est_gap = _kst(2026, 11, 2, 17, 30)
    est_us = _kst(2026, 11, 2, 18, 0)
    cases = [
        (noon, True, False),
        (last, True, False),
        (late, True, True),
        (morning_guard, True, True),
        (wipe, False, True),
        (weekend, False, True),
        (est_overlap, True, False),
        (est_day, True, False),
        (est_gap, False, False),
        (est_us, True, False),
    ]
    for moment, open_, blocked in cases:
        got_open = bot._window_open("intraday", moment, _et_of(moment))
        got_blocked = bot._orders_blocked(moment)
        assert got_open is open_, (moment.isoformat(), got_open, open_)
        assert got_blocked is blocked, (moment.isoformat(), got_blocked, blocked)
    assert bot._window_open("reset", noon, _et_of(noon))
    assert bot._window_open("reset", est_gap, _et_of(est_gap))
    assert not bot._window_open("intraday", est_gap, _et_of(est_gap))
    assert bot._kst_day(late)
    assert bot._order_type(late, _et_of(late), "intraday") == "63"

    state = bot._fresh_state()
    state["exception_filled"] = True
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [],
    }
    state = bot._clean_state(state)
    for moment, text in (
        (morning_guard, "skip: time guard"),
        (wipe, "skip: time guard"),
        (late, "skip: time guard"),
        (weekend, "skip: time guard"),
    ):
        code, out, world = _run_tracking(moment, state, [], [], None)
        assert code == 0, out
        assert text in out
        assert world["placed"] == []
        assert world["cancelled"] == []
        assert world["saved"] is None
    code, out, world = _run_tracking(est_gap, state, [], [], None)
    assert code == 0, out
    assert "OK skip" in out
    assert "order type" not in out
    assert world["placed"] == []
    assert world["saved"] is None


def _day_book():
    nail_key = "a1" * 32
    koru_key = "b2" * 32
    soxl_key = "c3" * 32
    state = bot._fresh_state()
    state["exception_filled"] = False
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [],
    }
    state["tickers"]["KORU"] = {
        "sell": _idx("KORU", 12.5),
        "buy": _idx("KORU", 11.3),
        "double_used": [],
    }
    state["tickers"]["SOXL"] = {
        "sell": _idx("SOXL", 94),
        "buy": _idx("SOXL", 86),
        "double_used": [],
    }
    opens = [
        {"ticker": "NAIL", "side": "sell", "price": 36, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 26, "qty": 10},
        {"ticker": "KORU", "side": "sell", "price": 12.5, "qty": 1},
        {"ticker": "KORU", "side": "sell", "price": 13.5, "qty": 1},
        {"ticker": "KORU", "side": "buy", "price": 10.3, "qty": 1},
        {"ticker": "SOXL", "side": "sell", "price": 94, "qty": 1},
        {"ticker": "SOXL", "side": "sell", "price": 103, "qty": 1},
        {"ticker": "SOXL", "side": "buy", "price": 79, "qty": 1},
        {"ticker": "SOXL", "side": "buy", "price": 75, "qty": 100},
    ]
    fills = [
        _fill("NAIL", "sell", 33, nail_key, 10),
        _fill("KORU", "buy", 11.3, koru_key, 1),
        _fill("SOXL", "buy", 86, soxl_key, 1),
    ]
    return bot._clean_state(state), opens, fills, (nail_key, koru_key, soxl_key)


def test_day_session_fill_followup():
    """Sell steps up, buy steps down, nothing is cancelled, cap stays 6."""
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    expected = [
        ("NAIL", "sell", 39, 10),
        ("NAIL", "buy", 30, 10),
        ("KORU", "sell", 11.3, 1),
        ("KORU", "buy", 9.4, 2),
        ("SOXL", "sell", 86, 1),
        ("SOXL", "buy", 72, 4),
    ]
    moments = (
        (_kst(2026, 10, 29, 12, 0), "63"),
        (_kst(2026, 10, 29, 15, 59), "63"),
        (_kst(2026, 11, 3, 10, 0), "63"),
        (_kst(2026, 11, 3, 9, 30), "62"),
    )
    for moment, kind in moments:
        state, opens, fills, keys = _day_book()
        code, out, world = _run_tracking(moment, state, opens, fills)
        assert code == 0, out
        assert "order type " + kind in out
        assert world["placed"] == expected, (moment.isoformat(), world["placed"], out)
        assert world["cancelled"] == []
        assert world["exceptions"] == []
        assert "exception placed" not in out
        assert "ERROR" not in out
        assert "NEEDS_REVIEW" not in out
        saved = world["saved"]
        assert saved["tickers"]["NAIL"]["sell"] == _idx("NAIL", 36)
        assert saved["tickers"]["NAIL"]["buy"] == _idx("NAIL", 30)
        assert saved["tickers"]["KORU"]["sell"] == _idx("KORU", 11.3)
        assert saved["tickers"]["KORU"]["buy"] == _idx("KORU", 10.3)
        assert saved["tickers"]["SOXL"]["sell"] == _idx("SOXL", 86)
        assert saved["tickers"]["SOXL"]["buy"] == _idx("SOXL", 79)
        for key in keys:
            assert key in saved["handled"]
        for _ticker, _side, _price, qty in world["placed"]:
            assert 1 <= qty <= bot._HARD_MAX[_ticker]
        booked = list(opens) + [
            {"ticker": ticker, "side": side, "price": price, "qty": qty}
            for ticker, side, price, qty in expected
        ]
        code, out, again = _run_tracking(moment, saved, booked, fills)
        assert code == 0, out
        assert again["placed"] == []
        assert again["cancelled"] == []
        assert again["exceptions"] == []
        assert again["saved"]["tickers"]["NAIL"]["sell"] == _idx("NAIL", 36)
        assert "OK dry" in out

    state, opens, fills, _keys = _day_book()
    bare = [order for order in opens if order.get("price") != 75]
    code, out, world = _run_tracking(_kst(2026, 10, 29, 12, 0), state, bare, fills)
    assert code == 1, out
    assert "ERROR max_orders" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert world["exceptions"] == []
    assert world["saved"] is None


def test_day_session_quantities():
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    moment = _kst(2026, 10, 29, 12, 0)

    def _one(ticker, sell_px, buy_px, double_used, opens, fill_side, fill_px, fill_qty):
        os.environ["LIVE_TICKERS"] = ticker
        state = bot._fresh_state()
        state["exception_filled"] = True
        state["tickers"][ticker] = {
            "sell": _idx(ticker, sell_px),
            "buy": _idx(ticker, buy_px),
            "double_used": [_idx(ticker, price) for price in double_used],
        }
        key = "d4" * 32
        code, out, world = _run_tracking(
            moment,
            bot._clean_state(state),
            opens,
            [_fill(ticker, fill_side, fill_px, key, fill_qty)],
        )
        assert code == 0, out
        assert "order type 63" in out
        assert world["cancelled"] == []
        assert world["exceptions"] == []
        assert key in world["saved"]["handled"]
        return world

    # First buy at or below the low level doubles. 20 is not open yet.
    doubled = _one(
        "NAIL",
        26,
        24,
        [],
        [
            {"ticker": "NAIL", "side": "sell", "price": 26, "qty": 10},
            {"ticker": "NAIL", "side": "sell", "price": 28, "qty": 10},
            {"ticker": "NAIL", "side": "buy", "price": 22, "qty": 20},
        ],
        "buy",
        24,
        10,
    )
    assert doubled["placed"] == [("NAIL", "sell", 24, 10), ("NAIL", "buy", 20, 20)]

    # A level already bought reverts to the base quantity.
    reverted = _one(
        "NAIL",
        30,
        26,
        [22],
        [
            {"ticker": "NAIL", "side": "sell", "price": 30, "qty": 10},
            {"ticker": "NAIL", "side": "sell", "price": 33, "qty": 10},
            {"ticker": "NAIL", "side": "buy", "price": 24, "qty": 10},
        ],
        "buy",
        26,
        10,
    )
    assert reverted["placed"] == [("NAIL", "sell", 28, 10), ("NAIL", "buy", 22, 10)]
    assert _idx("NAIL", 22) in reverted["saved"]["tickers"]["NAIL"]["double_used"]

    koru = _one(
        "KORU",
        12.5,
        11.3,
        [],
        [
            {"ticker": "KORU", "side": "sell", "price": 12.5, "qty": 1},
            {"ticker": "KORU", "side": "sell", "price": 13.5, "qty": 1},
            {"ticker": "KORU", "side": "buy", "price": 10.3, "qty": 1},
        ],
        "buy",
        11.3,
        1,
    )
    assert koru["placed"] == [("KORU", "sell", 11.3, 1), ("KORU", "buy", 9.4, 2)]

    soxl_at = _one(
        "SOXL",
        94,
        86,
        [],
        [
            {"ticker": "SOXL", "side": "sell", "price": 94, "qty": 1},
            {"ticker": "SOXL", "side": "sell", "price": 103, "qty": 1},
            {"ticker": "SOXL", "side": "buy", "price": 79, "qty": 1},
        ],
        "buy",
        86,
        1,
    )
    assert soxl_at["placed"] == [("SOXL", "sell", 86, 1), ("SOXL", "buy", 72, 4)]

    soxl_below = _one(
        "SOXL",
        86,
        79,
        [],
        [
            {"ticker": "SOXL", "side": "sell", "price": 86, "qty": 1},
            {"ticker": "SOXL", "side": "sell", "price": 94, "qty": 1},
            {"ticker": "SOXL", "side": "buy", "price": 72, "qty": 4},
        ],
        "buy",
        79,
        1,
    )
    assert soxl_below["placed"] == [("SOXL", "sell", 79, 1), ("SOXL", "buy", 66, 2)]


def test_day_session_partial_does_not_advance():
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL"
    moment = _kst(2026, 10, 29, 12, 0)
    nail_key = "e5" * 32
    state = bot._fresh_state()
    state["exception_filled"] = True
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [],
    }
    state = bot._clean_state(state)
    opens = [
        {"ticker": "NAIL", "side": "sell", "price": 36, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 26, "qty": 10},
    ]
    fills = [_fill("NAIL", "sell", 33, nail_key, 10)]
    seen = []

    def place(_ticker, item, _counter):
        seen.append((item["side"], item["price"]))
        if len(seen) == 1:
            return True
        bot._last_reject = {
            "ticker": "NAIL",
            "side": item["side"],
            "price": item["price"],
            "http": 400,
            "rsp_cd": "E63",
            "rsp_msg": "day session type required",
        }
        return None

    code, out, world = _run_tracking(moment, state, opens, fills, place)
    assert code == 1, out
    assert "order type 63" in out
    assert seen == [("sell", 39), ("buy", 30)]
    assert "REJECT NAIL buy 30" in out
    assert world["cancelled"] == []
    assert world["saved"]["tickers"]["NAIL"]["sell"] == _idx("NAIL", 33)
    assert world["saved"]["tickers"]["NAIL"]["buy"] == _idx("NAIL", 28)
    assert nail_key not in world["saved"]["handled"]


def _cron_field(spec, value, low, high):
    for part in spec.split(","):
        step = 1
        base = part
        if "/" in part:
            base, step_text = part.split("/", 1)
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            left, right = base.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(base)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _cron_hits(expr, minute, hour, dow):
    fields = expr.split()
    assert len(fields) == 5, expr
    return (
        _cron_field(fields[0], minute, 0, 59)
        and _cron_field(fields[1], hour, 0, 23)
        and _cron_field(fields[4], dow, 0, 7)
    )


def _workflow_crons(name):
    root = os.path.dirname(os.path.abspath(__file__))
    text = open(os.path.join(root, ".github", "workflows", name), encoding="utf-8").read()
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- cron:"):
            found.append(stripped.split('"')[1])
    return found


def test_day_session_crons():
    crons = _workflow_crons("ladder-intraday.yml")
    assert "*/5 0-6 * * 1-5" in crons
    assert "*/5 8-22 * * 1-5" in crons
    assert "*/5 0 * * 6" in crons
    assert "*/5 * * * 1-5" not in crons
    # Monday/Wednesday/Friday in daylight time and in standard time,
    # plus Saturday (Friday's US session) and Sunday.
    days = (
        datetime(2026, 10, 26, tzinfo=timezone.utc),
        datetime(2026, 10, 28, tzinfo=timezone.utc),
        datetime(2026, 10, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 31, tzinfo=timezone.utc),
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        datetime(2026, 11, 2, tzinfo=timezone.utc),
        datetime(2026, 11, 4, tzinfo=timezone.utc),
        datetime(2026, 11, 6, tzinfo=timezone.utc),
        datetime(2026, 11, 7, tzinfo=timezone.utc),
    )
    for day in days:
        for hour in range(24):
            for minute in range(0, 60, 5):
                moment = day.replace(hour=hour, minute=minute)
                hits = sum(_cron_hits(expr, minute, hour, day.isoweekday()) for expr in crons)
                assert hits <= 1, (moment.isoformat(), hits)
                kst = moment.astimezone(bot.KST)
                tradable = bot._window_open(
                    "intraday", kst, moment.astimezone(bot.ET)
                ) and not bot._orders_blocked(kst)
                if tradable:
                    assert hits == 1, (moment.isoformat(), kst.isoformat())


def test_weekend_guard_follows_us_session():
    _restore_guards()
    # Saturday 31 Oct 2026 is still daylight time. Friday's session ends 09:00 KST.
    sat = _kst(2026, 10, 31, 0, 0)
    sun = _kst(2026, 11, 1, 0, 0)
    mon = _kst(2026, 10, 26, 0, 0)
    for hour, minute in ((0, 30), (3, 0), (7, 30)):
        moment = sat.replace(hour=hour, minute=minute)
        assert not bot._orders_blocked(moment), moment.isoformat()
    assert bot._order_type(sat.replace(hour=0, minute=30), _et_of(sat.replace(hour=0, minute=30)), "intraday") == "61"
    assert bot._order_type(sat.replace(hour=3, minute=0), _et_of(sat.replace(hour=3, minute=0)), "intraday") == "61"
    assert bot._order_type(sat.replace(hour=7, minute=30), _et_of(sat.replace(hour=7, minute=30)), "intraday") == "62"
    for hour, minute in ((9, 0), (9, 15), (12, 0), (17, 15), (23, 0)):
        moment = sat.replace(hour=hour, minute=minute)
        assert bot._orders_blocked(moment), moment.isoformat()
    for hour, minute in ((0, 30), (3, 0), (9, 0), (12, 0), (18, 0), (23, 30)):
        moment = sun.replace(hour=hour, minute=minute)
        assert bot._orders_blocked(moment), moment.isoformat()
    # Monday before the day session is closed. 09:10 and the US evening are not.
    for hour, minute in ((0, 30), (3, 0), (7, 30), (9, 0)):
        moment = mon.replace(hour=hour, minute=minute)
        assert bot._orders_blocked(moment), moment.isoformat()
    assert not bot._orders_blocked(mon.replace(hour=9, minute=10))
    assert not bot._orders_blocked(mon.replace(hour=12, minute=0))
    assert not bot._orders_blocked(mon.replace(hour=17, minute=15))
    # Standard time: Friday after-hours lasts until 10:00 KST Saturday.
    est_sat = _kst(2026, 11, 7, 0, 0)
    assert est_sat.weekday() == 5
    for hour, minute in ((0, 30), (3, 0), (7, 30), (9, 30)):
        moment = est_sat.replace(hour=hour, minute=minute)
        assert not bot._orders_blocked(moment), moment.isoformat()
    assert bot._order_type(est_sat.replace(hour=7, minute=30), _et_of(est_sat.replace(hour=7, minute=30)), "intraday") == "62"
    assert bot._order_type(est_sat.replace(hour=9, minute=30), _et_of(est_sat.replace(hour=9, minute=30)), "intraday") == "62"
    for hour, minute in ((8, 30), (9, 0), (10, 0), (12, 0)):
        moment = est_sat.replace(hour=hour, minute=minute)
        assert bot._orders_blocked(moment), moment.isoformat()
    assert bot._orders_blocked(_kst(2026, 11, 2, 7, 30))
    assert not bot._orders_blocked(_kst(2026, 11, 2, 18, 0))

    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL"
    state = bot._fresh_state()
    state["exception_filled"] = True
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [],
    }
    state = bot._clean_state(state)
    code, out, world = _run_tracking(sat.replace(hour=7, minute=30), state, [], [])
    assert code == 0, out
    assert "skip: time guard" not in out
    assert "order type 62" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    code, out, world = _run_tracking(sun.replace(hour=3, minute=0), state, [], [])
    assert code == 0, out
    assert "skip: time guard" in out
    assert world["placed"] == []
    assert world["saved"] is None
    code, out, world = _run_tracking(sat.replace(hour=9, minute=15), state, [], [])
    assert code == 0, out
    assert "skip: time guard" in out
    assert world["placed"] == []


def test_dates_cover_previous_trading_day():
    _restore_guards()
    monday = _kst(2026, 10, 26, 12, 0)
    assert bot._dates(monday) == ["20261026", "20261025", "20261023"]
    assert bot._dates(_kst(2026, 11, 2, 10, 0)) == ["20261102", "20261101", "20261030"]
    assert bot._dates(_kst(2026, 10, 27, 12, 0)) == ["20261027", "20261026"]
    assert bot._dates(_kst(2026, 10, 31, 3, 0)) == ["20261031", "20261030"]
    assert bot._dates(_kst(2026, 11, 1, 12, 0)) == ["20261101", "20261031", "20261030"]
    assert len(bot._dates(monday)) == len(set(bot._dates(monday)))

    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL"
    nail_key = "f6" * 32
    state = bot._fresh_state()
    state["exception_filled"] = True
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [],
    }
    opens = [
        {"ticker": "NAIL", "side": "sell", "price": 36, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 26, "qty": 10},
    ]
    fills = [_fill("NAIL", "sell", 33, nail_key, 10)]
    fresh = bot._clean_state(state)
    code, out, world = _run_tracking(monday, fresh, opens, fills)
    assert code == 0, out
    assert world["placed"] == [("NAIL", "sell", 39, 10), ("NAIL", "buy", 30, 10)]
    assert nail_key in world["saved"]["handled"]
    assert world["cancelled"] == []

    handled = bot._clean_state(state)
    handled["handled"] = [nail_key]
    code, out, again = _run_tracking(monday, handled, opens, fills)
    assert code == 0, out
    assert again["placed"] == []
    assert again["cancelled"] == []
    assert again["saved"]["tickers"]["NAIL"]["sell"] == _idx("NAIL", 33)
    assert again["saved"]["tickers"]["NAIL"]["buy"] == _idx("NAIL", 28)
    assert again["saved"]["handled"] == [nail_key]


def main():
    try:
        test_session_table()
        test_order_type_override()
        test_premarket_wait_and_retry()
        test_reject_redaction_and_intraday_state()
        test_partial_leg_does_not_advance()
        test_crons_kept()
        test_day_session_window()
        test_day_session_fill_followup()
        test_day_session_quantities()
        test_day_session_partial_does_not_advance()
        test_day_session_crons()
        test_weekend_guard_follows_us_session()
        test_dates_cover_previous_trading_day()
    finally:
        bot._now_kst = _ORIG_NOW
        bot._orders_blocked = _ORIG_BLOCKED
        gb._post_bearer = _ORIG_POST
        os.environ.pop("ORDER_TYPE", None)
        os.environ.pop("LADDER_MODE", None)
    print("OK order type")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        bot._now_kst = _ORIG_NOW
        bot._orders_blocked = _ORIG_BLOCKED
        gb._post_bearer = _ORIG_POST
        raise
