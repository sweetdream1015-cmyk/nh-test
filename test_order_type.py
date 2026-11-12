"""Session order types across DST, and broker-reject logging. No live calls."""

import io
import json
import os
import sys
from datetime import datetime, timezone

import gb_readonly_check as gb
import ladder_bot as bot
import ladder_dryrun as ladders
import restore_ladder_state as restore

_ORIG_NOW = bot._now_kst
_ORIG_BLOCKED = bot._orders_blocked
_ORIG_POST = gb._post_bearer


def _kst(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=bot.KST)


def _et_of(moment):
    return moment.astimezone(bot.ET)


def _idx(ticker, price):
    return ladders._level_of(ticker, price)


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
        # 09:30 ET cash open is 지정가 00, through 15:59. 16:00 is 62.
        (_kst(2026, 10, 29, 22, 30), "intraday", "00"),
        (_kst(2026, 10, 29, 22, 30), "reset", "00"),
        (_kst(2026, 10, 30, 1, 34), "intraday", "00"),
        (_kst(2026, 10, 30, 1, 34), "reset", "00"),
        (_kst(2026, 10, 30, 4, 59), "intraday", "00"),
        (_kst(2026, 10, 30, 4, 59), "reset", "00"),
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
        # 09:30 EST cash open. Reset slot 17 is still open, and uses 00 too.
        (_kst(2026, 11, 2, 23, 30), "intraday", "00"),
        (_kst(2026, 11, 2, 23, 30), "reset", "00"),
        # Failed ladder-intraday runs on 2026-11-03 KST: 10:34, 11:37, 12:32 EST.
        (_kst(2026, 11, 3, 0, 34), "intraday", "00"),
        (_kst(2026, 11, 3, 0, 34), "reset", "00"),
        (_kst(2026, 11, 3, 1, 37), "intraday", "00"),
        (_kst(2026, 11, 3, 2, 32), "intraday", "00"),
        # Monday 09:10 KST is still Sunday evening in New York. Not after-hours.
        (_kst(2026, 11, 2, 9, 10), "reset", "63"),
        # Tuesday 06:16 KST is Monday 16:16 EST.
        (_kst(2026, 11, 3, 6, 0), "intraday", "62"),
        (_kst(2026, 11, 3, 6, 16), "intraday", "62"),
        (_kst(2026, 11, 3, 5, 16), "intraday", "00"),
        (_kst(2026, 11, 3, 5, 59), "intraday", "00"),
        (_kst(2026, 11, 3, 5, 59), "reset", "00"),
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
    os.environ["ORDER_TYPE"] = "00"
    try:
        assert bot._order_type(_kst(2026, 10, 29, 12, 0), mode="intraday") == "00"
        assert bot._order_type(_kst(2026, 11, 3, 0, 34), mode="reset") == "00"
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
        world["exceptions"].append((200, 37.5))
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
        {"ticker": "SOXL", "side": "buy", "price": 37.5, "qty": 200},
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
        ("KORU", "buy", 9.4, 1),
        ("SOXL", "sell", 86, 2),
        ("SOXL", "buy", 72, 2),
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

    # 200@37.5 is a reset order. Intraday still places the six follow-ups
    # when that buy is absent, and does not place the exception itself.
    state, opens, fills, _keys = _day_book()
    bare = [order for order in opens if order.get("price") != 37.5]
    code, out, world = _run_tracking(_kst(2026, 10, 29, 12, 0), state, bare, fills)
    assert code == 0, out
    assert world["placed"] == expected
    assert world["cancelled"] == []
    assert world["exceptions"] == []
    assert "exception placed" not in out
    assert "ERROR" not in out


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

    # First buy at 22 is 2x. The new rung is 22; 24 is already open.
    at_22 = _one(
        "NAIL",
        28,
        26,
        [],
        [
            {"ticker": "NAIL", "side": "sell", "price": 28, "qty": 10},
            {"ticker": "NAIL", "side": "sell", "price": 30, "qty": 10},
            {"ticker": "NAIL", "side": "buy", "price": 24, "qty": 10},
        ],
        "buy",
        26,
        10,
    )
    assert at_22["placed"] == [("NAIL", "sell", 26, 10), ("NAIL", "buy", 22, 20)]

    # Above 22 stays at the base quantity.
    at_24 = _one(
        "NAIL",
        30,
        28,
        [],
        [
            {"ticker": "NAIL", "side": "sell", "price": 30, "qty": 10},
            {"ticker": "NAIL", "side": "sell", "price": 33, "qty": 10},
            {"ticker": "NAIL", "side": "buy", "price": 26, "qty": 10},
        ],
        "buy",
        28,
        10,
    )
    assert at_24["placed"] == [("NAIL", "sell", 28, 10), ("NAIL", "buy", 24, 10)]

    # First buy at 20 or below is 4x. 20 is not open yet.
    at_20 = _one(
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
    assert at_20["placed"] == [("NAIL", "sell", 24, 10), ("NAIL", "buy", 20, 40)]

    at_18 = _one(
        "NAIL",
        24,
        22,
        [],
        [
            {"ticker": "NAIL", "side": "sell", "price": 24, "qty": 10},
            {"ticker": "NAIL", "side": "sell", "price": 26, "qty": 10},
            {"ticker": "NAIL", "side": "buy", "price": 20, "qty": 40},
        ],
        "buy",
        22,
        20,
    )
    assert at_18["placed"] == [("NAIL", "sell", 22, 10), ("NAIL", "buy", 18, 40)]

    # A level already bought reverts to the base quantity.
    used_20 = _one(
        "NAIL",
        26,
        24,
        [20],
        [
            {"ticker": "NAIL", "side": "sell", "price": 26, "qty": 10},
            {"ticker": "NAIL", "side": "sell", "price": 28, "qty": 10},
            {"ticker": "NAIL", "side": "buy", "price": 22, "qty": 20},
        ],
        "buy",
        24,
        10,
    )
    assert used_20["placed"] == [("NAIL", "sell", 24, 10), ("NAIL", "buy", 20, 10)]
    assert _idx("NAIL", 20) in used_20["saved"]["tickers"]["NAIL"]["double_used"]

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
    assert koru["placed"] == [("KORU", "sell", 11.3, 1), ("KORU", "buy", 9.4, 1)]

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
    assert soxl_at["placed"] == [("SOXL", "sell", 86, 2), ("SOXL", "buy", 72, 2)]

    soxl_below = _one(
        "SOXL",
        86,
        79,
        [],
        [
            {"ticker": "SOXL", "side": "sell", "price": 86, "qty": 1},
            {"ticker": "SOXL", "side": "sell", "price": 94, "qty": 1},
            {"ticker": "SOXL", "side": "buy", "price": 72, "qty": 2},
        ],
        "buy",
        79,
        1,
    )
    assert soxl_below["placed"] == [("SOXL", "sell", 79, 2), ("SOXL", "buy", 66, 2)]

    # Above 39 the new buy is the base 2. The new sell is 2.
    soxl_above = _one(
        "SOXL",
        103,
        94,
        [],
        [
            {"ticker": "SOXL", "side": "sell", "price": 103, "qty": 2},
            {"ticker": "SOXL", "side": "sell", "price": 113, "qty": 2},
            {"ticker": "SOXL", "side": "buy", "price": 86, "qty": 1},
        ],
        "buy",
        94,
        1,
    )
    assert soxl_above["placed"] == [("SOXL", "sell", 94, 2), ("SOXL", "buy", 79, 2)]

    # A used mark above 39 does not change the base 2.
    soxl_used = _one(
        "SOXL",
        94,
        86,
        [72],
        [
            {"ticker": "SOXL", "side": "sell", "price": 94, "qty": 2},
            {"ticker": "SOXL", "side": "sell", "price": 103, "qty": 2},
            {"ticker": "SOXL", "side": "buy", "price": 79, "qty": 1},
        ],
        "buy",
        86,
        1,
    )
    assert soxl_used["placed"] == [("SOXL", "sell", 86, 2), ("SOXL", "buy", 72, 2)]

    soxl_used_low = _one(
        "SOXL",
        86,
        79,
        [66],
        [
            {"ticker": "SOXL", "side": "sell", "price": 86, "qty": 2},
            {"ticker": "SOXL", "side": "sell", "price": 94, "qty": 2},
            {"ticker": "SOXL", "side": "buy", "price": 72, "qty": 2},
        ],
        "buy",
        79,
        1,
    )
    assert soxl_used_low["placed"] == [("SOXL", "sell", 79, 2), ("SOXL", "buy", 66, 2)]


def test_koru_buy_fill_steps_below_7_2():
    """6.6 is base 1. Filling down onto 6.0 places the first buy of 2."""
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "KORU"
    moment = _kst(2026, 10, 29, 12, 0)

    def _book(sell_px, buy_px, opens, fill_px):
        state = bot._fresh_state()
        state["exception_filled"] = True
        state["tickers"]["KORU"] = {
            "sell": _idx("KORU", sell_px),
            "buy": _idx("KORU", buy_px),
            "double_used": [],
        }
        key = "e5" * 32
        code, out, world = _run_tracking(
            moment,
            bot._clean_state(state),
            opens,
            [_fill("KORU", "buy", fill_px, key, 2)],
        )
        assert code == 0, out
        assert world["cancelled"] == []
        assert world["exceptions"] == []
        assert "NEEDS_REVIEW KORU" not in out
        assert "ERROR" not in out
        assert key in world["saved"]["handled"]
        return world

    # The 7.9 core buy fills while 7.2 is already open, so intraday places 1@6.6.
    placed = _book(
        8.6,
        7.9,
        [
            {"ticker": "KORU", "side": "sell", "price": 8.6, "qty": 1},
            {"ticker": "KORU", "side": "sell", "price": 9.4, "qty": 1},
            {"ticker": "KORU", "side": "buy", "price": 7.2, "qty": 2},
        ],
        7.9,
    )
    assert placed["placed"] == [("KORU", "sell", 7.9, 1), ("KORU", "buy", 6.6, 1)]
    assert placed["saved"]["tickers"]["KORU"]["sell"] == _idx("KORU", 7.9)
    assert placed["saved"]["tickers"]["KORU"]["buy"] == _idx("KORU", 7.2)

    # Filling the 7.2 core buy steps onto 6.6. The open 6.6 stays put.
    # The saved pair is then 7.9/6.6, whose next plan buys 1 there.
    stepped = _book(
        8.6,
        7.2,
        [
            {"ticker": "KORU", "side": "sell", "price": 8.6, "qty": 1},
            {"ticker": "KORU", "side": "sell", "price": 9.4, "qty": 1},
            {"ticker": "KORU", "side": "buy", "price": 6.6, "qty": 2},
        ],
        7.2,
    )
    assert stepped["placed"] == [("KORU", "sell", 7.9, 1), ("KORU", "buy", 6.0, 2)]
    saved = stepped["saved"]["tickers"]["KORU"]
    assert saved["sell"] == _idx("KORU", 7.9)
    assert saved["buy"] == _idx("KORU", 6.6)
    assert saved["buy"] == -4
    assert _idx("KORU", 7.2) not in saved["double_used"]
    assert _idx("KORU", 6.6) not in saved["double_used"]
    desired = bot._desired(
        "KORU", saved["sell"], saved["buy"], 1.0, saved["double_used"]
    )
    assert [(item["side"], item["price"], item["qty"]) for item in desired] == [
        ("sell", 7.9, 1),
        ("sell", 8.6, 1),
        ("buy", 6.6, 1),
        ("buy", 6.0, 2),
    ]


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
    # Saturday morning KST is still Friday regular hours in New York.
    assert bot._order_type(sat.replace(hour=0, minute=30), _et_of(sat.replace(hour=0, minute=30)), "intraday") == "00"
    assert bot._order_type(sat.replace(hour=3, minute=0), _et_of(sat.replace(hour=3, minute=0)), "intraday") == "00"
    assert bot._order_type(sat.replace(hour=0, minute=30), _et_of(sat.replace(hour=0, minute=30)), "reset") == "00"
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
    assert bot._order_type(est_sat.replace(hour=0, minute=30), _et_of(est_sat.replace(hour=0, minute=30)), "intraday") == "00"
    assert bot._order_type(est_sat.replace(hour=5, minute=30), _et_of(est_sat.replace(hour=5, minute=30)), "intraday") == "00"
    assert bot._order_type(est_sat.replace(hour=5, minute=30), _et_of(est_sat.replace(hour=5, minute=30)), "reset") == "00"
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
    # Monday still includes Friday. The open window is wider than that.
    assert set(["20261026", "20261025", "20261023"]) <= set(bot._dates(monday))
    assert set(["20261102", "20261101", "20261030"]) <= set(bot._dates(_kst(2026, 11, 2, 10, 0)))
    assert set(["20261027", "20261026"]) <= set(bot._dates(_kst(2026, 10, 27, 12, 0)))
    assert set(["20261031", "20261030"]) <= set(bot._dates(_kst(2026, 10, 31, 3, 0)))
    assert set(["20261101", "20261031", "20261030"]) <= set(bot._dates(_kst(2026, 11, 1, 12, 0)))
    assert len(bot._dates(monday)) == len(set(bot._dates(monday)))
    # 05:40 KST Friday is still Thursday in New York. Both dates are queried.
    friday = _kst(2026, 11, 13, 5, 40)
    both = set(bot._dates(friday))
    assert "20261113" in both
    assert "20261112" in both
    fills = set(bot._fill_dates(friday))
    assert fills == {"20261113", "20261112"}

    nail = {
        "iem_cd": "NAIL",
        "fc_sec_trd_nat_cd": "200",
        "oss_sby_dit_cd": "02",
        "fc_orr_uit_pr": "28.0000",
        "orr_qty": "10.0000",
        "ny_cns_orr_qty": "",
        "cns_qty": "0",
        "can_qty": "0",
        "oss_ato_orr_sts_cd": "3",
        "orr_no": "55",
        "orr_dt": "2026-11-13",
    }
    parsed = bot._parse_row(nail)
    assert parsed["side"] == "buy"
    assert parsed["qty"] == 10
    assert abs(parsed["price"] - 28) < 0.001
    assert parsed["orr_dt"] == "20261113"
    assert bot._dup_match(
        parsed, {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 10}
    )
    zero_remaining = dict(nail)
    zero_remaining["ny_cns_orr_qty"] = "0"
    zero_remaining["orr_no"] = "56"
    assert bot._parse_row(zero_remaining)["qty"] == 10
    filled = dict(nail)
    filled["ny_cns_orr_qty"] = "0"
    filled["cns_qty"] = "10"
    filled["orr_no"] = "57"
    assert bot._parse_row(filled)["qty"] == 0
    assert ladders._side({"oss_sby_dit_cd": "2"}) == "buy"
    assert ladders._open_qty({"ny_cns_orr_qty": "10", "orr_qty": "10"}) == 10

    calls = []

    def pages(_url, body, _block):
        calls.append(body["orr_dt"])
        if body["orr_dt"] != "20261113":
            return []
        first = dict(nail)
        second = dict(nail)
        second["orr_no"] = "58"
        second["fc_orr_uit_pr"] = 28
        # The same order number on a second date must not become a second order.
        echo = dict(nail)
        echo["orr_dt"] = "20261112"
        return [first, second, echo]

    old_pages = bot._pages
    bot._pages = pages
    try:
        rows = bot._inquiry("ACCT", friday, "open")
    finally:
        bot._pages = old_pages
    assert "20261113" in calls
    assert "20261112" in calls
    assert len(rows) == 2, rows
    assert {row["orr_no"] for row in rows} == {"55", "58"}
    assert all(row["qty"] == 10 and row["side"] == "buy" for row in rows)
    assert "ACCT" not in "".join(
        "%s %s" % (day, count) for day, count in bot._last_open_inquiry["dates"]
    )

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


def test_regular_session_body_is_limit_00():
    """Regular hours send 지정가 00 and the same fc_orr_uit_pr field."""
    _restore_guards()
    os.environ.pop("ORDER_TYPE", None)
    cases = (
        (_kst(2026, 10, 30, 1, 34), "intraday", "sell"),  # 12:34 EDT
        (_kst(2026, 10, 29, 22, 30), "reset", "buy"),  # 09:30 EDT
        (_kst(2026, 11, 3, 0, 34), "intraday", "sell"),  # 10:34 EST
        (_kst(2026, 11, 3, 2, 32), "reset", "buy"),  # 12:32 EST
    )
    try:
        for moment, mode, side in cases:
            os.environ["LADDER_MODE"] = mode
            _arm(moment)
            captured = {}

            def fake_post(_url, raw, _extra, captured=captured):
                captured["body"] = json.loads(raw.decode())["Input_0"]
                return 200, {}, {"Output_0": {"orr_no": "1"}}

            gb._post_bearer = fake_post
            result = bot._place("TEST", "NAIL", side, 42.0, 10, True, [0])
            assert result is True, (moment.isoformat(), mode, side)
            assert bot._last_order_no == 1
            body = captured["body"]
            assert body["ahi_nmn_pr_tp_cd"] == "00", (
                moment.isoformat(),
                mode,
                body.get("ahi_nmn_pr_tp_cd"),
            )
            assert body["fc_orr_uit_pr"] == 42.0
            assert body["iem_cd"] == "NAIL"
            assert body["orr_qty"] == 10
            assert body["fc_sec_trd_nat_cd"] == "200"
            keys = {
                "act_no",
                "fc_sec_trd_nat_cd",
                "iem_cd",
                "orr_qty",
                "fc_orr_uit_pr",
                "ahi_nmn_pr_tp_cd",
            }
            if side == "buy":
                assert body["wtm_cur_knd_cd"] == "1"
                keys.add("wtm_cur_knd_cd")
            assert set(body) == keys, sorted(body)
    finally:
        gb._post_bearer = _ORIG_POST
        os.environ.pop("LADDER_MODE", None)


def test_regular_reject_does_not_block_other_tickers():
    """A NAIL reject during regular hours still lets KORU and SOXL run."""
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ["LADDER_MODE"] = "intraday"
    os.environ.pop("ORDER_TYPE", None)
    others = [
        ("KORU", "sell", 11.3, 1),
        ("KORU", "buy", 9.4, 1),
        ("SOXL", "sell", 86, 2),
        ("SOXL", "buy", 72, 2),
    ]
    for moment in (
        _kst(2026, 10, 30, 1, 34),  # 12:34 EDT
        _kst(2026, 11, 3, 0, 34),  # 10:34 EST, the failed run
    ):
        state, opens, fills, keys = _day_book()
        nail_key, koru_key, soxl_key = keys
        seen = []

        def place(ticker, item, _counter, seen=seen):
            seen.append((ticker, item["side"], item["price"]))
            if ticker != "NAIL":
                return True
            bot._last_reject = {
                "ticker": "NAIL",
                "side": item["side"],
                "price": item["price"],
                "http": 200,
                "rsp_cd": "23530",
                "rsp_msg": "프리마켓 주문시간이 아닙니다. 호가유형을 변경하세요.",
            }
            return None

        code, out, world = _run_tracking(moment, state, opens, fills, place)
        assert code == 1, out
        assert "order type 00" in out
        assert "REJECT NAIL sell 39 http 200 rsp_cd 23530" in out
        assert "ERROR reject" in out
        assert "ERROR unexpected" not in out
        assert "OK live" not in out
        assert "OK dry" not in out
        assert seen[0] == ("NAIL", "sell", 39)
        assert [item for item in seen if item[0] == "NAIL"] == [("NAIL", "sell", 39)]
        assert world["placed"] == others, (moment.isoformat(), world["placed"])
        assert world["cancelled"] == []
        assert world["exceptions"] == []
        saved = world["saved"]
        assert saved["tickers"]["NAIL"]["sell"] == _idx("NAIL", 33)
        assert saved["tickers"]["NAIL"]["buy"] == _idx("NAIL", 28)
        assert nail_key not in saved["handled"]
        assert saved["tickers"]["KORU"]["sell"] == _idx("KORU", 11.3)
        assert saved["tickers"]["KORU"]["buy"] == _idx("KORU", 10.3)
        assert saved["tickers"]["SOXL"]["sell"] == _idx("SOXL", 86)
        assert saved["tickers"]["SOXL"]["buy"] == _idx("SOXL", 79)
        assert koru_key in saved["handled"]
        assert soxl_key in saved["handled"]


def test_reset_reject_still_places_exception():
    """A reset reject stops that ticker and still places the exception."""
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "SOXL"
    os.environ["LADDER_MODE"] = "reset"
    os.environ.pop("ORDER_TYPE", None)
    for moment in (
        _kst(2026, 10, 29, 22, 30),  # 09:30 EDT, reset slot 17
        _kst(2026, 11, 2, 23, 30),  # 09:30 EST, reset slot 17
    ):
        state = bot._fresh_state()
        state["exception_filled"] = False
        state["tickers"]["SOXL"] = {
            "sell": _idx("SOXL", 94),
            "buy": _idx("SOXL", 86),
            "double_used": [],
        }
        state = bot._clean_state(state)
        world = {"saved": None, "placed": [], "exceptions": []}

        def save_state(data, world=world):
            world["saved"] = json.loads(json.dumps(data))

        def place(ticker, item, _counter, world=world):
            world["placed"].append((ticker, item["side"], item["price"]))
            bot._last_reject = {
                "ticker": ticker,
                "side": item["side"],
                "price": item["price"],
                "http": 200,
                "rsp_cd": "23530",
                "rsp_msg": "regular session",
            }
            return None

        def place_exception(_counter, world=world):
            world["exceptions"].append((200, 37.5))
            return True

        actions = {
            "place": place,
            "cancel": lambda _ticker, _order: True,
            "exists": lambda _ticker, _side, _index: False,
            "confirm_clear": lambda _ticker: True,
            "exception_open": lambda: False,
            "place_exception": place_exception,
            "save_state": save_state,
        }
        _arm(moment)
        code, out = _capture(
            lambda: bot.run(
                "reset",
                "workflow_dispatch",
                moment,
                _et_of(moment),
                [],
                [],
                state,
                actions,
            )
        )
        assert code == 1, out
        assert "order type 00" in out
        assert "REJECT SOXL" in out
        assert "rsp_cd 23530" in out
        assert "exception placed 1" in out
        assert "ERROR reject" in out
        assert "ERROR unexpected" not in out
        assert "OK live" not in out
        assert len(world["placed"]) == 1
        assert world["exceptions"] == [(200, 37.5)]
        saved = world["saved"]
        assert saved["reset_tickers"]["17"].get("SOXL", "") == ""
        assert saved["reset_done"]["17"] == ""


def _record(bucket, number=None):
    def place(ticker, item, _counter):
        bucket.append((ticker, item["side"], item["price"], item["qty"]))
        if number is None:
            return True
        return {"ok": True, "orr_no": number}

    return place


def _nail_before_fill():
    """Anchors before buy 10@33 filled: sells 39/42, buys 33/30."""
    state = bot._fresh_state()
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 39),
        "buy": _idx("NAIL", 33),
        "double_used": [_idx("NAIL", 20)],
    }
    return bot._clean_state(state)


def _nail_lag_book():
    """Inquiry after the kick: the new sell is visible, the new buy is not."""
    return [
        {"ticker": "NAIL", "side": "sell", "price": 42, "qty": 10},
        {"ticker": "NAIL", "side": "sell", "price": 39, "qty": 10},
        {"ticker": "NAIL", "side": "sell", "price": 36, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 30, "qty": 10},
    ]


def _nail_fill():
    return [
        {
            "ticker": "NAIL",
            "side": "buy",
            "idx": _idx("NAIL", 33),
            "price": 33,
            "key": "cd" * 32,
            "filled_qty": 10,
            "cancelled_qty": 0,
        }
    ]


def _recent_buy(moment, price=28, qty=10, orr_no=None, slot="", day=None):
    mark = {
        "ticker": "NAIL",
        "side": "buy",
        "price": price,
        "qty": qty,
        "at": int(moment.timestamp()),
        "slot": slot,
        "day": day if day is not None else moment.strftime("%Y%m%d"),
    }
    if orr_no is not None:
        mark["orr_no"] = orr_no
    return mark


def test_lagging_inquiry_does_not_duplicate_recent_place():
    """The 2026-11-13 NAIL buy 10@28. A late inquiry must not send it again."""
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    moment = _kst(2026, 11, 13, 4, 26)
    assert not bot._orders_blocked(moment)
    state = _nail_before_fill()
    used = list(state["tickers"]["NAIL"]["double_used"])
    opens = _nail_lag_book()
    fills = _nail_fill()
    placed = []
    code, out, world = _run(
        "intraday", moment, state, opens, fills, _record(placed)
    )
    assert code == 0, out
    assert "order type 00" in out
    assert placed == [("NAIL", "buy", 28, 10)], placed
    assert "NEEDS_REVIEW NAIL" not in out
    remembered = world["saved"]["recent_orders"]
    assert len(remembered) == 1
    assert remembered[0]["ticker"] == "NAIL"
    assert remembered[0]["side"] == "buy"
    assert remembered[0]["price"] == 28
    assert remembered[0]["qty"] == 10
    assert remembered[0]["at"] == int(moment.timestamp())
    assert world["saved"]["tickers"]["NAIL"]["double_used"] == used

    stale = _nail_before_fill()
    stale["recent_orders"] = [
        _recent_buy(moment, orr_no=4328),
        {
            "ticker": "NAIL",
            "side": "sell",
            "price": 36,
            "qty": 10,
            "at": int(moment.timestamp()),
            "slot": "",
            "day": "20261113",
            "orr_no": 4327,
        },
    ]
    again = []
    code, out, world = _run(
        "intraday", moment, bot._clean_state(stale), opens, fills, _record(again)
    )
    assert code == 0, out
    assert again == [], (again, out)
    assert "NAIL resting buy 28 qty 10" in out
    assert "would_place 0" in out or "NAIL would_place 0" in out
    assert world["saved"]["tickers"]["NAIL"]["sell"] == _idx("NAIL", 36)
    assert world["saved"]["tickers"]["NAIL"]["buy"] == _idx("NAIL", 30)
    assert world["saved"]["tickers"]["NAIL"]["double_used"] == used
    assert "cd" * 32 in world["saved"]["handled"]

    fresh = bot._clean_state(stale)
    fresh["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 36),
        "buy": _idx("NAIL", 30),
        "double_used": used,
    }
    fresh["handled"] = ["cd" * 32]
    held = []
    code, out, world = _run(
        "intraday", moment, bot._clean_state(fresh), opens, fills, _record(held)
    )
    assert code == 0, out
    assert held == [], (held, out)
    assert "NEEDS_REVIEW NAIL" not in out
    assert world["saved"]["tickers"]["NAIL"]["double_used"] == used

    expired = _nail_before_fill()
    old = _recent_buy(moment)
    old["at"] = int(moment.timestamp()) - bot._RECENT_GRACE - 1
    expired["recent_orders"] = [old]
    late = []
    code, out, _world = _run(
        "intraday", moment, bot._clean_state(expired), opens, fills, _record(late)
    )
    assert code == 0, out
    assert late == [("NAIL", "buy", 28, 10)], late

    shown = list(opens) + [
        {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 10, "orr_no": 4328}
    ]
    seen = []
    code, out, world = _run(
        "intraday",
        moment,
        bot._clean_state(stale),
        shown,
        fills,
        _record(seen),
    )
    assert code == 0, out
    assert seen == [], seen
    assert world["saved"]["recent_orders"] == []


def test_reset_keeps_same_slot_recent_orders():
    """A reset rebuilds the book, except orders this slot already sent."""
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL"
    os.environ.pop("ORDER_TYPE", None)
    moment = _kst(2026, 11, 12, 10, 0)
    assert bot._reset_slot(moment) == "09"
    assert not bot._orders_blocked(moment)
    state = bot._fresh_state()
    state["tickers"]["NAIL"] = {
        "sell": _idx("NAIL", 33),
        "buy": _idx("NAIL", 28),
        "double_used": [_idx("NAIL", 20)],
    }
    used = [_idx("NAIL", 20)]
    state["recent_orders"] = [
        _recent_buy(moment, slot="09", day="20261112", orr_no=77)
    ]
    placed = []
    code, out, world = _run(
        "reset", moment, bot._clean_state(state), [], [], _record(placed)
    )
    assert code == 0, out
    assert ("NAIL", "buy", 28, 10) not in placed, placed
    assert ("NAIL", "sell", 33, 10) in placed
    assert ("NAIL", "sell", 36, 10) in placed
    assert ("NAIL", "buy", 26, 10) in placed
    assert "NAIL resting buy 28 qty 10" in out
    assert world["saved"]["tickers"]["NAIL"]["double_used"] == used
    assert any(item.get("orr_no") == 77 for item in world["saved"]["recent_orders"])

    other = bot._clean_state(state)
    other["recent_orders"] = [
        _recent_buy(moment, slot="17", day="20261112", orr_no=77)
    ]
    placed = []
    code, out, world = _run(
        "reset", moment, other, [], [], _record(placed)
    )
    assert code == 0, out
    assert ("NAIL", "buy", 28, 10) in placed, placed
    assert world["saved"]["tickers"]["NAIL"]["double_used"] == used

    stale = bot._clean_state(state)
    aged = _recent_buy(moment, slot="09", day="20261112")
    aged["at"] = int(moment.timestamp()) - bot._RECENT_GRACE - 1
    stale["recent_orders"] = [aged]
    placed = []
    code, out, _world = _run("reset", moment, stale, [], [], _record(placed))
    assert code == 0, out
    assert ("NAIL", "buy", 28, 10) in placed, placed


def test_cancel_dup_cancels_only_the_newest_extra():
    _restore_guards()
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ["CANCEL_DUP"] = "cancel-dup NAIL BUY 28 10"
    os.environ.pop("ORDER_TYPE", None)
    moment = _kst(2026, 11, 13, 4, 26)
    state = _nail_before_fill()
    state["recent_orders"] = [
        _recent_buy(moment, orr_no=10),
        _recent_buy(moment, orr_no=5),
    ]
    state = bot._clean_state(state)
    used = list(state["tickers"]["NAIL"]["double_used"])
    opens = [
        {
            "ticker": "NAIL",
            "side": "buy",
            "price": 28,
            "qty": 10,
            "orr_no": 10,
            "orr_dt": "20261112",
        },
        {
            "ticker": "NAIL",
            "side": "buy",
            "price": 28.0,
            "qty": 10,
            "orr_no": "5",
            "orr_dt": "20261113",
        },
        {
            "ticker": "NAIL",
            "side": "buy",
            "price": 28,
            "qty": 10,
            "orr_no": 4,
            "orr_dt": "20261113",
        },
        {"ticker": "NAIL", "side": "sell", "price": 36, "qty": 10, "orr_no": 99},
        {"ticker": "KORU", "side": "buy", "price": 28, "qty": 10, "orr_no": 100},
        {"ticker": "NAIL", "side": "buy", "price": 30, "qty": 10, "orr_no": 101},
        {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 20, "orr_no": 102},
    ]
    cancelled = []

    def cancel(ticker, order):
        cancelled.append(
            (
                ticker,
                order.get("side"),
                order.get("price"),
                order.get("qty"),
                order.get("orr_no"),
            )
        )
        return True

    def save_state(data):
        world["saved"] = json.loads(json.dumps(data))

    world = {"saved": None}
    actions = {
        "place": lambda *_args: (_ for _ in ()).throw(AssertionError("placed")),
        "cancel": cancel,
        "exists": lambda *_args: False,
        "confirm_clear": lambda *_args: True,
        "exception_open": lambda: False,
        "place_exception": lambda *_args: False,
        "save_state": save_state,
    }
    bot._now_kst = lambda: moment
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "workflow_dispatch",
            moment,
            _et_of(moment),
            opens,
            [],
            state,
            actions,
        )
    )
    assert code == 0, out
    assert cancelled == [("NAIL", "buy", 28.0, 10, "5")], cancelled
    assert "cancel-dup open NAIL buy 28 qty 10 orr_no 5 orr_dt 20261113" in out
    assert "cancel-dup open NAIL buy 28 qty 10 orr_no 10 orr_dt 20261112" in out
    assert "cancel-dup open NAIL buy 28 qty 10 orr_no 4 orr_dt 20261113" in out
    assert "cancel-dup open NAIL buy 30 qty 10 orr_no 101" in out
    assert "cancel-dup open NAIL sell 36 qty 10 orr_no 99" in out
    assert "cancel-dup open KORU" not in out
    assert out.count("cancel-dup cancelled") == 1
    assert "cancel-dup cancelled NAIL buy 28 qty 10 orr_no 5" in out
    assert "OK cancel-dup" in out
    assert world["saved"]["tickers"]["NAIL"]["double_used"] == used
    assert all(item.get("orr_no") != 5 for item in world["saved"]["recent_orders"])
    assert any(item.get("orr_no") == 10 for item in world["saved"]["recent_orders"])

    cancelled.clear()
    one = [opens[0], opens[3]]
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "workflow_dispatch",
            moment,
            _et_of(moment),
            one,
            [],
            state,
            actions,
        )
    )
    assert code == 0, out
    assert cancelled == []
    assert "cancel-dup skip NAIL buy 28 qty 10 matches 1" in out

    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "workflow_dispatch",
            moment,
            _et_of(moment),
            [opens[3]],
            [],
            state,
            actions,
        )
    )
    assert code == 0, out
    assert cancelled == []
    assert "matches 0" in out

    guard = _kst(2026, 11, 13, 16, 30)
    bot._now_kst = lambda: guard
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "workflow_dispatch",
            guard,
            _et_of(guard),
            opens,
            [],
            state,
            actions,
        )
    )
    assert code == 0, out
    assert cancelled == []
    assert "skip: time guard" in out

    os.environ["CANCEL_DUP"] = "cancel-dup NAIL BUY 28"
    bot._now_kst = lambda: moment
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "workflow_dispatch",
            moment,
            _et_of(moment),
            opens,
            [],
            state,
            actions,
        )
    )
    assert code == 0, out
    assert cancelled == []
    assert "cancel-dup skip parse" in out

    os.environ["CANCEL_DUP"] = "cancel-dup NAIL BUY 28 10"
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup", "push", moment, _et_of(moment), opens, [], state, actions
        )
    )
    assert code == 0, out
    assert cancelled == []
    assert "OK dry" in out

    os.environ.pop("LIVE_TRADING", None)
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "schedule",
            moment,
            _et_of(moment),
            opens,
            [],
            state,
            actions,
        )
    )
    assert code == 0, out
    assert cancelled == []
    assert "OK dry" in out


def test_prefer_newer_state_and_cancel_workflow():
    assert restore.prefer_newer(None, None) == "none"
    assert restore.prefer_newer({"saved_at": 1}, None) == "cache"
    assert restore.prefer_newer(None, {"saved_at": 0}) == "artifact"
    assert restore.prefer_newer({"saved_at": 0}, {"saved_at": 0}) == "cache"
    assert restore.prefer_newer({"saved_at": 5}, {"saved_at": 9}) == "artifact"
    assert restore.prefer_newer({"saved_at": 9}, {"saved_at": 5}) == "cache"
    assert restore.prefer_newer({}, {"saved_at": 1}) == "artifact"

    root = os.path.dirname(os.path.abspath(__file__))
    intra = open(
        os.path.join(root, ".github/workflows/ladder-intraday.yml"), encoding="utf-8"
    ).read()
    reset = open(
        os.path.join(root, ".github/workflows/ladder-reset.yml"), encoding="utf-8"
    ).read()
    cancel = open(
        os.path.join(root, ".github/workflows/ladder-cancel-dup.yml"), encoding="utf-8"
    ).read()
    assert "startsWith(github.event.comment.body, 'kick')" in intra
    assert "ladder-skip-" in intra
    assert "branches:" in intra
    assert "- main" in intra
    assert "branches:" in reset
    assert "- main" in reset
    assert "hashFiles('.ladder-state/state.json')" not in intra
    assert "github.event.issue.number == 18" in cancel
    assert "ladder-kick" in cancel
    assert "github.repository_owner" in cancel
    assert "startsWith(github.event.comment.body, 'cancel-dup')" in cancel
    assert "pull_request" in cancel
    assert "LADDER_MODE: cancel-dup" in cancel
    assert "ladder-bot" in cancel
    assert "ladder-skip-" in cancel
    assert "startsWith(github.event.comment.body, 'cancel-dup')" in cancel
    assert "workflow_dispatch:" not in cancel
    assert "cron:" not in cancel
    assert "on:\n  push:" not in cancel


def test_http_error_names_the_endpoint():
    """A broker failure names the endpoint. An old date does not stop today."""
    _restore_guards()
    account = "00112233445566"
    secret = "supersecret-token-value"
    gb._secrets.extend((account, secret))
    bot._note_http(
        "acctinfo",
        401,
        {"rsp_cd": "IGW00121", "rsp_msg": "rejected " + account + " " + secret},
    )
    code, out = _capture(bot._die_http)
    assert code == 1
    assert "ERROR http /n2/acctinfo status 401 rsp_cd IGW00121" in out
    assert "rsp_msg rejected" in out
    assert account not in out
    assert secret not in out

    moment = _kst(2026, 11, 13, 5, 49)
    assert bot._required_open_dates(moment) == {"20261113", "20261112"}
    nail = {
        "iem_cd": "NAIL",
        "fc_sec_trd_nat_cd": "200",
        "oss_sby_dit_cd": "2",
        "fc_orr_uit_pr": "28.0000",
        "orr_qty": "10",
        "ny_cns_orr_qty": "",
        "oss_ato_orr_sts_cd": "3",
        "orr_no": "77",
        "orr_dt": "20261113",
    }

    def pages(_url, body, _block):
        day = body["orr_dt"]
        if day == "20261109":
            bot._note_http(
                "unexecuted",
                400,
                {"rsp_cd": "EOLD", "rsp_msg": "none " + account},
                "date " + day,
            )
            return None
        if day == "20261113":
            return [dict(nail)]
        return []

    saved = bot._pages
    bot._pages = pages
    try:
        rows, out = _capture(lambda: bot._inquiry(account, moment, "open"))
    finally:
        bot._pages = saved
    assert rows is not None and len(rows) == 1, rows
    assert rows[0]["qty"] == 10
    assert "inquiry skip /gbstock/inquiry/v1/unexecuted date 20261109 status 400" in out
    assert account not in out
    assert "ERROR http" not in out

    def today_fails(_url, body, _block):
        if body["orr_dt"] == "20261113":
            bot._note_http(
                "unexecuted",
                500,
                {"rsp_cd": "E500", "rsp_msg": "down " + secret},
                "date 20261113",
            )
            return None
        return []

    bot._pages = today_fails
    try:
        rows, _out = _capture(lambda: bot._inquiry(account, moment, "open"))
        code, out = _capture(bot._die_http)
    finally:
        bot._pages = saved
    assert rows is None
    assert code == 1
    assert "ERROR http /gbstock/inquiry/v1/unexecuted date 20261113 status 500 rsp_cd E500" in out
    assert secret not in out
    assert "rsp_msg down" in out


def test_invalid_cached_token_is_replaced():
    """400 IGW40043 is an invalid token. Issue a new one and retry once."""
    calls = []

    def fake_post(_url, _body, headers):
        auth = headers.get("Authorization", "")
        calls.append(auth)
        if auth == "Bearer stale":
            return 400, {}, {
                "rsp_cd": "IGW40043",
                "rsp_msg": "유효하지 않은 token 입니다.",
            }
        return 200, {}, {"rsp_cd": "00000", "rsp_msg": "ok"}

    issued = {"n": 0}

    def fake_issue(_key, _secret):
        issued["n"] += 1
        return "fresh"

    old_post = gb._post
    old_issue = gb._issue_token
    old_token = gb._token
    gb._post = fake_post
    gb._issue_token = fake_issue
    gb._token = "stale"
    try:
        status, _headers, payload = gb._post_bearer(
            "https://api.nhplug.com:8443/n2/acctinfo", b"{}", {}
        )
        assert status == 200, payload
        assert calls == ["Bearer stale", "Bearer fresh"]
        assert issued["n"] == 1
        assert gb._token == "fresh"
        calls.clear()
        issued["n"] = 0
        gb._token = "stale"

        def other_error(_url, _body, headers):
            calls.append(headers.get("Authorization", ""))
            return 500, {}, {"rsp_cd": "E500", "rsp_msg": "down"}

        gb._post = other_error
        status, _headers, payload = gb._post_bearer(
            "https://api.nhplug.com:8443/n2/acctinfo", b"{}", {}
        )
        assert status == 500
        assert calls == ["Bearer stale"]
        assert issued["n"] == 0
    finally:
        gb._post = old_post
        gb._issue_token = old_issue
        gb._token = old_token


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
        test_koru_buy_fill_steps_below_7_2()
        test_day_session_partial_does_not_advance()
        test_day_session_crons()
        test_weekend_guard_follows_us_session()
        test_dates_cover_previous_trading_day()
        test_regular_session_body_is_limit_00()
        test_regular_reject_does_not_block_other_tickers()
        test_reset_reject_still_places_exception()
        test_lagging_inquiry_does_not_duplicate_recent_place()
        test_reset_keeps_same_slot_recent_orders()
        test_cancel_dup_cancels_only_the_newest_extra()
        test_prefer_newer_state_and_cancel_workflow()
        test_http_error_names_the_endpoint()
        test_invalid_cached_token_is_replaced()
    finally:
        bot._now_kst = _ORIG_NOW
        bot._orders_blocked = _ORIG_BLOCKED
        gb._post_bearer = _ORIG_POST
        os.environ.pop("ORDER_TYPE", None)
        os.environ.pop("LADDER_MODE", None)
        os.environ.pop("CANCEL_DUP", None)
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
