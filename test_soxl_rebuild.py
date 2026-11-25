"""SOXL is 20 shares. Reset places the buy of 400 at 18.7. Rebuild leaves it."""

import os
from datetime import datetime

import gb_readonly_check as gb
import ladder_bot as bot
import ladder_dryrun as ladders
import test_reset_batch as sim

_WHEN = sim._FIXED
_KEY = "ab" * 32


def _level(price):
    return ladders._level_of("SOXL", price)


def _pair():
    used = [_level(price) for price in (72, 66, 60, 55, 50, 46, 42, 39, 36, 33, 30)]
    return sim._pair("SOXL", 36, 30, used)


def _state():
    return bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "NAIL": sim._pair("NAIL", 39, 33),
                "KORU": sim._pair("KORU", 7.2, 6.0),
                "SOXL": _pair(),
            },
            "handled": [],
            "exception_filled": False,
            "exception_id": "",
            "reset_done": {"09": "", "17": ""},
        }
    )


def _opens():
    rows = []
    number = 1
    for price in (36, 39, 42, 46, 50):
        rows.append(
            {"ticker": "SOXL", "side": "sell", "price": price, "qty": 4, "orr_no": number}
        )
        number += 1
    for price, qty in ((30, 4), (28, 8), (26, 4)):
        rows.append(
            {"ticker": "SOXL", "side": "buy", "price": price, "qty": qty, "orr_no": number}
        )
        number += 1
    rows.append(
        {"ticker": "SOXL", "side": "sell", "price": 75, "qty": 150, "orr_no": 90}
    )
    rows.append({"ticker": "KORU", "side": "buy", "price": 6.0, "qty": 2, "orr_no": 91})
    rows.append({"ticker": "NAIL", "side": "sell", "price": 39, "qty": 10, "orr_no": 92})
    return rows


def _fill_30():
    return {
        "ticker": "SOXL",
        "side": "buy",
        "price": 30,
        "qty": 4,
        "filled_qty": 4,
        "cancelled_qty": 0,
        "idx": _level(30),
        "key": _KEY,
    }


def _go(state, opens, fills, live):
    if live:
        os.environ["SOXL_REBUILD_LIVE"] = "on"
    else:
        os.environ.pop("SOXL_REBUILD_LIVE", None)
    os.environ["LIVE_TRADING"] = "on"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    bot._now_kst = lambda: _WHEN
    world = {"placed": [], "cancelled": [], "exceptions": []}
    code, out = sim._capture(
        lambda: bot.run(
            "soxl-rebuild",
            "workflow_dispatch",
            _WHEN,
            _WHEN.astimezone(bot.ET),
            opens,
            fills,
            state,
            sim._actions(world),
        )
    )
    return code, out, world


def test_flat_twenty_and_no_double():
    assert bot._HARD_MAX["SOXL"] == 20
    assert bot._HARD_MAX["KORU"] == 4
    assert bot._HARD_MAX["NAIL"] == 40
    used = [_level(30)]
    assert bot._buy_computed("SOXL", _level(30), []) == 20
    assert bot._buy_computed("SOXL", _level(30), used) == 20
    assert bot._buy_computed("SOXL", _level(42), []) == 20
    assert bot._buy_computed("KORU", ladders._level_of("KORU", 6), []) == 2
    assert bot._buy_computed("NAIL", ladders._level_of("NAIL", 20), []) == 40
    fresh = bot._desired("SOXL", _level(36), _level(30), 1.0, [])
    assert [(item["side"], item["price"], item["qty"]) for item in fresh] == [
        ("sell", 36, 20),
        ("sell", 39, 20),
        ("buy", 30, 20),
        ("buy", 28, 20),
    ]
    stepped = bot._anchor_fill_step(
        "SOXL", _pair(), [_fill_30()], set(), 1.0, bot._clean_state({})
    )
    assert ladders._price_at("SOXL", stepped["sell"]) == 33
    assert ladders._price_at("SOXL", stepped["buy"]) == 28
    book = bot._desired(
        "SOXL", stepped["sell"], stepped["buy"], 1.0, stepped["double_used"]
    )
    assert [(item["side"], item["price"], item["qty"]) for item in book] == [
        ("sell", 33, 20),
        ("sell", 36, 20),
        ("buy", 28, 20),
        ("buy", 26, 20),
    ]


def test_buy_400_at_18_7_is_a_reset_exception():
    ids = [item["id"] for item in bot._EXCEPTIONS]
    assert ids == ["koru-buy-100-4.2", "soxl-buy-400-18-7-v2"]
    assert "soxl-buy-400-18-7" not in ids
    assert "soxl-sell-150-75" not in ids
    exc = bot._EXCEPTION_BY_ID["soxl-buy-400-18-7-v2"]
    assert exc["side"] == "buy" and exc["qty"] == 400
    assert abs(exc["price"] - 18.7) < 1e-9
    assert ladders._level_of("SOXL", 18.7) is None
    state = bot._clean_state({})
    assert bot._exception_remaining(state, exc) == 400
    assert bot._exception_remaining(state) == 0
    assert bot._exception_due(state, [], "09", "20261125") is False
    assert bot._exception_due(state, [], "09", "20261125", exc) is True
    state["exception_id"] = bot._EXCEPTION_ID
    state["exception_filled"] = True
    assert bot._exception_done(state) is True
    assert bot._exception_due(state, [], "09", "20261125", exc) is True
    old = bot._clean_state(
        {
            "exception_orders": {
                "soxl-buy-400-18-7": {
                    "filled": True,
                    "filled_qty": 400,
                    "parts": {},
                    "resting": {},
                    "order_nos": [9],
                }
            }
        }
    )
    assert "soxl-buy-400-18-7" not in old.get("exception_orders", {})
    assert bot._exception_done_for(old, exc) is False


def test_last_sell_from_36_is_103_for_15():
    """475 - 100 base - 120 exception = 255 ladder shares from 36."""
    index = _level(36)
    left = 475 - 100 - 120
    assert left == 255
    last = None
    while left > 0:
        price = ladders._price_at("SOXL", index)
        qty = 20 if left >= 20 else left
        last = (price, qty)
        left -= qty
        index += 1
    assert last == (103, 15)


def test_dry_rebuild_keeps_36_30_despite_the_30_fill():
    bare = [order for order in _opens() if order.get("price") != 75]
    code, out, world = _go(_state(), bare, [_fill_30()], live=False)
    assert code == 0, out
    assert "SOXL anchor sell 36 buy 30" in out
    assert "SOXL plan place sell 36 qty 20" in out
    assert "SOXL plan place sell 39 qty 20" in out
    assert "SOXL plan place buy 30 qty 20" in out
    assert "SOXL plan place buy 28 qty 20" in out
    assert "SOXL plan place sell 33 qty 20" not in out
    assert "SOXL plan place buy 26 qty 20" not in out
    assert "exception plan place sell 75" not in out
    assert "SOXL plan cancel sell 75" not in out
    assert "18.7" not in out
    assert "NAIL unchanged" in out
    assert "KORU unchanged" in out
    assert "OK dry" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert world["exceptions"] == []
    assert "saved" not in world

    code, out, world = _go(_state(), _opens(), [_fill_30()], live=False)
    assert code == 0, out
    assert "SOXL anchor sell 36 buy 30" in out
    assert "exception plan place sell 75" not in out
    assert "SOXL plan ignore sell 75 qty 150" not in out
    assert "SOXL plan cancel sell 75 qty 150" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert world["exceptions"] == []
    assert "saved" not in world
    root = os.path.dirname(os.path.abspath(__file__))
    text = open(
        os.path.join(root, ".github/workflows/soxl-rebuild.yml"), encoding="utf-8"
    ).read()
    assert "default: false" in text
    assert "SOXL_REBUILD_LIVE" in text
    assert "NHPLUG_ACCOUNT_SUFFIX:" not in text
    assert "startsWith(github.event.comment.body, 'soxl-rebuild')" in text
    assert "github.event.issue.number == 18" in text
    assert "LADDER_MODE: soxl-rebuild" in text


def test_live_rebuild_cancels_only_soxl_ladder_orders():
    code, out, world = _go(_state(), _opens(), [_fill_30()], live=True)
    assert code == 0, out
    assert "OK live" in out
    assert {ticker for ticker, _price, _number in world["cancelled"]} == {"SOXL"}
    assert ("SOXL", 75, 90) in world["cancelled"]
    assert sorted(world["placed"]) == [
        ("SOXL", "buy", 28, 20),
        ("SOXL", "buy", 30, 20),
        ("SOXL", "sell", 36, 20),
        ("SOXL", "sell", 39, 20),
    ]
    assert world["exceptions"] == []
    assert (150, 75) not in world["exceptions"]
    saved = world["saved"]
    assert saved["tickers"]["SOXL"]["sell"] == _level(36)
    assert saved["tickers"]["SOXL"]["buy"] == _level(30)
    assert saved["tickers"]["NAIL"]["sell"] == sim._pair("NAIL", 39, 33)["sell"]
    assert saved["tickers"]["KORU"]["buy"] == sim._pair("KORU", 7.2, 6.0)["buy"]
    assert _KEY in saved["handled"]
    assert "soxl-buy-400-18-7" not in saved.get("exception_orders", {})
    assert "soxl-buy-400-18-7-v2" not in saved.get("exception_orders", {})


def test_later_reset_and_intraday_keep_36_30():
    """The 11:55 buy of 4 at 30 is handled, so the anchor does not step."""
    code, out, world = _go(_state(), _opens(), [_fill_30()], live=True)
    assert code == 0, out
    saved = world["saved"]
    assert _KEY in saved["handled"]
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "SOXL"
    os.environ.pop("SOXL_REBUILD_LIVE", None)
    os.environ.pop("ORDER_TYPE", None)
    bot._now_kst = lambda: _WHEN
    code, out, reset_world = sim._run(
        saved, [], {}, fills=[_fill_30()], mode="reset", when=_WHEN
    )
    assert code == 0, out
    kept = reset_world["saved"]["tickers"]["SOXL"]
    assert kept["sell"] == _level(36)
    assert kept["buy"] == _level(30)
    assert ("SOXL", "sell", 33, 20) not in reset_world["placed"]
    assert ("SOXL", "buy", 26, 20) not in reset_world["placed"]
    assert "SOXL resting sell 36 qty 20" in out or ("SOXL", "sell", 36, 20) in reset_world["placed"]

    code, out, intra = sim._run(
        saved, [], {}, fills=[_fill_30()], mode="intraday", when=_WHEN
    )
    assert code == 0, out
    kept = intra["saved"]["tickers"]["SOXL"]
    assert kept["sell"] == _level(36)
    assert kept["buy"] == _level(30)
    assert ("SOXL", "sell", 33, 20) not in intra["placed"]
    assert ("SOXL", "buy", 26, 20) not in intra["placed"]


def _old_book():
    """The open SOXL book from before the 20-share rebuild."""
    return [
        {"ticker": "SOXL", "side": "buy", "price": 26, "qty": 8, "orr_no": 26},
        {"ticker": "SOXL", "side": "buy", "price": 28, "qty": 8, "orr_no": 28},
        {"ticker": "SOXL", "side": "sell", "price": 33, "qty": 4, "orr_no": 33},
        {"ticker": "SOXL", "side": "sell", "price": 36, "qty": 4, "orr_no": 36},
        {"ticker": "SOXL", "side": "sell", "price": 75, "qty": 150, "orr_no": 75},
    ]


def test_scheduled_runs_leave_the_old_book():
    """Reset and intraday must not quote 20s or 400 beside the old book.

    The sell of 150 at 75 stays until soxl-rebuild cancels it. The
    unhandled buy of 4 at 30 does not step the anchor, even if a sell
    at 39 is also open.
    """
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ.pop("SOXL_REBUILD_LIVE", None)
    os.environ.pop("ORDER_TYPE", None)
    when = datetime(2026, 11, 25, 12, 40, tzinfo=bot.KST)
    bot._now_kst = lambda: when
    opens = _old_book()
    code, out, world = sim._run(
        _state(), opens, {}, fills=[_fill_30()], mode="reset", when=when
    )
    assert code == 0, out
    assert "SOXL skip old book" in out
    assert world["cancelled"] == []
    assert [item for item in world["placed"] if item[0] == "SOXL"] == []
    assert (400, 18.7) not in world["exceptions"]
    assert (150, 75) not in world["exceptions"]
    kept = world["saved"]["tickers"]["SOXL"]
    assert kept["sell"] == _level(36)
    assert kept["buy"] == _level(30)
    assert _KEY not in world["saved"]["handled"]
    nail = [item for item in world["placed"] if item[0] == "NAIL"]
    assert nail, out

    wider = opens + [
        {"ticker": "SOXL", "side": "sell", "price": 39, "qty": 4, "orr_no": 39}
    ]
    code, out, intra = sim._run(
        _state(), wider, {}, fills=[_fill_30()], mode="intraday", when=when
    )
    assert code == 0, out
    assert "SOXL skip old book" in out
    assert intra["placed"] == []
    assert intra["cancelled"] == []
    assert (400, 18.7) not in intra["exceptions"]
    kept = intra["saved"]["tickers"]["SOXL"]
    assert kept["sell"] == _level(36)
    assert kept["buy"] == _level(30)
    assert "NEEDS_REVIEW SOXL" not in out


def test_live_clocked_rebuild_at_1240_reaches_runner():
    """A live issue-comment rebuild at 12:40 KST must not log OK skip.

    The guards at 08:30 and 16:30 KST still stop it before any order.
    """
    noon = datetime(2026, 11, 25, 12, 40, tzinfo=bot.KST)
    morning = datetime(2026, 11, 25, 8, 30, tzinfo=bot.KST)
    afternoon = datetime(2026, 11, 25, 16, 30, tzinfo=bot.KST)
    assert noon.weekday() == 2
    assert bot._window_open("soxl-rebuild", noon, noon.astimezone(bot.ET))
    assert not bot._orders_blocked(noon)
    assert bot._orders_blocked(morning)
    assert bot._orders_blocked(afternoon)
    ten = datetime(2026, 11, 25, 10, 0, tzinfo=bot.KST)
    assert not bot._orders_blocked(ten)
    assert bot._window_open("soxl-rebuild", ten, ten.astimezone(bot.ET))

    calls = {"n": 0}
    moment = {"when": noon}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            current = moment["when"]
            if tz is None:
                return current.replace(tzinfo=None)
            return current.astimezone(tz)

    original_run = bot._run_soxl_rebuild
    original_datetime = bot.datetime
    original_token = bot._load_token
    original_inquiry = bot._inquiry
    original_state = bot._load_state
    original_place = bot._place
    original_cancel = bot._cancel
    original_place_exc = bot._place_exception
    original_save = bot._save_json
    original_post = gb._post_bearer

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return original_run(*args, **kwargs)

    saved = {}

    def save_json(_path, data):
        saved["data"] = data

    def post(url, _body, _extra):
        if "acctinfo" not in url:
            return 500, {}, {}
        return 200, {}, {"Output_0": [{"acct_type": "01", "acct_no": "20102999991"}]}

    os.environ["LADDER_MODE"] = "soxl-rebuild"
    os.environ["GITHUB_EVENT_NAME"] = "schedule"
    os.environ["SOXL_REBUILD_LIVE"] = "on"
    os.environ["LIVE_TRADING"] = "on"
    os.environ["NHPLUG_APP_KEY"] = "test-key"
    os.environ["NHPLUG_APP_SECRET"] = "test-secret"
    os.environ.pop("NHPLUG_ACCOUNT_SUFFIX", None)
    os.environ.pop("ORDER_TYPE", None)
    bot._now_kst = lambda: moment["when"]
    bot.datetime = _Clock
    bot._run_soxl_rebuild = wrapped
    bot._load_token = lambda: "token"
    bot._load_state = _state
    bot._inquiry = lambda *_args, **_kwargs: []
    bot._place = lambda *_args, **_kwargs: True
    bot._cancel = lambda *_args, **_kwargs: True
    bot._place_exception = lambda *_args, **_kwargs: True
    bot._save_json = save_json
    gb._post_bearer = post
    try:
        code, out = sim._capture(bot._main_once)
        assert calls["n"] == 1, out
        assert "OK skip" not in out
        assert "SOXL anchor sell 36 buy 30" in out
        assert "OK live" in out
        assert "20102999991" not in out
        assert code == (0, False), out

        calls["n"] = 0
        for blocked in (morning, afternoon):
            moment["when"] = blocked
            code, out = sim._capture(bot._main_once)
            assert code == (0, False), out
            assert "skip: time guard" in out
            assert calls["n"] == 0, out
            assert "SOXL anchor" not in out
    finally:
        bot.datetime = original_datetime
        bot._run_soxl_rebuild = original_run
        bot._load_token = original_token
        bot._inquiry = original_inquiry
        bot._load_state = original_state
        bot._place = original_place
        bot._cancel = original_cancel
        bot._place_exception = original_place_exc
        bot._save_json = original_save
        gb._post_bearer = original_post
        os.environ.pop("LADDER_MODE", None)
        os.environ.pop("GITHUB_EVENT_NAME", None)
        os.environ.pop("NHPLUG_APP_KEY", None)
        os.environ.pop("NHPLUG_APP_SECRET", None)


def _user_book():
    """The 12:51 book: old 4/8 orders, an extra sell 4 at 39, and the 400."""
    rows = [
        ("buy", 28, 8, 28),
        ("buy", 26, 8, 26),
        ("sell", 36, 4, 36),
        ("sell", 39, 4, 39),
        ("sell", 33, 4, 33),
    ]
    book = []
    for side, price, qty, number in rows:
        book.append(
            {
                "ticker": "SOXL",
                "side": side,
                "price": price,
                "qty": qty,
                "orr_no": number,
                "idx": _level(price),
            }
        )
    book.append(
        {"ticker": "SOXL", "side": "sell", "price": 75, "qty": 150, "orr_no": 75, "idx": None}
    )
    book.append(
        {"ticker": "SOXL", "side": "buy", "price": 18.7, "qty": 400, "orr_no": 187, "idx": None}
    )
    book.append({"ticker": "KORU", "side": "buy", "price": 6.0, "qty": 2, "orr_no": 60})
    return book


def _manual_buy():
    """A share-count change, not a ladder fill at the buy anchor."""
    return {
        "ticker": "SOXL",
        "side": "buy",
        "price": 31.2,
        "qty": 15,
        "filled_qty": 15,
        "cancelled_qty": 0,
        "key": "ef" * 32,
    }


def test_extra_sell_39_is_cancelled_and_anchor_stays():
    book = _user_book()
    code, out, world = _go(_state(), book, [_fill_30(), _manual_buy()], live=True)
    assert code == 0, out
    assert "SOXL plan cancel sell 39 qty 4" in out
    assert "SOXL plan cancel sell 75 qty 150" in out
    assert "SOXL plan ignore buy 18.7 qty 400" in out
    assert "SOXL plan cancel buy 18.7" not in out
    assert "exception plan place buy 18.7" not in out
    assert ("SOXL", 39, 39) in world["cancelled"]
    assert ("SOXL", 75, 75) in world["cancelled"]
    assert all(number != 187 for _ticker, _price, number in world["cancelled"])
    assert all(ticker == "SOXL" for ticker, _price, _number in world["cancelled"])
    assert world["placed"].count(("SOXL", "sell", 39, 20)) == 1
    assert world["exceptions"] == []
    saved = world["saved"]
    assert saved["tickers"]["SOXL"]["sell"] == _level(36)
    assert saved["tickers"]["SOXL"]["buy"] == _level(30)
    assert _KEY in saved["handled"]
    assert "ef" * 32 in saved["handled"]

    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "SOXL"
    os.environ.pop("SOXL_REBUILD_LIVE", None)
    code, out, later = sim._run(
        saved, [], {}, fills=[_fill_30(), _manual_buy()], mode="reset", when=_WHEN
    )
    assert code == 0, out
    kept = later["saved"]["tickers"]["SOXL"]
    assert kept["sell"] == _level(36)
    assert kept["buy"] == _level(30)
    assert ("SOXL", "sell", 33, 20) not in later["placed"]
    assert ("SOXL", "buy", 26, 20) not in later["placed"]


def test_reinquire_does_not_double_place():
    """A rung still open after the cancels is not placed again."""
    book = _user_book()
    stuck = [
        {"ticker": "SOXL", "side": "sell", "price": 39, "qty": 4, "orr_no": 39, "idx": _level(39)},
        {"ticker": "SOXL", "side": "buy", "price": 18.7, "qty": 400, "orr_no": 187, "idx": None},
    ]
    os.environ["SOXL_REBUILD_LIVE"] = "on"
    os.environ["LIVE_TRADING"] = "on"
    os.environ.pop("ORDER_TYPE", None)
    bot._now_kst = lambda: _WHEN
    world = {"placed": [], "cancelled": [], "exceptions": []}
    actions = sim._actions(world)
    actions["fresh_opens"] = lambda: stuck
    code, out = sim._capture(
        lambda: bot.run(
            "soxl-rebuild",
            "workflow_dispatch",
            _WHEN,
            _WHEN.astimezone(bot.ET),
            book,
            [_manual_buy()],
            _state(),
            actions,
        )
    )
    assert code == 1, out
    assert "SOXL still open sell 39 qty 4" in out
    assert "SOXL resting sell 39 qty 20" in out
    assert ("SOXL", "sell", 39, 20) not in world["placed"]
    assert world["placed"].count(("SOXL", "sell", 36, 20)) == 1
    assert world["placed"].count(("SOXL", "buy", 30, 20)) == 1
    assert world["placed"].count(("SOXL", "buy", 28, 20)) == 1
    assert all(number != 187 for _ticker, _price, number in world["cancelled"])
    assert "SOXL still open buy 18.7" not in out
    assert world["saved"]["tickers"]["SOXL"]["sell"] == _level(36)
    assert world["saved"]["tickers"]["SOXL"]["buy"] == _level(30)

    already = [
        {"ticker": "SOXL", "side": "sell", "price": 39, "qty": 20, "orr_no": 391, "idx": _level(39)},
        {"ticker": "SOXL", "side": "buy", "price": 18.7, "qty": 400, "orr_no": 187, "idx": None},
    ]
    world = {"placed": [], "cancelled": [], "exceptions": []}
    actions = sim._actions(world)
    actions["fresh_opens"] = lambda: already
    code, out = sim._capture(
        lambda: bot.run(
            "soxl-rebuild",
            "workflow_dispatch",
            _WHEN,
            _WHEN.astimezone(bot.ET),
            already,
            [],
            _state(),
            actions,
        )
    )
    assert code == 1, out
    assert ("SOXL", "sell", 39, 20) not in world["placed"]
    assert "SOXL resting sell 39 qty 20" in out


def test_live_rebuild_cancels_without_live_trading():
    """The 12:51 run planned, then cancel was not live, so nothing was sent."""
    noon = datetime(2026, 11, 25, 12, 40, tzinfo=bot.KST)
    moment = {"when": noon}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            current = moment["when"]
            if tz is None:
                return current.replace(tzinfo=None)
            return current.astimezone(tz)

    book = _user_book()
    opened = {"n": 0}
    sent = []
    saved = {}
    original_datetime = bot.datetime
    original_inquiry = bot._inquiry
    original_token = bot._load_token
    original_state = bot._load_state
    original_post_order = bot._post_order
    original_save = bot._save_json
    original_bearer = gb._post_bearer

    def inquiry(_account, _now, kind):
        if kind == "fill":
            return [_fill_30(), _manual_buy()]
        opened["n"] += 1
        if opened["n"] == 1:
            return book
        return []

    def post_order(_url, body):
        sent.append(
            {
                "cancel": body.get("org_orr_no"),
                "qty": body.get("orr_qty"),
                "price": body.get("fc_orr_uit_pr"),
            }
        )
        return True

    def bearer(url, _body, _extra):
        if "acctinfo" not in url:
            return 500, {}, {}
        return 200, {}, {"Output_0": [{"acct_type": "01", "acct_no": "20102999991"}]}

    os.environ["LADDER_MODE"] = "soxl-rebuild"
    os.environ["GITHUB_EVENT_NAME"] = "schedule"
    os.environ["SOXL_REBUILD_LIVE"] = "on"
    os.environ.pop("LIVE_TRADING", None)
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("NHPLUG_ACCOUNT_SUFFIX", None)
    os.environ["NHPLUG_APP_KEY"] = "test-key"
    os.environ["NHPLUG_APP_SECRET"] = "test-secret"
    bot._now_kst = lambda: moment["when"]
    bot.datetime = _Clock
    bot._load_token = lambda: "token"
    bot._load_state = _state
    bot._inquiry = inquiry
    bot._post_order = post_order
    bot._save_json = lambda _path, data: saved.__setitem__("data", data)
    gb._post_bearer = bearer
    try:
        assert bot._cancel(
            "20102999991", "SOXL", {"idx": None, "orr_no": 75, "rebuild_cancel": True}, True
        ) is True
        assert bot._cancel("20102999991", "SOXL", {"idx": None, "orr_no": 76}, True) is None
        sent.clear()
        code, out = sim._capture(bot._main_once)
        assert code == (0, False), out
        assert "ERROR unexpected cancel" not in out
        assert "SOXL plan cancel sell 39 qty 4" in out
        assert "OK live" in out
        assert "20102999991" not in out
        cancelled = [item["cancel"] for item in sent if item["cancel"] is not None]
        assert 39 in cancelled
        assert 75 in cancelled
        assert 187 not in cancelled
        assert "SOXL plan ignore buy 18.7 qty 400" in out
        assert sum(1 for item in sent if item["price"] == 18.7) == 0
        assert sum(1 for item in sent if item["price"] == 39.0 and item["qty"] == 20) == 1
        assert saved["data"]["tickers"]["SOXL"]["sell"] == _level(36)
        assert saved["data"]["tickers"]["SOXL"]["buy"] == _level(30)
        assert "ef" * 32 in saved["data"]["handled"]
    finally:
        bot.datetime = original_datetime
        bot._inquiry = original_inquiry
        bot._load_token = original_token
        bot._load_state = original_state
        bot._post_order = original_post_order
        bot._save_json = original_save
        gb._post_bearer = original_bearer
        os.environ.pop("LADDER_MODE", None)
        os.environ.pop("GITHUB_EVENT_NAME", None)
        os.environ.pop("NHPLUG_APP_KEY", None)
        os.environ.pop("NHPLUG_APP_SECRET", None)


def test_evening_reset_keeps_the_rebuilt_soxl_book():
    """18:11 keeps sell 20@36, sell 20@39, buy 20@30, buy 20@28."""
    when = datetime(2026, 11, 25, 18, 11, tzinfo=bot.KST)
    assert when.strftime("%A") == "Wednesday"
    assert bot._reset_slot(when) == "17"
    assert not bot._orders_blocked(when)
    state = _state()
    state["handled"] = [_KEY]
    opens = [
        {"ticker": "SOXL", "side": "sell", "price": 36, "qty": 20, "orr_no": 1},
        {"ticker": "SOXL", "side": "sell", "price": 39, "qty": 20, "orr_no": 2},
        {"ticker": "SOXL", "side": "buy", "price": 30, "qty": 20, "orr_no": 3},
        {"ticker": "SOXL", "side": "buy", "price": 28, "qty": 20, "orr_no": 4},
    ]
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "SOXL"
    os.environ.pop("SOXL_REBUILD_LIVE", None)
    os.environ.pop("ORDER_TYPE", None)
    code, out, world = sim._run(
        state, opens, {}, fills=[_fill_30()], mode="reset", when=when
    )
    assert code == 0, out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert world["exceptions"] == [(400, 18.7)]
    assert "exception placed 1" in out
    kept = world["saved"]["tickers"]["SOXL"]
    assert kept["sell"] == _level(36)
    assert kept["buy"] == _level(30)
    code, out, intra = sim._run(
        state, opens, {}, fills=[_fill_30()], mode="intraday", when=datetime(2026, 11, 25, 12, 40, tzinfo=bot.KST)
    )
    assert code == 0, out
    assert intra["placed"] == []
    assert intra["cancelled"] == []
    assert "18.7" not in out


def test_live_trading_alone_stays_dry():
    os.environ["LIVE_TRADING"] = "on"
    os.environ.pop("SOXL_REBUILD_LIVE", None)
    code, out, world = _go(_state(), _opens(), [], live=False)
    assert code == 0, out
    assert "SOXL anchor sell 36 buy 30" in out
    assert "SOXL plan place buy 30 qty 20" in out
    assert "SOXL plan place sell 36 qty 20" in out
    assert "OK dry" in out
    assert world["placed"] == []
    assert world["cancelled"] == []


def main():
    original = bot._now_kst
    try:
        test_flat_twenty_and_no_double()
        test_buy_400_at_18_7_is_a_reset_exception()
        test_last_sell_from_36_is_103_for_15()
        test_dry_rebuild_keeps_36_30_despite_the_30_fill()
        test_live_rebuild_cancels_only_soxl_ladder_orders()
        test_later_reset_and_intraday_keep_36_30()
        test_scheduled_runs_leave_the_old_book()
        test_live_clocked_rebuild_at_1240_reaches_runner()
        test_extra_sell_39_is_cancelled_and_anchor_stays()
        test_reinquire_does_not_double_place()
        test_live_rebuild_cancels_without_live_trading()
        test_evening_reset_keeps_the_rebuilt_soxl_book()
        test_live_trading_alone_stays_dry()
        print("OK soxl rebuild")
    finally:
        bot._now_kst = original
        os.environ.pop("SOXL_REBUILD_LIVE", None)
        os.environ.pop("LIVE_TRADING", None)
        os.environ.pop("ORDER_TYPE", None)
        os.environ.pop("MAX_QTY_MULT", None)
        os.environ.pop("LIVE_TICKERS", None)


if __name__ == "__main__":
    main()
