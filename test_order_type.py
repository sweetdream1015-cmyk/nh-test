"""Session order types across DST, and broker-reject logging. No live calls."""

import io
import json
import os
import sys
from datetime import datetime

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


def main():
    try:
        test_session_table()
        test_order_type_override()
        test_premarket_wait_and_retry()
        test_reject_redaction_and_intraday_state()
        test_partial_leg_does_not_advance()
        test_crons_kept()
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
