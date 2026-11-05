"""Simulation for the reset cap and SEED_FORCE. No broker calls."""

import io
import json
import os
import sys
import tempfile
from datetime import datetime
from decimal import Decimal

import ladder_bot as bot
import ladder_dryrun as ladders
import seed_state

_FIXED = datetime(2026, 10, 29, 17, 15, tzinfo=bot.KST)
_TODAY = "20261029"
_HANDLED = "ab" * 32
_ORIG_NOW = bot._now_kst
_ORIG_STATE = bot._STATE_PATH


def _level(ticker, price):
    return ladders._level_of(ticker, price)


def _pair(ticker, sell_px, buy_px, double_used=()):
    return {
        "sell": _level(ticker, sell_px),
        "buy": _level(ticker, buy_px),
        "double_used": list(double_used),
    }


def _orders(ticker, sell_px, buy_px):
    desired = bot._desired(ticker, _level(ticker, sell_px), _level(ticker, buy_px), 1.0)
    assert desired
    return [(item["side"], item["price"], item["qty"]) for item in desired]


def _tagged(ticker, sell_px, buy_px):
    return [(ticker, side, price, qty) for side, price, qty in _orders(ticker, sell_px, buy_px)]


def _capture(fn):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        code = fn()
    finally:
        sys.stdout = old
    return code, buf.getvalue()


def _actions(world):
    def place(ticker, item, counter):
        if item["qty"] > bot._HARD_MAX[ticker] or item["qty"] < 1:
            return None
        if counter[0] >= bot._MAX_NEW:
            return None
        counter[0] += 1
        world["placed"].append((ticker, item["side"], item["price"], item["qty"]))
        return True

    def cancel(ticker, order):
        world["cancelled"].append((ticker, order.get("price"), order.get("orr_no")))
        return True

    def exists(ticker, side, index):
        checker = world.get("exists")
        if checker is None:
            return False
        return checker(ticker, side, index)

    def confirm_clear(_ticker):
        return True

    def exception_open():
        return world.get("exception_open", False)

    def place_exception(counter):
        if counter[0] >= bot._MAX_NEW:
            return None
        counter[0] += 1
        world["exceptions"].append((100, 75))
        world["exception_open"] = True
        return True

    def save_state(data):
        world["saved"] = json.loads(json.dumps(data))

    return {
        "place": place,
        "cancel": cancel,
        "exists": exists,
        "confirm_clear": confirm_clear,
        "exception_open": exception_open,
        "place_exception": place_exception,
        "save_state": save_state,
    }


def _run(state, opens, world, event="workflow_dispatch", when=None):
    moment = when or _FIXED
    bot._now_kst = lambda: moment
    world["placed"] = []
    world["cancelled"] = []
    world["exceptions"] = []
    actions = _actions(world)
    code, out = _capture(
        lambda: bot.run(
            "reset", event, moment, moment, opens, [], state, actions
        )
    )
    return code, out, world


def _strip_maps(state):
    """Old intraday saves drop reset_tickers and keep handled markers."""
    clone = json.loads(json.dumps(state))
    clone["reset_tickers"] = {"09": {}, "17": {}}
    return clone


def test_pairs_and_limits():
    assert _level("KORU", 13.5) == 7
    assert _level("SOXL", 13.5) == 4
    assert _level("KORU", 11.3) == 5
    assert _level("SOXL", 113) == 28
    assert _level("SOXL", 94) == 26
    assert _level("NAIL", 33) == 14
    assert _level("NAIL", 28) == 12
    assert _orders("NAIL", 33, 28) == [
        ("sell", 33, 10),
        ("sell", 36, 10),
        ("buy", 28, 10),
        ("buy", 26, 10),
    ]
    assert _orders("KORU", 13.5, 11.3) == [
        ("sell", 13.5, 1),
        ("sell", 15, 1),
        ("buy", 11.3, 1),
        ("buy", 10.3, 1),
    ]
    assert _orders("SOXL", 113, 94) == [
        ("sell", 113, 2),
        ("sell", 125, 2),
        ("buy", 94, 1),
        ("buy", 86, 1),
    ]
    assert _orders("KORU", 12.5, 10.3)[0] == ("sell", 12.5, 1)
    assert bot._HARD_MAX == {"NAIL": 40, "KORU": 4, "SOXL": 4}
    assert bot._MAX_NEW == 6
    assert bot._sized("NAIL", 20, 1) == 20
    assert bot._sized("NAIL", 40, 1) == 40
    assert bot._sized("NAIL", 41, 1) is None
    assert bot._sized("KORU", 4, 1) == 4
    assert bot._sized("KORU", 5, 1) is None
    assert bot._sized("SOXL", 4, 1) == 4
    assert bot._sized("SOXL", 5, 1) is None
    assert bot._orders_blocked(datetime(2026, 10, 29, 8, 30, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 29, 9, 9, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 9, 10, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 29, 16, 0, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 29, 17, 9, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 17, 10, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 17, 11, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 31, 17, 15, tzinfo=bot.KST))
    sat = datetime(2026, 10, 31, tzinfo=bot.KST)
    assert not bot._orders_blocked(sat.replace(hour=0, minute=30))
    assert not bot._orders_blocked(sat.replace(hour=3, minute=0))
    assert not bot._orders_blocked(sat.replace(hour=7, minute=30))
    assert bot._orders_blocked(sat.replace(hour=9, minute=0))
    assert bot._orders_blocked(sat.replace(hour=12, minute=0))
    assert bot._orders_blocked(datetime(2026, 11, 1, 3, 0, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 11, 1, 12, 0, tzinfo=bot.KST))
    assert bot._reset_slot(datetime(2026, 10, 29, 17, 11, tzinfo=bot.KST)) == "17"
    assert bot._reset_slot(datetime(2026, 10, 29, 9, 10, tzinfo=bot.KST)) == "09"


def test_batch_selector():
    plans = [
        {"ticker": "NAIL", "review": False, "place": [0] * 4},
        {"ticker": "KORU", "review": False, "place": [0] * 4},
        {"ticker": "SOXL", "review": False, "place": [0] * 4},
    ]
    allow = {"NAIL", "KORU", "SOXL"}
    state = bot._fresh_state()
    first = bot._reset_batch(plans, allow, state, "17", _TODAY, True)
    assert first["selected"] == {"NAIL"}
    assert first["deferred"] == {"KORU", "SOXL"}
    assert first["exception"] is False
    assert first["blocked"] is False
    bot._mark_slot_done(state, "17", "NAIL", _TODAY)
    second = bot._reset_batch(plans, allow, state, "17", _TODAY, True)
    assert second["selected"] == {"KORU"}
    assert second["exception"] is False
    bot._mark_slot_done(state, "17", "KORU", _TODAY)
    third = bot._reset_batch(plans, allow, state, "17", _TODAY, True)
    assert third["selected"] == {"SOXL"}
    assert third["deferred"] == set()
    assert third["exception"] is True
    huge = [dict(plan) for plan in plans]
    huge[0] = {"ticker": "NAIL", "review": False, "place": [0] * 7}
    blocked = bot._reset_batch(huge, allow, bot._fresh_state(), "17", _TODAY, True)
    assert blocked["blocked"] is True


def test_three_resets():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "NAIL": _pair("NAIL", 33, 28),
                "KORU": _pair("KORU", 13.5, 11.3),
                "SOXL": _pair("SOXL", 113, 94),
            },
            "handled": [_HANDLED],
            "exception_filled": False,
            "reset_done": {"09": "", "17": ""},
        }
    )
    world = {}
    code, out, world = _run(state, [], world)
    assert code == 0, out
    assert world["placed"] == _tagged("NAIL", 33, 28)
    assert world["cancelled"] == []
    assert world["exceptions"] == []
    assert "DEFERRED KORU" in out
    assert "DEFERRED SOXL" in out
    assert "DEFERRED exception" in out
    assert "ERROR max_orders" not in out
    assert "OK live" in out
    saved = world["saved"]
    assert saved["reset_tickers"]["17"]["NAIL"] == _TODAY
    assert "KORU" not in saved["reset_tickers"]["17"]
    assert saved["reset_done"]["17"] == ""
    assert _HANDLED in saved["handled"]
    assert bot._done_marker("17", "NAIL", _TODAY) in saved["handled"]

    code, out, world = _run(_strip_maps(saved), [], world)
    assert code == 0, out
    assert world["placed"] == _tagged("KORU", 13.5, 11.3)
    assert world["exceptions"] == []
    assert world["cancelled"] == []
    assert "DEFERRED SOXL" in out
    assert "NAIL skip done" in out
    assert "ERROR" not in out
    saved = world["saved"]
    assert saved["reset_tickers"]["17"]["NAIL"] == _TODAY
    assert saved["reset_tickers"]["17"]["KORU"] == _TODAY
    assert saved["reset_done"]["17"] == ""

    code, out, world = _run(_strip_maps(saved), [], world)
    assert code == 0, out
    assert world["placed"] == _tagged("SOXL", 113, 94)
    assert world["exceptions"] == [(100, 75)]
    assert "exception placed 1" in out
    assert "ERROR" not in out
    saved = world["saved"]
    assert saved["reset_done"]["17"] == _TODAY
    assert saved["reset_tickers"]["17"]["SOXL"] == _TODAY
    assert saved["exception_filled"] is False
    assert _HANDLED in saved["handled"]
    assert saved["tickers"]["KORU"]["sell"] == 7
    assert saved["tickers"]["SOXL"]["sell"] == 28

    code, out, world = _run(saved, [], world)
    assert code == 0, out
    assert world["placed"] == []
    assert world["exceptions"] == []
    assert world["cancelled"] == []
    assert "OK skip" in out
    assert "saved" not in world or world["saved"]["reset_done"]["17"] == _TODAY


def test_deferred_not_cancelled_and_exception_survives():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "NAIL": _pair("NAIL", 33, 28),
                "KORU": _pair("KORU", 13.5, 11.3),
                "SOXL": _pair("SOXL", 113, 94),
            },
            "handled": [],
            "exception_filled": False,
            "reset_done": {"09": "", "17": ""},
        }
    )
    opens = [
        {"ticker": "NAIL", "side": "sell", "price": 33, "qty": 10, "orr_no": 1},
        {"ticker": "KORU", "side": "buy", "price": 10.3, "qty": 1, "orr_no": 2},
        {"ticker": "SOXL", "side": "buy", "price": 75, "qty": 100, "orr_no": 75},
    ]
    world = {}
    code, out, world = _run(state, opens, world)
    assert code == 0, out
    assert [item[0] for item in world["cancelled"]] == ["NAIL"]
    assert all(item[2] != 75 for item in world["cancelled"])
    assert [item[0] for item in world["placed"]] == ["NAIL", "NAIL", "NAIL", "NAIL"]
    assert world["exceptions"] == []

    done = world["saved"]
    # SOXL's turn. The 75 buy is not a ladder rung, so the reset must not cancel it.
    code, _out, world = _run(done, opens, world)
    assert code == 0
    code, out, world = _run(world["saved"], opens, world)
    assert code == 0, out
    assert world["cancelled"] == []
    assert world["placed"] == _tagged("SOXL", 113, 94)
    assert world["exceptions"] == []
    assert "exception placed" not in out


def test_exists_skips_duplicates():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "NAIL": _pair("NAIL", 33, 28),
                "KORU": _pair("KORU", 13.5, 11.3),
                "SOXL": _pair("SOXL", 113, 94),
            },
            "handled": [],
            "exception_filled": False,
            "reset_done": {"09": "", "17": ""},
        }
    )

    def exists(ticker, _side, _index):
        return ticker == "NAIL"

    world = {"exists": exists}
    code, out, world = _run(state, [], world)
    assert code == 0, out
    assert world["placed"] == []
    assert world["saved"]["reset_tickers"]["17"]["NAIL"] == _TODAY
    assert "DEFERRED KORU" in out


def test_intraday_unchanged():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "NAIL": _pair("NAIL", 33, 28),
                "KORU": _pair("KORU", 13.5, 11.3),
                "SOXL": _pair("SOXL", 113, 94),
            },
            "handled": [_HANDLED],
            "exception_filled": True,
            "reset_done": {"09": "", "17": ""},
        }
    )
    opens = [
        {"ticker": "NAIL", "side": "sell", "price": 33, "qty": 10},
        {"ticker": "NAIL", "side": "sell", "price": 36, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 28, "qty": 10},
        {"ticker": "NAIL", "side": "buy", "price": 26, "qty": 10},
    ]
    world = {}
    moment = _FIXED
    bot._now_kst = lambda: moment
    actions = _actions(world)
    world["placed"] = []
    world["cancelled"] = []
    world["exceptions"] = []
    code, out = _capture(
        lambda: bot.run("intraday", "workflow_dispatch", moment, moment, opens, [], state, actions)
    )
    assert code == 0, out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert world["exceptions"] == []
    assert "DEFERRED" not in out
    assert "ERROR" not in out
    assert world["saved"]["reset_done"]["17"] == ""
    assert "NAIL" not in world["saved"]["reset_tickers"]["17"]
    assert _HANDLED in world["saved"]["handled"]


def test_push_and_time_guard_place_nothing():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "NAIL": _pair("NAIL", 33, 28),
                "KORU": _pair("KORU", 13.5, 11.3),
                "SOXL": _pair("SOXL", 113, 94),
            },
            "handled": [],
            "exception_filled": False,
            "reset_done": {"09": "", "17": ""},
        }
    )
    world = {}
    code, out, world = _run(state, [], world, event="push")
    assert code == 0, out
    assert world["placed"] == []
    assert world["exceptions"] == []
    assert "saved" not in world
    assert "ERROR max_orders" not in out
    assert "OK dry" in out
    blocked = datetime(2026, 10, 29, 16, 30, tzinfo=bot.KST)
    code, out, world = _run(state, [], world, when=blocked)
    assert code == 0, out
    assert world["placed"] == []
    assert "skip: time guard" in out


def _write_state(path, state):
    bot._STATE_PATH = path
    bot._save_json(path, bot._clean_state(state))


def _read_state(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def test_seed_force():
    os.environ.pop("SEED_FLAGS", None)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.json")
        handled = "cd" * 32
        _write_state(
            path,
            {
                "version": 2,
                "tickers": {
                    "NAIL": _pair("NAIL", 33, 28),
                    "KORU": _pair("KORU", 12.5, 10.3, [4]),
                    "SOXL": _pair("SOXL", 103, 86),
                },
                "handled": [handled],
                "exception_filled": True,
                "reset_done": {"09": "20261029", "17": ""},
            },
        )
        before = open(path, "rb").read()
        os.environ["SEED_FORCE"] = "KORU:999:1"
        code, out = _capture(seed_state.main)
        assert code == 1, out
        assert "ERROR config" in out
        assert open(path, "rb").read() == before

        os.environ["SEED_FORCE"] = "KORU:13.5:11.3,KORU:15:12.5"
        code, out = _capture(seed_state.main)
        assert code == 1, out
        assert open(path, "rb").read() == before

        os.environ["SEED_FORCE"] = "KORU:13.5:11.3,SOXL:113:94"
        code, out = _capture(seed_state.main)
        assert code == 0, out
        assert "OK force" in out
        forced = _read_state(path)
        assert forced["tickers"]["NAIL"] == {
            "sell": 14,
            "buy": 12,
            "double_used": [],
        }
        assert forced["tickers"]["KORU"]["sell"] == 7
        assert forced["tickers"]["KORU"]["buy"] == 5
        assert forced["tickers"]["KORU"]["double_used"] == [4]
        assert forced["tickers"]["SOXL"]["sell"] == 28
        assert forced["tickers"]["SOXL"]["buy"] == 26
        assert handled in forced["handled"]
        assert forced["exception_filled"] is True
        assert forced["reset_done"]["09"] == "20261029"

        os.environ["SEED_FORCE"] = ""
        code, out = _capture(seed_state.main)
        assert code == 0, out
        assert "OK skip" in out
        assert _read_state(path)["tickers"]["KORU"]["sell"] == 7

        os.remove(path)
        os.environ["SEED_FORCE"] = "KORU:13.5:11.3"
        code, out = _capture(seed_state.main)
        assert code == 0, out
        fresh = _read_state(path)
        assert fresh["tickers"]["NAIL"]["sell"] == 14
        assert fresh["tickers"]["KORU"]["sell"] == 7
        assert fresh["tickers"]["SOXL"]["sell"] == _level("SOXL", 103)

        broken = os.path.join(tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        bot._STATE_PATH = broken
        os.environ["SEED_FORCE"] = "SOXL:113:94"
        code, out = _capture(seed_state.main)
        assert code == 1, out
        assert open(broken, encoding="utf-8").read() == "{not json"


def test_seed_flags():
    os.environ.pop("SEED_FORCE", None)
    os.environ.pop("SEED_FLAGS", None)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.json")
        handled = "ef" * 32
        _write_state(
            path,
            {
                "version": 2,
                "tickers": {
                    "NAIL": _pair("NAIL", 33, 28),
                    "KORU": _pair("KORU", 12.5, 10.3, [4]),
                    "SOXL": _pair("SOXL", 103, 86),
                },
                "handled": [handled],
                "exception_filled": False,
                "reset_done": {"09": "20261029", "17": ""},
            },
        )
        before = open(path, "rb").read()
        os.environ["SEED_FLAGS"] = "exception_filled=yes"
        code, out = _capture(seed_state.main)
        assert code == 1, out
        assert "ERROR config" in out
        assert open(path, "rb").read() == before

        os.environ["SEED_FLAGS"] = "SOXL.double_used=999"
        code, out = _capture(seed_state.main)
        assert code == 1, out
        assert open(path, "rb").read() == before

        os.environ["SEED_FLAGS"] = "exception_filled=true;SOXL.double_used=72"
        orders = {"n": 0}

        def _boom(*_args, **_kwargs):
            orders["n"] += 1
            raise AssertionError("seed placed an order")

        old_place = bot._place
        old_post = bot._post_order
        bot._place = _boom
        bot._post_order = _boom
        try:
            code, out = _capture(seed_state.main)
        finally:
            bot._place = old_place
            bot._post_order = old_post
        assert code == 0, out
        assert orders["n"] == 0
        assert "OK flags" in out
        assert "placed" not in out
        marked = _read_state(path)
        assert marked["exception_filled"] is True
        assert marked["tickers"]["SOXL"]["double_used"] == [_level("SOXL", 72)]
        assert marked["tickers"]["SOXL"]["sell"] == _level("SOXL", 103)
        assert marked["tickers"]["KORU"]["double_used"] == [4]
        assert marked["tickers"]["NAIL"]["sell"] == _level("NAIL", 33)
        assert handled in marked["handled"]
        assert marked["reset_done"]["09"] == "20261029"

        os.environ["SEED_FORCE"] = "KORU:13.5:11.3"
        os.environ["SEED_FLAGS"] = "exception_filled=false;SOXL.double_used=72,66;KORU.double_used="
        code, out = _capture(seed_state.main)
        assert code == 0, out
        assert "OK force" in out
        assert orders["n"] == 0
        both = _read_state(path)
        assert both["exception_filled"] is False
        assert both["tickers"]["KORU"]["sell"] == _level("KORU", 13.5)
        assert both["tickers"]["KORU"]["buy"] == _level("KORU", 11.3)
        assert both["tickers"]["KORU"]["double_used"] == []
        assert both["tickers"]["SOXL"]["double_used"] == [
            _level("SOXL", 72),
            _level("SOXL", 66),
        ]
        assert both["tickers"]["NAIL"]["double_used"] == []
        assert handled in both["handled"]

        os.environ["SEED_FORCE"] = ""
        os.environ["SEED_FLAGS"] = "QQQ.double_used=72"
        snapshot = open(path, "rb").read()
        code, out = _capture(seed_state.main)
        assert code == 1, out
        assert open(path, "rb").read() == snapshot

        only_nail = os.path.join(tmp, "nail.json")
        _write_state(
            only_nail,
            {
                "version": 2,
                "tickers": {"NAIL": _pair("NAIL", 33, 28)},
                "handled": [],
                "exception_filled": False,
                "reset_done": {"09": "", "17": ""},
            },
        )
        nail_before = open(only_nail, "rb").read()
        os.environ["SEED_FLAGS"] = "SOXL.double_used=72"
        code, out = _capture(seed_state.main)
        assert code == 1, out
        assert open(only_nail, "rb").read() == nail_before

        bot._STATE_PATH = path
        os.remove(path)
        os.environ["SEED_FORCE"] = ""
        os.environ["SEED_FLAGS"] = "exception_filled=true;NAIL.double_used=22"
        code, out = _capture(seed_state.main)
        assert code == 0, out
        assert "OK flags" in out
        created = _read_state(path)
        assert created["exception_filled"] is True
        assert created["tickers"]["NAIL"]["double_used"] == [_level("NAIL", 22)]
        assert created["tickers"]["KORU"]["sell"] == _level("KORU", 12.5)
        assert created["tickers"]["SOXL"]["double_used"] == []


def test_reset_low_rung_sizes():
    koru = ladders._CFG["KORU"]["ladder"]
    assert koru[2] == 8.6
    assert 8.5 not in koru
    assert bot._buy_computed("SOXL", _level("SOXL", 72), []) == 4
    assert bot._buy_computed("SOXL", _level("SOXL", 66), []) == 4
    assert bot._buy_computed("SOXL", _level("SOXL", 60), []) == 4
    assert bot._buy_computed("SOXL", _level("SOXL", 79), []) == 1
    assert bot._buy_computed("SOXL", _level("SOXL", 66), [_level("SOXL", 66)]) == 1
    assert bot._buy_computed("SOXL", _level("SOXL", 72), [_level("SOXL", 72)]) == 1
    assert bot._buy_computed("KORU", _level("KORU", 8.6), []) == 2
    assert bot._buy_computed("KORU", _level("KORU", 9.4), []) == 2
    assert bot._buy_computed("NAIL", _level("NAIL", 24), []) == 10
    assert bot._buy_computed("NAIL", _level("NAIL", 22), []) == 20
    assert bot._buy_computed("NAIL", _level("NAIL", 20), []) == 40
    assert bot._buy_computed("NAIL", _level("NAIL", 18), []) == 40
    assert bot._buy_computed("NAIL", _level("NAIL", 16.5), []) == 40
    assert bot._buy_computed("NAIL", _level("NAIL", 30), []) == 10
    assert bot._buy_computed("NAIL", _level("NAIL", 22), [_level("NAIL", 22)]) == 10
    assert bot._buy_computed("NAIL", _level("NAIL", 20), [_level("NAIL", 20)]) == 10

    cases = [
        (
            "SOXL",
            79,
            66,
            [_level("SOXL", 72)],
            [
                ("sell", 79, 2),
                ("sell", 86, 2),
                ("buy", 66, 4),
                ("buy", 60, 4),
            ],
        ),
        (
            "SOXL",
            86,
            72,
            [_level("SOXL", 72), _level("SOXL", 66)],
            [
                ("sell", 86, 2),
                ("sell", 94, 2),
                ("buy", 72, 1),
                ("buy", 66, 1),
            ],
        ),
        (
            "KORU",
            11.3,
            9.4,
            [],
            [
                ("sell", 11.3, 1),
                ("sell", 12.5, 1),
                ("buy", 9.4, 2),
                ("buy", 8.6, 2),
            ],
        ),
        (
            "NAIL",
            36,
            30,
            [],
            [
                ("sell", 36, 10),
                ("sell", 39, 10),
                ("buy", 30, 10),
                ("buy", 28, 10),
            ],
        ),
        (
            "NAIL",
            26,
            22,
            [],
            [
                ("sell", 26, 10),
                ("sell", 28, 10),
                ("buy", 22, 20),
                ("buy", 20, 40),
            ],
        ),
        (
            "NAIL",
            26,
            22,
            [_level("NAIL", 20)],
            [
                ("sell", 26, 10),
                ("sell", 28, 10),
                ("buy", 22, 20),
                ("buy", 20, 10),
            ],
        ),
    ]
    os.environ["LIVE_TRADING"] = "on"
    for ticker, sell_px, buy_px, used, expected in cases:
        got = bot._desired(
            ticker, _level(ticker, sell_px), _level(ticker, buy_px), 1.0, used
        )
        assert [(item["side"], item["price"], item["qty"]) for item in got] == expected
        os.environ["LIVE_TICKERS"] = ticker
        state = bot._clean_state(
            {
                "version": 2,
                "tickers": {ticker: _pair(ticker, sell_px, buy_px, used)},
                "handled": [],
                "exception_filled": True,
                "reset_done": {"09": "", "17": ""},
            }
        )
        code, out, world = _run(state, [], {})
        assert code == 0, out
        assert world["placed"] == [
            (ticker, side, price, qty) for side, price, qty in expected
        ], (ticker, world["placed"], out)
        assert world["cancelled"] == []
        assert world["exceptions"] == []
        assert "exception placed" not in out


def _book(orders):
    return [{"side": side, "price": price, "qty": qty} for side, price, qty in orders]


def test_soxl_sell_qty_and_buy_rules():
    """SOXL sells are 2 everywhere. Buys stay on the existing rule."""
    cfg = ladders._CFG["SOXL"]
    ladder = cfg["ladder"]
    assert cfg["base"] == 1
    assert cfg["sell_qty"] == 2
    assert ladders._sell_shares(cfg) == 2
    assert ladders._sell_shares(ladders._CFG["NAIL"]) == 10
    assert ladders._sell_shares(ladders._CFG["KORU"]) == 1
    assert "sell_qty" not in ladders._CFG["NAIL"]
    assert "sell_qty" not in ladders._CFG["KORU"]

    used = [_level("SOXL", price) for price in (72, 66, 60, 55)]
    for sell_idx in range(2, len(ladder) - 1):
        buy_idx = sell_idx - 1
        for flags, label in (((), "first"), (used, "used")):
            desired = bot._desired("SOXL", sell_idx, buy_idx, 1.0, flags)
            assert desired, (label, sell_idx)
            for item in desired:
                price = item["price"]
                if item["side"] == "sell":
                    assert item["qty"] == 2, (label, item)
                    continue
                boosted = abs(price - 72) < 0.051 or price <= 66
                if boosted and item["idx"] in set(flags):
                    assert item["qty"] == 1, (label, item)
                elif boosted:
                    assert item["qty"] == 4, (label, item)
                else:
                    assert item["qty"] == 1, (label, item)

    added = bot.intraday_adds(
        "SOXL",
        [
            {"side": "sell", "price": 94, "qty": 1},
            {"side": "sell", "price": 103, "qty": 1},
            {"side": "buy", "price": 79, "qty": 1},
        ],
        [{"side": "buy", "idx": _level("SOXL", 86)}],
        1.0,
    )
    assert [(item["side"], item["price"], item["qty"]) for item in added] == [
        ("buy", 72, 4),
        ("sell", 86, 2),
    ]
    nail_added = bot.intraday_adds(
        "NAIL",
        [
            {"side": "sell", "price": 33, "qty": 10},
            {"side": "sell", "price": 36, "qty": 10},
            {"side": "buy", "price": 26, "qty": 10},
        ],
        [{"side": "buy", "idx": _level("NAIL", 28)}],
        1.0,
    )
    assert [(item["side"], item["price"], item["qty"]) for item in nail_added] == [
        ("buy", 24, 10),
        ("sell", 30, 10),
    ]
    koru_added = bot.intraday_adds(
        "KORU",
        [
            {"side": "sell", "price": 12.5, "qty": 1},
            {"side": "sell", "price": 13.5, "qty": 1},
            {"side": "buy", "price": 10.3, "qty": 1},
        ],
        [{"side": "buy", "idx": _level("KORU", 11.3)}],
        1.0,
    )
    assert [(item["side"], item["price"], item["qty"]) for item in koru_added] == [
        ("buy", 9.4, 2),
        ("sell", 11.3, 1),
    ]

    assert ladders.plan(
        "SOXL",
        _book(
            [
                ("sell", 113, 2),
                ("sell", 125, 2),
                ("buy", 94, 1),
                ("buy", 86, 1),
            ]
        ),
    )["ok"]
    stale = ladders.plan(
        "SOXL",
        _book(
            [
                ("sell", 113, 1),
                ("sell", 125, 1),
                ("buy", 94, 1),
                ("buy", 86, 1),
            ]
        ),
    )
    assert stale["ok"] is False
    assert stale["would_place"] == 2
    assert stale["would_cancel"] == 2
    assert ladders.plan(
        "SOXL",
        _book(
            [
                ("sell", 86, 2),
                ("sell", 94, 2),
                ("buy", 72, 4),
                ("buy", 66, 4),
            ]
        ),
    )["ok"]
    assert not ladders.plan(
        "SOXL",
        _book(
            [
                ("sell", 113, 2),
                ("sell", 125, 2),
                ("buy", 94, 2),
                ("buy", 86, 1),
            ]
        ),
    )["ok"]
    assert ladders.plan(
        "NAIL",
        _book(
            [
                ("sell", 33, 10),
                ("sell", 36, 10),
                ("buy", 28, 10),
                ("buy", 26, 10),
            ]
        ),
    )["ok"]
    assert ladders.plan(
        "NAIL",
        _book(
            [
                ("sell", 26, 10),
                ("sell", 28, 10),
                ("buy", 22, 20),
                ("buy", 20, 40),
            ]
        ),
    )["ok"]
    assert ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 13.5, 1),
                ("sell", 15, 1),
                ("buy", 11.3, 1),
                ("buy", 10.3, 1),
            ]
        ),
    )["ok"]
    assert ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 11.3, 1),
                ("sell", 12.5, 1),
                ("buy", 9.4, 2),
                ("buy", 8.6, 2),
            ]
        ),
    )["ok"]


def test_koru_extends_below_7_2():
    """7.2 is not a floor. 6.5 and the next ~10% rungs are real KORU buys."""
    assert ladders._KORU_LADDER[:4] == [7.2, 7.9, 8.6, 9.4]
    assert ladders._KORU_LADDER[-1] == 72
    assert 6.5 not in ladders._KORU_LADDER
    assert [_level("KORU", price) for price in (7.2, 7.9, 8.6, 9.4, 13.5)] == [
        0,
        1,
        2,
        3,
        7,
    ]
    assert [_level("KORU", price) for price in (6.5, 5.9, 5.4, 4.9, 4.5, 4.1)] == [
        -1,
        -2,
        -3,
        -4,
        -5,
        -6,
    ]
    assert [ladders._price_at("KORU", index) for index in range(-1, -7, -1)] == [
        6.5,
        5.9,
        5.4,
        4.9,
        4.5,
        4.1,
    ]
    assert ladders._level_of("KORU", 6.4) is None
    assert ladders._next_rung(Decimal("1.155"), up=False) == Decimal("1.0")

    assert ladders._NAIL_LADDER[0] == 9.4 and ladders._NAIL_LADDER[-1] == 220
    assert ladders._SOXL_LADDER[0] == 9.4 and ladders._SOXL_LADDER[-1] == 220
    assert ladders._CFG["SOXL"]["sell_qty"] == 2
    assert ladders._CFG["SOXL"]["base"] == 1
    assert "sell_qty" not in ladders._CFG["KORU"]
    assert ladders._price_at("NAIL", -1) is None
    assert ladders._price_at("SOXL", -1) is None
    assert ladders._price_at("SOXL", len(ladders._SOXL_LADDER)) is None
    assert ladders._price_at("NAIL", len(ladders._NAIL_LADDER)) is None
    assert bot._desired("NAIL", _level("NAIL", 11.3), _level("NAIL", 9.4), 1.0) is None
    assert (
        bot._step_buy(
            "NAIL",
            {"sell": _level("NAIL", 11.3), "buy": _level("NAIL", 10.3), "double_used": []},
        )
        is None
    )
    nail_floor = bot.intraday_adds(
        "NAIL",
        [
            {"side": "sell", "price": 10.3, "qty": 10},
            {"side": "sell", "price": 11.3, "qty": 10},
            {"side": "buy", "price": 9.4, "qty": 40},
        ],
        [{"side": "buy", "idx": _level("NAIL", 10.3)}],
        1.0,
    )
    assert nail_floor is None

    assert bot._buy_computed("KORU", _level("KORU", 6.5), []) == 2
    assert bot._buy_computed("KORU", _level("KORU", 5.9), []) == 2
    assert bot._buy_computed("KORU", _level("KORU", 5.4), []) == 2
    assert bot._buy_computed("KORU", _level("KORU", 6.5), [_level("KORU", 6.5)]) == 1
    assert bot._buy_computed("KORU", _level("KORU", 7.2), []) == 2

    at_floor = bot._desired("KORU", _level("KORU", 8.6), _level("KORU", 7.2), 1.0, [])
    assert [(item["side"], item["price"], item["qty"]) for item in at_floor] == [
        ("sell", 8.6, 1),
        ("sell", 9.4, 1),
        ("buy", 7.2, 2),
        ("buy", 6.5, 2),
    ]
    below = bot._desired(
        "KORU",
        _level("KORU", 7.9),
        _level("KORU", 6.5),
        1.0,
        [_level("KORU", 7.2)],
    )
    assert [(item["side"], item["price"], item["qty"]) for item in below] == [
        ("sell", 7.9, 1),
        ("sell", 8.6, 1),
        ("buy", 6.5, 2),
        ("buy", 5.9, 2),
    ]
    used_low = bot._desired(
        "KORU",
        _level("KORU", 7.9),
        _level("KORU", 6.5),
        1.0,
        [_level("KORU", 6.5)],
    )
    assert [(item["side"], item["price"], item["qty"]) for item in used_low] == [
        ("sell", 7.9, 1),
        ("sell", 8.6, 1),
        ("buy", 6.5, 1),
        ("buy", 5.9, 2),
    ]

    pair = {"sell": _level("KORU", 8.6), "buy": _level("KORU", 7.2), "double_used": []}
    stepped_buys = []
    for _ in range(4):
        pair = bot._step_buy("KORU", pair)
        assert pair
        stepped_buys.append(ladders._price_at("KORU", pair["buy"]))
    assert stepped_buys == [6.5, 5.9, 5.4, 4.9]

    added = bot.intraday_adds(
        "KORU",
        [
            {"side": "sell", "price": 8.6, "qty": 1},
            {"side": "sell", "price": 9.4, "qty": 1},
            {"side": "buy", "price": 7.2, "qty": 2},
        ],
        [{"side": "buy", "idx": _level("KORU", 7.9)}],
        1.0,
    )
    assert [(item["side"], item["price"], item["qty"]) for item in added] == [
        ("buy", 6.5, 2),
        ("sell", 7.9, 1),
    ]
    further = bot.intraday_adds(
        "KORU",
        [
            {"side": "sell", "price": 7.9, "qty": 1},
            {"side": "sell", "price": 8.6, "qty": 1},
            {"side": "buy", "price": 6.5, "qty": 2},
        ],
        [{"side": "buy", "idx": _level("KORU", 7.2)}],
        1.0,
    )
    assert [(item["side"], item["price"], item["qty"]) for item in further] == [
        ("buy", 5.9, 2),
        ("sell", 7.2, 1),
    ]

    missing = ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 8.6, 1),
                ("sell", 9.4, 1),
                ("buy", 7.2, 2),
            ]
        ),
    )
    assert missing["ignored"] == 0
    assert missing["ok"] is False
    assert missing["would_place"] == 1
    assert ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 8.6, 1),
                ("sell", 9.4, 1),
                ("buy", 7.2, 2),
                ("buy", 6.5, 2),
            ]
        ),
    )["ok"]
    odd = ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 8.6, 1),
                ("sell", 9.4, 1),
                ("buy", 7.2, 2),
                ("buy", 6.4, 2),
            ]
        ),
    )
    assert odd["ignored"] == 1
    assert odd["would_place"] == 1
    assert ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 7.9, 1),
                ("sell", 8.6, 1),
                ("buy", 6.5, 2),
                ("buy", 5.9, 2),
            ]
        ),
    )["ok"]
    assert not ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 7.9, 1),
                ("sell", 8.6, 1),
                ("buy", 6.5, 1),
                ("buy", 5.9, 2),
            ]
        ),
    )["ok"]

    flags = seed_state._parse_flags("KORU.double_used=6.5,7.2")
    assert flags[1]["KORU"] == [-1, 0]
    assert seed_state._parse_flags("KORU.double_used=6.4") is None
    assert seed_state._parse_force("KORU:7.9:6.5")["KORU"] == (
        _level("KORU", 7.9),
        _level("KORU", 6.5),
    )
    assert seed_state._parse_force("NAIL:11.3:9.4") is None

    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "KORU"
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {"KORU": _pair("KORU", 8.6, 7.2)},
            "handled": [],
            "exception_filled": True,
            "reset_done": {"09": "", "17": ""},
        }
    )
    assert state["tickers"]["KORU"]["sell"] == 2
    assert state["tickers"]["KORU"]["buy"] == 0
    morning = datetime(2026, 10, 29, 9, 10, tzinfo=bot.KST)
    code, out, world = _run(state, [], {}, when=morning)
    assert code == 0, out
    assert "order type 63" in out
    assert world["placed"] == [
        ("KORU", "sell", 8.6, 1),
        ("KORU", "sell", 9.4, 1),
        ("KORU", "buy", 7.2, 2),
        ("KORU", "buy", 6.5, 2),
    ]
    assert world["cancelled"] == []
    assert "NEEDS_REVIEW KORU" not in out
    assert world["saved"]["tickers"]["KORU"]["buy"] == 0


def test_workflows():
    root = os.path.dirname(os.path.abspath(__file__))
    reset = open(
        os.path.join(root, ".github/workflows/ladder-reset.yml"), encoding="utf-8"
    ).read()
    intra = open(
        os.path.join(root, ".github/workflows/ladder-intraday.yml"), encoding="utf-8"
    ).read()
    seed = open(
        os.path.join(root, ".github/workflows/seed-state.yml"), encoding="utf-8"
    ).read()
    assert "workflow_dispatch:" in reset
    assert '- cron: "10 0 * * 1-5"' in reset
    assert '- cron: "10 8 * * 1-5"' in reset
    assert '- cron: "12 0 * * 1-5"' in reset
    assert '- cron: "16 8 * * 1-5"' in reset
    assert "workflow_dispatch:" in intra
    assert '- cron: "*/5 0-6 * * 1-5"' in intra
    assert '- cron: "*/5 8-22 * * 1-5"' in intra
    assert '- cron: "*/5 0 * * 6"' in intra
    assert '- cron: "*/5 * * * 1-5"' not in intra
    assert "group: ladder-bot" in reset
    assert "group: ladder-bot" in seed
    assert "SEED_FORCE:" in seed
    assert "seed_flags:" in seed
    assert "SEED_FLAGS:" in seed
    assert "exception_filled=true;SOXL.double_used=72" in seed


def main():
    try:
        test_pairs_and_limits()
        test_batch_selector()
        test_three_resets()
        test_deferred_not_cancelled_and_exception_survives()
        test_exists_skips_duplicates()
        test_intraday_unchanged()
        test_push_and_time_guard_place_nothing()
        test_seed_force()
        test_seed_flags()
        test_reset_low_rung_sizes()
        test_soxl_sell_qty_and_buy_rules()
        test_koru_extends_below_7_2()
        test_workflows()
    finally:
        bot._now_kst = _ORIG_NOW
        bot._STATE_PATH = _ORIG_STATE
        os.environ.pop("SEED_FORCE", None)
        os.environ.pop("SEED_FLAGS", None)
    print("OK reset batch")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        bot._now_kst = _ORIG_NOW
        bot._STATE_PATH = _ORIG_STATE
        raise
