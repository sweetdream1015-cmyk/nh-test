"""Simulation for the reset cap and SEED_FORCE. No broker calls."""

import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
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

    def place_exception(counter, qty=None, exc=None):
        if counter[0] >= bot._MAX_NEW:
            return None
        counter[0] += 1
        spec = exc or {
            "id": bot._EXCEPTION_ID,
            "qty": bot._EXCEPTION_QTY,
            "price": bot._EXCEPTION_PRICE,
        }
        if qty is None:
            qty = spec["qty"]
        world["exceptions"].append((qty, spec["price"]))
        if spec.get("id") == bot._EXCEPTION_ID:
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


def _run(
    state,
    opens,
    world,
    event="workflow_dispatch",
    when=None,
    fills=None,
    mode="reset",
    actions=None,
):
    moment = when or _FIXED
    bot._now_kst = lambda: moment
    world["placed"] = []
    world["cancelled"] = []
    world["exceptions"] = []
    if actions is None:
        actions = _actions(world)
    code, out = _capture(
        lambda: bot.run(
            mode,
            event,
            moment,
            moment,
            opens,
            [] if fills is None else fills,
            state,
            actions,
        )
    )
    return code, out, world


def _strip_maps(state):
    """Old intraday saves drop reset_tickers and keep handled markers."""
    clone = json.loads(json.dumps(state))
    clone["reset_tickers"] = {"09": {}, "17": {}}
    return clone


def test_pairs_and_limits():
    assert _level("KORU", 13.5) == _level("SOXL", 13.5) == _level("NAIL", 13.5) == 4
    assert _level("KORU", 11.3) == _level("SOXL", 11.3) == 2
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
        ("sell", 113, 4),
        ("sell", 125, 4),
        ("buy", 94, 4),
        ("buy", 86, 4),
    ]
    assert _orders("KORU", 12.5, 10.3)[0] == ("sell", 12.5, 1)
    assert bot._HARD_MAX == {"NAIL": 40, "KORU": 4, "SOXL": 8}
    assert bot._MAX_NEW == 6
    assert bot._sized("NAIL", 20, 1) == 20
    assert bot._sized("NAIL", 40, 1) == 40
    assert bot._sized("NAIL", 41, 1) is None
    assert bot._sized("KORU", 4, 1) == 4
    assert bot._sized("KORU", 5, 1) is None
    assert bot._sized("SOXL", 4, 1) == 4
    assert bot._sized("SOXL", 8, 1) == 8
    assert bot._sized("SOXL", 9, 1) is None
    # 2026-10-29 is still US daylight time, so the guards are 07:00-08:10
    # and 15:00-16:10 KST (18:00-19:10 and 02:00-03:10 ET).
    assert bot._orders_blocked(datetime(2026, 10, 29, 7, 0, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 29, 8, 9, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 8, 10, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 8, 30, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 9, 9, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 9, 10, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 29, 15, 0, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 29, 16, 0, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 29, 16, 9, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 16, 10, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 17, 9, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 17, 10, tzinfo=bot.KST))
    assert not bot._orders_blocked(datetime(2026, 10, 29, 17, 11, tzinfo=bot.KST))
    assert bot._orders_blocked(datetime(2026, 10, 31, 17, 15, tzinfo=bot.KST))
    sat = datetime(2026, 10, 31, tzinfo=bot.KST)
    assert not bot._orders_blocked(sat.replace(hour=0, minute=30))
    assert not bot._orders_blocked(sat.replace(hour=3, minute=0))
    assert not bot._orders_blocked(sat.replace(hour=6, minute=30))
    assert bot._orders_blocked(sat.replace(hour=7, minute=30))
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
    assert first["exception"] is True
    assert first["blocked"] is False
    bot._mark_slot_done(state, "17", "NAIL", _TODAY)
    second = bot._reset_batch(plans, allow, state, "17", _TODAY, True)
    assert second["selected"] == {"KORU"}
    assert second["deferred"] == {"SOXL"}
    assert second["exception"] is True
    bot._mark_slot_done(state, "17", "KORU", _TODAY)
    third = bot._reset_batch(plans, allow, state, "17", _TODAY, True)
    assert third["selected"] == {"SOXL"}
    assert third["deferred"] == set()
    assert third["exception"] is True
    huge = [dict(plan) for plan in plans]
    huge[0] = {"ticker": "NAIL", "review": False, "place": [0] * 7}
    blocked = bot._reset_batch(huge, allow, bot._fresh_state(), "17", _TODAY, True)
    assert blocked["blocked"] is True
    tight = [
        {"ticker": "NAIL", "review": False, "place": [0] * 6},
        {"ticker": "KORU", "review": False, "place": [0] * 4},
        {"ticker": "SOXL", "review": False, "place": [0] * 4},
    ]
    full = bot._reset_batch(tight, allow, bot._fresh_state(), "17", _TODAY, True)
    assert full["selected"] == {"NAIL"}
    assert full["deferred"] == {"KORU", "SOXL"}
    assert full["exception"] is False
    assert full["exception_take"] == 0
    assert full["blocked"] is False
    short = [
        {"ticker": "NAIL", "review": False, "place": [0] * 5},
        {"ticker": "KORU", "review": False, "place": [0] * 4},
        {"ticker": "SOXL", "review": False, "place": [0] * 4},
    ]
    partial = bot._reset_batch(short, allow, bot._fresh_state(), "17", _TODAY, True, 2)
    assert partial["selected"] == {"NAIL"}
    assert partial["exception_take"] == 1
    assert partial["exception"] is False
    both = bot._reset_batch(plans, allow, bot._fresh_state(), "17", _TODAY, True, 2)
    assert both["exception_take"] == 2
    assert both["exception"] is True


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
    assert world["exceptions"] == [(150, 75), (100, 4.2)]
    assert "DEFERRED KORU" in out
    assert "DEFERRED SOXL" in out
    assert "DEFERRED exception" not in out
    assert "exception placed 2" in out
    assert "FOLLOWUP reset" in out
    assert "ERROR max_orders" not in out
    assert "OK live" in out
    saved = world["saved"]
    assert saved["reset_tickers"]["17"]["NAIL"] == _TODAY
    assert "KORU" not in saved["reset_tickers"]["17"]
    assert saved["reset_done"]["17"] == ""
    assert saved["exception_resting"]["slot"] == "17"
    assert saved["exception_resting"]["day"] == _TODAY
    assert saved["exception_resting"]["id"] == bot._EXCEPTION_ID
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
    assert world["exceptions"] == []
    assert "exception placed" not in out
    assert "FOLLOWUP reset" not in out
    assert "ERROR" not in out
    saved = world["saved"]
    assert saved["reset_done"]["17"] == _TODAY
    assert saved["reset_tickers"]["17"]["SOXL"] == _TODAY
    assert saved["exception_filled"] is False
    assert saved["exception_id"] == ""
    assert _HANDLED in saved["handled"]
    assert saved["tickers"]["KORU"]["sell"] == _level("KORU", 13.5)
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
        {"ticker": "SOXL", "side": "sell", "price": 75, "qty": 150, "orr_no": 75},
    ]
    world = {}
    code, out, world = _run(state, opens, world)
    assert code == 0, out
    assert [item[0] for item in world["cancelled"]] == ["NAIL"]
    assert all(item[1] != 75 for item in world["cancelled"])
    assert [item[0] for item in world["placed"]] == ["NAIL", "NAIL", "NAIL", "NAIL"]
    assert world["exceptions"] == [(100, 4.2)]
    assert "exception placed 1" in out

    done = world["saved"]
    # SOXL's turn. The standing sell at 75 is not a ladder rung, so the reset must not cancel it.
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
            "exception_id": bot._EXCEPTION_ID,
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
    blocked = datetime(2026, 10, 29, 15, 30, tzinfo=bot.KST)
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
                    "KORU": _pair("KORU", 12.5, 10.3, [_level("KORU", 10.3)]),
                    "SOXL": _pair("SOXL", 103, 86),
                },
                "handled": [handled],
                "exception_filled": True,
                "exception_id": bot._EXCEPTION_ID,
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
        assert forced["tickers"]["KORU"]["sell"] == _level("KORU", 13.5)
        assert forced["tickers"]["KORU"]["buy"] == _level("KORU", 11.3)
        assert forced["tickers"]["KORU"]["double_used"] == [_level("KORU", 10.3)]
        assert forced["tickers"]["SOXL"]["sell"] == 28
        assert forced["tickers"]["SOXL"]["buy"] == 26
        assert handled in forced["handled"]
        assert forced["exception_filled"] is True
        assert forced["exception_id"] == bot._EXCEPTION_ID
        assert forced["reset_done"]["09"] == "20261029"

        os.environ["SEED_FORCE"] = ""
        code, out = _capture(seed_state.main)
        assert code == 0, out
        assert "OK skip" in out
        assert _read_state(path)["tickers"]["KORU"]["sell"] == _level("KORU", 13.5)

        os.remove(path)
        os.environ["SEED_FORCE"] = "KORU:13.5:11.3"
        code, out = _capture(seed_state.main)
        assert code == 0, out
        fresh = _read_state(path)
        assert fresh["tickers"]["NAIL"]["sell"] == 14
        assert fresh["tickers"]["KORU"]["sell"] == _level("KORU", 13.5)
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
                    "KORU": _pair("KORU", 12.5, 10.3, [_level("KORU", 10.3)]),
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
        assert marked["exception_id"] == bot._EXCEPTION_ID
        assert marked["tickers"]["SOXL"]["double_used"] == [_level("SOXL", 72)]
        assert marked["tickers"]["SOXL"]["sell"] == _level("SOXL", 103)
        assert marked["tickers"]["KORU"]["double_used"] == [_level("KORU", 10.3)]
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
        assert both["exception_id"] == ""
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
        assert created["exception_id"] == bot._EXCEPTION_ID
        assert created["tickers"]["NAIL"]["double_used"] == [_level("NAIL", 22)]
        assert created["tickers"]["KORU"]["sell"] == _level("KORU", 12.5)
        assert created["tickers"]["SOXL"]["double_used"] == []


def test_reset_low_rung_sizes():
    assert ladders._NAIL_LADDER is ladders._KORU_LADDER is ladders._SOXL_LADDER
    assert ladders._price_at("KORU", 2) == 11.3
    assert ladders._level_of("KORU", 8.6) == ladders._level_of("NAIL", 8.6)
    assert ladders._level_of("NAIL", 8.5) is None
    assert bot._buy_computed("SOXL", _level("SOXL", 72), []) == 4
    assert bot._buy_computed("SOXL", _level("SOXL", 66), []) == 4
    assert bot._buy_computed("SOXL", _level("SOXL", 42), []) == 4
    assert bot._buy_computed("SOXL", _level("SOXL", 39), []) == 8
    assert bot._buy_computed("SOXL", _level("SOXL", 39), [_level("SOXL", 39)]) == 4
    assert bot._buy_computed("SOXL", _level("SOXL", 36), []) == 8
    assert bot._buy_computed("SOXL", _level("SOXL", 36), [_level("SOXL", 36)]) == 4
    assert bot._buy_computed("KORU", _level("KORU", 8.6), []) == 1
    assert bot._buy_computed("KORU", _level("KORU", 6.6), []) == 1
    assert bot._buy_computed("KORU", _level("KORU", 6.0), []) == 2
    assert bot._buy_computed("KORU", _level("KORU", 6.0), [_level("KORU", 6.0)]) == 1
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
                ("sell", 79, 4),
                ("sell", 86, 4),
                ("buy", 66, 4),
                ("buy", 60, 4),
            ],
        ),
        (
            "SOXL",
            46,
            39,
            [_level("SOXL", 39)],
            [
                ("sell", 46, 4),
                ("sell", 50, 4),
                ("buy", 39, 4),
                ("buy", 36, 8),
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
                ("buy", 9.4, 1),
                ("buy", 8.6, 1),
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
                "exception_id": bot._EXCEPTION_ID,
                "reset_done": {"09": "", "17": ""},
            }
        )
        code, out, world = _run(state, [], {})
        assert code == 0, out
        assert world["placed"] == [
            (ticker, side, price, qty) for side, price, qty in expected
        ], (ticker, world["placed"], out)
        assert world["cancelled"] == []
        if ticker == "KORU":
            assert world["exceptions"] == [(100, 4.2)]
            assert "exception placed 1" in out
        else:
            assert world["exceptions"] == []
            assert "exception placed" not in out


def _book(orders):
    return [{"side": side, "price": price, "qty": qty} for side, price, qty in orders]


def test_soxl_sell_qty_and_buy_rules():
    """SOXL sells are 4. Buys are 4, or 8 on an unused level at 39 or below."""
    cfg = ladders._CFG["SOXL"]
    ladder = cfg["ladder"]
    assert cfg["base"] == 4
    assert cfg["sell_qty"] == 4
    assert ladders._sell_shares(cfg) == 4
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
                    assert item["qty"] == 4, (label, item)
                    continue
                first = price <= 39
                if first and item["idx"] in set(flags):
                    assert item["qty"] == 4, (label, item)
                elif first:
                    assert item["qty"] == 8, (label, item)
                else:
                    assert item["qty"] == 4, (label, item)

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
        ("sell", 86, 4),
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
        ("buy", 9.4, 1),
        ("sell", 11.3, 1),
    ]

    assert ladders.plan(
        "SOXL",
        _book(
            [
                ("sell", 113, 4),
                ("sell", 125, 4),
                ("buy", 94, 4),
                ("buy", 86, 4),
            ]
        ),
    )["ok"]
    stale = ladders.plan(
        "SOXL",
        _book(
            [
                ("sell", 113, 1),
                ("sell", 125, 1),
                ("buy", 94, 2),
                ("buy", 86, 2),
            ]
        ),
    )
    assert stale["ok"] is False
    assert stale["would_place"] == 4
    assert stale["would_cancel"] == 4
    assert ladders.plan(
        "SOXL",
        _book(
            [
                ("sell", 86, 4),
                ("sell", 94, 4),
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
                ("buy", 9.4, 1),
                ("buy", 8.6, 1),
            ]
        ),
    )["ok"]


def test_shared_ladder():
    """NAIL, KORU, and SOXL share one series, extended below 5.0 and above 220."""
    listed = [
        5.0, 5.5, 6.0, 6.6, 7.2, 7.9, 8.6, 9.4, 10.3, 11.3, 12.5, 13.5, 15,
        16.5, 18, 20, 22, 24, 26, 28, 30, 33, 36, 39, 42, 46, 50, 55, 60, 66, 72,
    ]
    indexes = [_level("SOXL", price) for price in listed]
    assert indexes == list(range(-7, -7 + len(listed)))
    assert indexes[listed.index(9.4)] == 0
    for price in listed:
        assert _level("NAIL", price) == _level("KORU", price) == _level("SOXL", price)
    for index in range(ladders._MIN_INDEX, ladders._MAX_INDEX + 1):
        price = ladders._price_at("NAIL", index)
        assert price is not None
        assert _level("KORU", price) == index
        assert _level("SOXL", price) == index
        assert ladders._price_at("KORU", index) == price
    assert [ladders._price_at("NAIL", index) for index in (-8, -9, -10)] == [4.6, 4.2, 3.9]
    assert [ladders._price_at("KORU", index) for index in (35, 36, 37, 38)] == [
        220, 240, 260, 280,
    ]
    assert ladders._price_at("SOXL", ladders._MIN_INDEX) == 0.66
    assert ladders._price_at("SOXL", ladders._MIN_INDEX - 1) is None
    assert ladders._price_at("SOXL", ladders._MAX_INDEX + 1) is None
    for price in (6.5, 5.9, 5.4, 4.9, 4.5, 4.1, 6.4, 8.5):
        assert _level("NAIL", price) is None
        assert _level("KORU", price) is None
        assert _level("SOXL", price) is None

    assert ladders._LADDER[0] == 9.4 and ladders._LADDER[-1] == 220
    assert ladders._NAIL_LADDER is ladders._KORU_LADDER is ladders._SOXL_LADDER
    assert _level("SOXL", 113) == 28
    assert _level("NAIL", 42) == 17 and _level("NAIL", 36) == 15
    assert _level("SOXL", 46) == 18 and _level("SOXL", 39) == 16
    assert _level("KORU", 7.2) == -3 and _level("KORU", 6.0) == -5
    assert ladders._CFG["SOXL"]["sell_qty"] == 4
    assert ladders._CFG["SOXL"]["base"] == 4
    assert "sell_qty" not in ladders._CFG["KORU"]

    for price in (6.6, 6.0, 5.5, 5.0, 4.6, 0.66):
        koru_idx = _level("KORU", price)
        nail_idx = _level("NAIL", price)
        soxl_idx = _level("SOXL", price)
        assert bot._buy_computed("KORU", koru_idx, []) == (2 if price <= 6 else 1)
        assert bot._buy_computed("KORU", koru_idx, [koru_idx]) == 1
        assert bot._buy_computed("NAIL", nail_idx, []) == 40
        assert bot._buy_computed("NAIL", nail_idx, [nail_idx]) == 10
        assert bot._buy_computed("SOXL", soxl_idx, []) == 8
        assert bot._buy_computed("SOXL", soxl_idx, [soxl_idx]) == 4

    low_nail = bot._desired("NAIL", _level("NAIL", 11.3), _level("NAIL", 9.4), 1.0)
    assert [(item["side"], item["price"], item["qty"]) for item in low_nail] == [
        ("sell", 11.3, 10),
        ("sell", 12.5, 10),
        ("buy", 9.4, 40),
        ("buy", 8.6, 40),
    ]
    stepped_nail = bot._step_buy(
        "NAIL",
        {"sell": _level("NAIL", 11.3), "buy": _level("NAIL", 10.3), "double_used": []},
    )
    assert ladders._price_at("NAIL", stepped_nail["sell"]) == 10.3
    assert ladders._price_at("NAIL", stepped_nail["buy"]) == 9.4
    nail_cross = bot.intraday_adds(
        "NAIL",
        [
            {"side": "sell", "price": 10.3, "qty": 10},
            {"side": "sell", "price": 11.3, "qty": 10},
            {"side": "buy", "price": 9.4, "qty": 40},
        ],
        [{"side": "buy", "idx": _level("NAIL", 10.3)}],
        1.0,
    )
    assert nail_cross is None
    nail_down = bot.intraday_adds(
        "NAIL",
        [
            {"side": "sell", "price": 11.3, "qty": 10},
            {"side": "sell", "price": 12.5, "qty": 10},
            {"side": "buy", "price": 9.4, "qty": 40},
        ],
        [{"side": "buy", "idx": _level("NAIL", 10.3)}],
        1.0,
    )
    assert [(item["side"], item["price"], item["qty"]) for item in nail_down] == [
        ("buy", 8.6, 40),
        ("sell", 10.3, 10),
    ]
    high = bot._desired("SOXL", _level("SOXL", 220), _level("SOXL", 200), 1.0)
    assert [(item["side"], item["price"], item["qty"]) for item in high] == [
        ("sell", 220, 4),
        ("sell", 240, 4),
        ("buy", 200, 4),
        ("buy", 180, 4),
    ]
    high_nail = bot._desired("NAIL", _level("NAIL", 220), _level("NAIL", 200), 1.0)
    assert [(item["side"], item["price"], item["qty"]) for item in high_nail] == [
        ("sell", 220, 10),
        ("sell", 240, 10),
        ("buy", 200, 10),
        ("buy", 180, 10),
    ]

    at_low = bot._desired("KORU", _level("KORU", 8.6), _level("KORU", 7.2), 1.0, [])
    assert [(item["side"], item["price"], item["qty"]) for item in at_low] == [
        ("sell", 8.6, 1),
        ("sell", 9.4, 1),
        ("buy", 7.2, 1),
        ("buy", 6.6, 1),
    ]
    # 6.6 is above 6, so it is 1. 6.0 is an unused first buy of 2.
    below = bot._desired(
        "KORU",
        _level("KORU", 7.9),
        _level("KORU", 6.6),
        1.0,
        [_level("KORU", 7.2)],
    )
    assert [(item["side"], item["price"], item["qty"]) for item in below] == [
        ("sell", 7.9, 1),
        ("sell", 8.6, 1),
        ("buy", 6.6, 1),
        ("buy", 6.0, 2),
    ]
    used_low = bot._desired(
        "KORU",
        _level("KORU", 7.9),
        _level("KORU", 6.6),
        1.0,
        [_level("KORU", 6.6)],
    )
    assert [(item["side"], item["price"], item["qty"]) for item in used_low] == [
        ("sell", 7.9, 1),
        ("sell", 8.6, 1),
        ("buy", 6.6, 1),
        ("buy", 6.0, 2),
    ]

    pair = {"sell": _level("KORU", 8.6), "buy": _level("KORU", 7.2), "double_used": []}
    stepped_buys = []
    for _ in range(5):
        pair = bot._step_buy("KORU", pair)
        assert pair
        stepped_buys.append(ladders._price_at("KORU", pair["buy"]))
    assert stepped_buys == [6.6, 6.0, 5.5, 5.0, 4.6]

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
        ("buy", 6.6, 1),
        ("sell", 7.9, 1),
    ]
    further = bot.intraday_adds(
        "KORU",
        [
            {"side": "sell", "price": 7.9, "qty": 1},
            {"side": "sell", "price": 8.6, "qty": 1},
            {"side": "buy", "price": 6.6, "qty": 2},
        ],
        [{"side": "buy", "idx": _level("KORU", 7.2)}],
        1.0,
    )
    assert [(item["side"], item["price"], item["qty"]) for item in further] == [
        ("buy", 6.0, 2),
        ("sell", 7.2, 1),
    ]

    missing = ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 8.6, 1),
                ("sell", 9.4, 1),
                ("buy", 7.2, 1),
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
                ("buy", 7.2, 1),
                ("buy", 6.6, 1),
            ]
        ),
    )["ok"]
    odd = ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 8.6, 1),
                ("sell", 9.4, 1),
                ("buy", 7.2, 1),
                ("buy", 6.5, 2),
            ]
        ),
    )
    assert odd["ignored"] == 1
    assert odd["would_place"] == 1
    assert not ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 7.9, 1),
                ("sell", 8.6, 1),
                ("buy", 6.6, 2),
                ("buy", 6.0, 2),
            ]
        ),
    )["ok"]
    assert ladders.plan(
        "KORU",
        _book(
            [
                ("sell", 7.9, 1),
                ("sell", 8.6, 1),
                ("buy", 6.6, 1),
                ("buy", 6.0, 2),
            ]
        ),
    )["ok"]
    assert ladders.plan(
        "SOXL",
        _book(
            [
                ("sell", 240, 4),
                ("sell", 260, 4),
                ("buy", 220, 4),
                ("buy", 200, 4),
            ]
        ),
    )["ok"]
    assert ladders.plan(
        "NAIL",
        _book(
            [
                ("sell", 9.4, 10),
                ("sell", 10.3, 10),
                ("buy", 8.6, 40),
                ("buy", 7.9, 40),
            ]
        ),
    )["ok"]

    flags = seed_state._parse_flags("KORU.double_used=6.0,7.2")
    assert flags[1]["KORU"] == [_level("KORU", 6.0), _level("KORU", 7.2)]
    assert seed_state._parse_flags("KORU.double_used=6.5,7.2") is None
    assert seed_state._parse_flags("KORU.double_used=5.9") is None
    assert seed_state._parse_flags("KORU.double_used=6.4") is None
    assert seed_state._parse_force("KORU:7.9:6.5") is None
    assert seed_state._parse_force("KORU:7.9:6.0")["KORU"] == (
        _level("KORU", 7.9),
        _level("KORU", 6.0),
    )
    assert seed_state._parse_force("NAIL:11.3:9.4")["NAIL"] == (
        _level("NAIL", 11.3),
        _level("NAIL", 9.4),
    )
    assert seed_state._parse_force("SOXL:72:5.0")["SOXL"] == (
        _level("SOXL", 72),
        _level("SOXL", 5.0),
    )

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
    assert state["ladder_id"] == "shared"
    assert state["tickers"]["KORU"]["sell"] == _level("KORU", 8.6)
    assert state["tickers"]["KORU"]["buy"] == _level("KORU", 7.2)
    morning = datetime(2026, 10, 29, 9, 10, tzinfo=bot.KST)
    code, out, world = _run(state, [], {}, when=morning)
    assert code == 0, out
    assert "order type 63" in out
    assert world["placed"] == [
        ("KORU", "sell", 8.6, 1),
        ("KORU", "sell", 9.4, 1),
        ("KORU", "buy", 7.2, 1),
        ("KORU", "buy", 6.6, 1),
    ]
    assert world["cancelled"] == []
    assert "NEEDS_REVIEW KORU" not in out
    assert world["saved"]["tickers"]["KORU"]["buy"] == _level("KORU", 7.2)
    assert world["saved"]["ladder_id"] == "shared"


def test_legacy_koru_state_does_not_trade_5_9():
    """Old KORU index -2 was 5.9. Loading drops that pair instead of repricing it."""
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.json")
        bot._STATE_PATH = path
        bot._save_json(
            path,
            {
                "version": 2,
                "tickers": {
                    "NAIL": {"sell": 17, "buy": 15, "double_used": []},
                    "KORU": {"sell": 0, "buy": -2, "double_used": [-1]},
                    "SOXL": {"sell": 18, "buy": 16, "double_used": []},
                },
                "handled": [],
                "exception_filled": True,
                "exception_id": bot._EXCEPTION_ID,
                "reset_done": {"09": "", "17": ""},
            },
        )
        loaded = bot._load_state()
        assert loaded["ladder_id"] == "shared"
        assert "KORU" not in loaded["tickers"]
        assert loaded["tickers"]["NAIL"] == {"sell": 17, "buy": 15, "double_used": []}
        assert loaded["tickers"]["SOXL"] == {"sell": 18, "buy": 16, "double_used": []}
        assert ladders._price_at("NAIL", 17) == 42
        assert ladders._price_at("NAIL", 15) == 36
        assert ladders._price_at("SOXL", 18) == 46
        assert ladders._price_at("SOXL", 16) == 39
        assert bot._legacy_koru_index(0) == _level("KORU", 7.2)
        assert bot._legacy_koru_index(-2) is None
        assert bot._legacy_koru_index(-15) is None
        assert "KORU" not in bot._load_state()["tickers"]

        mapped = bot._migrate_loaded(
            {
                "version": 2,
                "tickers": {"KORU": {"sell": 2, "buy": 0, "double_used": [3, -1]}},
                "handled": [],
            }
        )
        assert mapped["tickers"]["KORU"]["sell"] == _level("KORU", 8.6)
        assert mapped["tickers"]["KORU"]["buy"] == _level("KORU", 7.2)
        assert mapped["tickers"]["KORU"]["double_used"] == [_level("KORU", 9.4)]

        kept = bot._migrate_loaded(
            {
                "version": 2,
                "ladder_id": "shared",
                "tickers": {
                    "KORU": {
                        "sell": _level("KORU", 7.2),
                        "buy": _level("KORU", 6.0),
                        "double_used": [],
                    }
                },
                "handled": [],
            }
        )
        assert kept["tickers"]["KORU"]["sell"] == _level("KORU", 7.2)
        assert kept["tickers"]["KORU"]["buy"] == _level("KORU", 6.0)

        morning = datetime(2026, 10, 29, 9, 10, tzinfo=bot.KST)
        code, out, world = _run(bot._clean_state(loaded), [], {}, when=morning)
        assert code == 0, out
        assert "NEEDS_REVIEW KORU" in out
        assert "DEFERRED SOXL" in out
        assert world["placed"] == _tagged("NAIL", 42, 36)
        assert all(row[0] != "KORU" for row in world["placed"])


def _legacy_soxl():
    """Live file shape: exception_filled true from the retired 100@75 fill."""
    return {
        "version": 2,
        "tickers": {"SOXL": _pair("SOXL", 113, 94)},
        "handled": [],
        "exception_filled": True,
        "reset_done": {"09": "", "17": ""},
    }


def _fill_at(price, qty, key, cancelled=0, side="sell"):
    return {
        "ticker": "SOXL",
        "side": side,
        "price": price,
        "filled_qty": qty,
        "cancelled_qty": cancelled,
        "idx": None,
        "key": key,
    }


def test_soxl_exception_sell_150_at_75():
    """Standing SOXL sell 150@75 until it is fully sold.

    Retired buys do not count: 100 at 75, 200 at 38, and 200 at 37.5
    filled 2026-11-21. A buy fill at 75 or 37.5 does not count. A
    partial sell is kept, and the next reset sells only the remainder.
    """
    assert bot._EXCEPTION_PRICE == 75
    assert bot._EXCEPTION_QTY == 150
    assert bot._EXCEPTION_ID == "soxl-sell-150-75"
    assert bot._HARD_MAX["SOXL"] == 8
    assert bot._sized("SOXL", 150, 1) is None
    assert bot._sized("SOXL", 200, 1) is None
    assert ladders._level_of("SOXL", 75) is None
    assert ladders._level_of("SOXL", 37.5) is None
    assert bot._exception_open(
        [{"ticker": "SOXL", "side": "sell", "price": 75, "qty": 150}]
    )
    assert bot._exception_open(
        [{"ticker": "SOXL", "side": "sell", "price": 75.05, "qty": 110}]
    )
    assert not bot._exception_open(
        [{"ticker": "SOXL", "side": "buy", "price": 75, "qty": 150}]
    )
    assert not bot._exception_open(
        [{"ticker": "SOXL", "side": "buy", "price": 37.5, "qty": 200}]
    )
    assert not bot._exception_open(
        [{"ticker": "SOXL", "side": "sell", "price": 37.5, "qty": 200}]
    )
    assert not bot._exception_open(
        [{"ticker": "SOXL", "side": "sell", "price": 72, "qty": 4}]
    )
    assert not bot._exception_open(
        [{"ticker": "NAIL", "side": "sell", "price": 75, "qty": 150}]
    )
    assert not bot._exception_open(
        [{"ticker": "SOXL", "side": "sell", "price": 75, "qty": 0}]
    )
    assert bot._ladder_book_empty(
        [{"ticker": "SOXL", "side": "sell", "price": 75, "qty": 150}]
    )
    legacy = bot._clean_state(_legacy_soxl())
    assert legacy["exception_filled"] is False
    assert legacy["exception_id"] == ""
    assert legacy["exception_filled_qty"] == 0
    for retired_id in ("soxl-buy-200-38", "soxl-buy-200-37-5"):
        retired = bot._clean_state(
            {
                "version": 2,
                "tickers": {},
                "handled": [],
                "exception_filled": True,
                "exception_id": retired_id,
                "exception_filled_qty": 200,
                "reset_done": {"09": "", "17": ""},
            }
        )
        assert retired["exception_filled"] is False
        assert retired["exception_id"] == ""
        assert retired["exception_filled_qty"] == 0
        assert not bot._exception_done(
            {"exception_filled": True, "exception_id": retired_id, "exception_filled_qty": 200}
        )
    done = bot._clean_state(
        {
            "version": 2,
            "tickers": {},
            "handled": [],
            "exception_filled": True,
            "exception_id": bot._EXCEPTION_ID,
            "reset_done": {"09": "", "17": ""},
        }
    )
    assert done["exception_filled"] is True
    assert done["exception_id"] == bot._EXCEPTION_ID
    assert done["exception_filled_qty"] == 150
    assert bot._exception_remaining(done) == 0
    sell_key = "cd" * 32
    buy_key = "ab" * 32
    assert bot._exception_filled([_fill_at(75, 150, sell_key)], set())
    assert bot._exception_filled([_fill_at(75.05, 40, "34" * 32)], set())
    assert not bot._exception_filled([_fill_at(75, 150, sell_key)], {sell_key})
    assert not bot._exception_filled([_fill_at(75, 150, buy_key, side="buy")], set())
    assert not bot._exception_filled([_fill_at(37.5, 200, "12" * 32, side="buy")], set())
    assert not bot._exception_filled(
        [_fill_at(75, 150, "ef" * 32, cancelled=150)], set()
    )

    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    os.environ.pop("CANCEL_DUP", None)
    ladder = _tagged("SOXL", 113, 94)
    book = [
        {"ticker": "SOXL", "side": side, "price": price, "qty": qty}
        for side, price, qty in _orders("SOXL", 113, 94)
    ]
    resting = {"ticker": "SOXL", "side": "sell", "price": 75, "qty": 150, "orr_no": 75}
    early = datetime(2026, 11, 5, 9, 59, tzinfo=bot.KST)
    morning = datetime(2026, 11, 5, 10, 0, tzinfo=bot.KST)
    before_evening = datetime(2026, 11, 5, 17, 10, tzinfo=bot.KST)
    evening = datetime(2026, 11, 5, 18, 11, tzinfo=bot.KST)
    evening_cron = datetime(2026, 11, 5, 18, 0, tzinfo=bot.KST)
    next_morning = datetime(2026, 11, 6, 10, 0, tzinfo=bot.KST)

    code, out, world = _run(_legacy_soxl(), [], {}, when=early)
    assert code == 0, out
    assert "skip: day session not open" in out
    assert world["exceptions"] == []
    assert world["placed"] == []

    code, out, world = _run(_legacy_soxl(), book + [resting], {}, when=early, mode="intraday")
    assert code == 0, out
    assert "order type 62" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert world["exceptions"] == []
    assert "exception placed" not in out
    assert world["saved"]["exception_filled"] is False
    assert world["saved"]["exception_id"] == ""

    code, out, world = _run(_legacy_soxl(), book + [resting], {}, when=morning, mode="intraday")
    assert code == 0, out
    assert world["exceptions"] == []
    assert world["cancelled"] == []
    assert world["placed"] == []
    assert "exception placed" not in out

    code, out, world = _run(_legacy_soxl(), [], {}, when=before_evening)
    assert code == 0, out
    assert "skip: premarket not open" in out
    assert world["exceptions"] == []

    code, out, placed = _run(
        _legacy_soxl(),
        [],
        {"exception_open": False},
        when=morning,
        fills=[_fill_at(75, 100, buy_key, side="buy"), _fill_at(37.5, 200, "12" * 32, side="buy")],
    )
    assert code == 0, out
    assert "order type 63" in out
    assert placed["placed"] == ladder
    assert placed["exceptions"] == [(150, 75)]
    assert placed["cancelled"] == []
    assert "exception placed 1" in out
    assert placed["saved"]["exception_filled"] is False
    assert placed["saved"]["exception_id"] == ""
    assert placed["saved"]["exception_filled_qty"] == 0
    assert placed["saved"]["reset_done"]["09"] == "20261105"
    for _ticker, _side, price, qty in placed["placed"]:
        assert price != 75 and price != 37.5
        if _side == "sell":
            assert qty == 4

    code, out, world = _run(placed["saved"], [], {"exception_open": False}, when=evening)
    assert code == 0, out
    assert "order type 61" in out
    assert world["exceptions"] == [(150, 75)]
    assert "exception placed 1" in out
    assert world["saved"]["exception_filled"] is False

    code, out, world = _run(_legacy_soxl(), [], {"exception_open": False}, when=evening_cron)
    assert code == 0, out
    assert world["exceptions"] == [(150, 75)]
    assert world["placed"] == ladder

    code, out, held = _run(_legacy_soxl(), [resting], {}, when=morning)
    assert code == 0, out
    assert held["exceptions"] == []
    assert "exception placed" not in out
    assert all(item[1] != 75 for item in held["cancelled"])
    assert held["placed"] == ladder
    assert held["saved"]["exception_filled"] is False

    code, out, held = _run(_legacy_soxl(), book + [resting], {}, when=evening)
    assert code == 0, out
    assert held["exceptions"] == []
    assert 75 not in [item[1] for item in held["cancelled"]]
    assert len(held["cancelled"]) == 4

    code, out, world = _run(_legacy_soxl(), [], {"exception_open": True}, when=morning)
    assert code == 0, out
    assert world["exceptions"] == []
    assert world["placed"] == ladder

    old_buy = {"ticker": "SOXL", "side": "buy", "price": 75, "qty": 100, "orr_no": 751}
    code, out, world = _run(_legacy_soxl(), [old_buy], {"exception_open": False}, when=evening)
    assert code == 0, out
    assert world["exceptions"] == [(150, 75)]
    assert all(item[1] != 75 for item in world["cancelled"])

    retired_buy = {"ticker": "SOXL", "side": "buy", "price": 37.5, "qty": 200, "orr_no": 375}
    code, out, world = _run(
        _legacy_soxl(), [retired_buy], {"exception_open": False}, when=morning
    )
    assert code == 0, out
    assert world["exceptions"] == [(150, 75)]
    assert all(item[1] != 37.5 and item[1] != 75 for item in world["cancelled"])

    code, out, world = _run(
        {
            "version": 2,
            "tickers": {"SOXL": _pair("SOXL", 113, 94)},
            "handled": [],
            "exception_filled": True,
            "exception_id": "soxl-buy-200-37-5",
            "exception_filled_qty": 200,
            "reset_done": {"09": "", "17": ""},
        },
        [],
        {"exception_open": False},
        when=morning,
        fills=[_fill_at(37.5, 200, "12" * 32, side="buy")],
    )
    assert code == 0, out
    assert world["exceptions"] == [(150, 75)]
    assert world["saved"]["exception_filled"] is False
    assert world["saved"]["exception_id"] == ""
    assert world["saved"]["exception_filled_qty"] == 0

    code, out, filled = _run(
        _legacy_soxl(),
        [],
        {},
        when=morning,
        fills=[_fill_at(75, 150, sell_key)],
    )
    assert code == 0, out
    assert filled["exceptions"] == []
    assert "exception placed" not in out
    assert filled["saved"]["exception_filled"] is True
    assert filled["saved"]["exception_id"] == bot._EXCEPTION_ID
    assert filled["saved"]["exception_filled_qty"] == 150
    assert sell_key in filled["saved"]["handled"]
    assert filled["saved"]["tickers"]["SOXL"]["sell"] == _level("SOXL", 113)
    assert filled["saved"]["tickers"]["SOXL"]["buy"] == _level("SOXL", 94)

    code, out, world = _run(
        filled["saved"], [], {"exception_open": False}, when=evening, fills=[]
    )
    assert code == 0, out
    assert world["exceptions"] == []
    assert world["placed"] == ladder
    assert world["saved"]["exception_filled"] is True
    assert world["saved"]["exception_id"] == bot._EXCEPTION_ID

    code, out, world = _run(
        filled["saved"], [], {"exception_open": False}, when=next_morning, fills=[]
    )
    assert code == 0, out
    assert world["exceptions"] == []
    assert world["saved"]["exception_filled"] is True

    code, out, world = _run(
        _legacy_soxl(),
        book,
        {},
        when=morning,
        fills=[_fill_at(75, 150, sell_key)],
        mode="intraday",
    )
    assert code == 0, out
    assert world["exceptions"] == []
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert world["saved"]["exception_filled"] is True
    assert world["saved"]["tickers"]["SOXL"]["sell"] == _level("SOXL", 113)

    code, out, world = _run(
        _legacy_soxl(),
        [],
        {"exception_open": False},
        when=evening,
        fills=[_fill_at(75, 150, "ef" * 32, cancelled=150)],
    )
    assert code == 0, out
    assert world["exceptions"] == [(150, 75)]
    assert world["saved"]["exception_filled"] is False
    assert world["saved"]["exception_id"] == ""

    partial_key = "a1" * 32
    code, out, partial = _run(
        _legacy_soxl(),
        [],
        {"exception_open": False},
        when=morning,
        fills=[_fill_at(75, 40, partial_key)],
    )
    assert code == 0, out
    assert partial["exceptions"] == [(110, 75)]
    assert "exception placed 1" in out
    assert partial["saved"]["exception_filled"] is False
    assert partial["saved"]["exception_id"] == bot._EXCEPTION_ID
    assert partial["saved"]["exception_filled_qty"] == 40
    assert partial["saved"]["exception_parts"][partial_key] == 40
    assert partial_key in partial["saved"]["handled"]
    assert partial["saved"]["tickers"]["SOXL"]["sell"] == _level("SOXL", 113)
    assert partial["placed"] == ladder

    code, out, again = _run(
        partial["saved"],
        [],
        {"exception_open": False},
        when=morning,
        fills=[_fill_at(75, 40, partial_key)],
    )
    assert code == 0, out
    assert "OK skip" in out
    assert again["exceptions"] == []

    code, out, nxt = _run(
        partial["saved"],
        [],
        {"exception_open": False},
        when=evening,
        fills=[_fill_at(75, 40, partial_key)],
    )
    assert code == 0, out
    assert nxt["exceptions"] == [(110, 75)]
    assert nxt["saved"]["exception_filled_qty"] == 40
    assert nxt["saved"]["exception_resting"]["slot"] == "17"

    grew_at = datetime(2026, 11, 6, 10, 0, tzinfo=bot.KST)
    code, out, grew = _run(
        partial["saved"],
        [],
        {"exception_open": False},
        when=grew_at,
        fills=[_fill_at(75, 90, partial_key)],
    )
    assert code == 0, out
    assert grew["exceptions"] == [(60, 75)]
    assert grew["saved"]["exception_filled_qty"] == 90
    assert grew["saved"]["exception_filled"] is False
    assert grew["saved"]["exception_parts"][partial_key] == 90

    rest_key = "b2" * 32
    later = datetime(2026, 11, 6, 18, 11, tzinfo=bot.KST)
    code, out, finished = _run(
        grew["saved"],
        [],
        {"exception_open": False},
        when=later,
        fills=[_fill_at(75, 90, partial_key), _fill_at(75, 60, rest_key)],
    )
    assert code == 0, out
    assert finished["exceptions"] == []
    assert "exception placed" not in out
    assert finished["saved"]["exception_filled"] is True
    assert finished["saved"]["exception_filled_qty"] == 150
    assert finished["saved"]["exception_id"] == bot._EXCEPTION_ID

    os.environ["CANCEL_DUP"] = "cancel-dup SOXL SELL 75 150"
    assert bot._parse_cancel_dup() is None
    os.environ["CANCEL_DUP"] = "cancel-dup SOXL SELL 75 4"
    dup_opens = [
        {
            "ticker": "SOXL",
            "side": "sell",
            "price": 75,
            "qty": 4,
            "orr_no": 1,
            "orr_dt": "20261105",
        },
        {
            "ticker": "SOXL",
            "side": "sell",
            "price": 75,
            "qty": 4,
            "orr_no": 2,
            "orr_dt": "20261105",
        },
    ]
    cancelled = []

    def _cancel(ticker, order):
        cancelled.append((ticker, order.get("price"), order.get("orr_no")))
        return True

    dup_world = {}
    dup_actions = _actions(dup_world)
    dup_actions["cancel"] = _cancel
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "workflow_dispatch",
            morning,
            morning.astimezone(bot.ET),
            dup_opens,
            [],
            _legacy_soxl(),
            dup_actions,
        )
    )
    assert code == 0, out
    assert cancelled == []
    assert "cancel-dup skip SOXL sell 75 qty 4 matches 0" in out
    os.environ.pop("CANCEL_DUP", None)

    monday = datetime(2026, 11, 23, 10, 11, tzinfo=bot.KST)
    assert monday.strftime("%A") == "Monday"
    assert bot._reset_slot(monday) == "09"
    assert not bot._orders_blocked(monday)
    assert bot._order_type(monday, monday.astimezone(bot.ET), "reset") == "63"
    os.environ["LIVE_TICKERS"] = "SOXL"
    code, out, world = _run(
        {
            "version": 2,
            "tickers": {"SOXL": _pair("SOXL", 113, 94)},
            "handled": [],
            "exception_filled": True,
            "exception_id": "soxl-buy-200-37-5",
            "exception_filled_qty": 200,
            "reset_done": {"09": "", "17": ""},
        },
        [],
        {"exception_open": False},
        when=monday,
    )
    assert code == 0, out
    assert "order type 63" in out
    assert "exception placed 1" in out
    assert "DEFERRED exception" not in out
    assert "exception plan place" not in out
    assert world["exceptions"] == [(150, 75)]
    assert world["saved"]["exception_filled"] is False
    assert world["saved"]["exception_id"] == ""
    assert world["saved"]["exception_filled_qty"] == 0
    assert world["saved"]["exception_resting"]["id"] == bot._EXCEPTION_ID
    assert world["saved"]["exception_resting"]["slot"] == "09"
    assert world["saved"]["exception_resting"]["day"] == "20261123"
    assert all(price != 75 for _ticker, _side, price, _qty in world["placed"])

    os.environ["ORDER_TYPE"] = "00"
    os.environ["LADDER_MODE"] = "reset"
    bot._now_kst = lambda: datetime(2026, 11, 5, 12, 0, tzinfo=bot.KST)
    captured = {"n": 0}

    def fake_post(url, raw, _extra):
        captured["n"] += 1
        captured["url"] = url
        captured["body"] = json.loads(raw.decode())["Input_0"]
        return 200, {}, {"Output_0": {"orr_no": "1"}}

    import gb_readonly_check as gb

    old_post = gb._post_bearer
    gb._post_bearer = fake_post
    try:
        assert bot._place_exception("0000", False, [0]) is False
        assert captured["n"] == 0
        assert bot._place_exception("0000", True, [bot._MAX_NEW]) is None
        assert captured["n"] == 0
        assert bot._place_exception("0000", True, [0]) is True
        assert captured["url"] == bot.SELL_URL
        full = captured["body"]
        assert bot._place_exception("0000", True, [1], 110) is True
        sell_body = dict(captured["body"])
        sell_url = captured["url"]
        koru = next(item for item in bot._EXCEPTIONS if item["id"] == "koru-buy-100-4.2")
        assert bot._place_exception("0000", True, [2], None, koru) is True
        assert captured["url"] == bot.BUY_URL
        koru_body = dict(captured["body"])
        captured["body"] = sell_body
        captured["url"] = sell_url
    finally:
        gb._post_bearer = old_post
        os.environ.pop("ORDER_TYPE", None)
        os.environ.pop("LADDER_MODE", None)
    assert full["iem_cd"] == "SOXL"
    assert full["orr_qty"] == 150
    assert full["fc_orr_uit_pr"] == 75
    assert "wtm_cur_knd_cd" not in full
    assert full["ahi_nmn_pr_tp_cd"] == "00"
    assert full["fc_sec_trd_nat_cd"] == "200"
    assert set(full) == {
        "act_no",
        "fc_sec_trd_nat_cd",
        "iem_cd",
        "orr_qty",
        "fc_orr_uit_pr",
        "ahi_nmn_pr_tp_cd",
    }
    body = captured["body"]
    assert body["orr_qty"] == 110
    assert body["fc_orr_uit_pr"] == 75
    assert "wtm_cur_knd_cd" not in body
    assert captured["url"] == bot.SELL_URL
    assert koru_body["iem_cd"] == "KORU"
    assert koru_body["orr_qty"] == 100
    assert koru_body["fc_orr_uit_pr"] == 4.2
    assert koru_body["wtm_cur_knd_cd"] == "1"
    assert koru_body["ahi_nmn_pr_tp_cd"] == "00"
    assert koru_body["fc_sec_trd_nat_cd"] == "200"

def test_saved_anchor_buy_sizes():
    """Current history stays. 39 used repeats at 4. An unused 39 is 8.

    42 is above 39, so it is 4 either way. KORU 6.6 is 1. 6.0 unused is 2.
    """
    soxl_used = [_level("SOXL", price) for price in (72, 66, 60, 55, 50, 46, 42, 39)]
    koru_used = [_level("KORU", price) for price in (9.4, 8.6, 7.9, 7.2)]

    def _rows(ticker, sell, buy, used):
        got = bot._desired(
            ticker, _level(ticker, sell), _level(ticker, buy), 1.0, used
        )
        return [(item["side"], item["price"], item["qty"]) for item in got]

    assert _rows("SOXL", 50, 42, soxl_used) == [
        ("sell", 50, 4),
        ("sell", 55, 4),
        ("buy", 42, 4),
        ("buy", 39, 4),
    ]
    assert _rows("SOXL", 50, 42, []) == [
        ("sell", 50, 4),
        ("sell", 55, 4),
        ("buy", 42, 4),
        ("buy", 39, 8),
    ]
    assert _rows("SOXL", 46, 42, []) == [
        ("sell", 46, 4),
        ("sell", 50, 4),
        ("buy", 42, 4),
        ("buy", 39, 8),
    ]
    assert _rows("SOXL", 55, 46, soxl_used) == [
        ("sell", 55, 4),
        ("sell", 60, 4),
        ("buy", 46, 4),
        ("buy", 42, 4),
    ]
    stepped = bot._step_buy(
        "SOXL",
        {
            "sell": _level("SOXL", 50),
            "buy": _level("SOXL", 42),
            "double_used": list(soxl_used),
        },
    )
    assert [ladders._price_at("SOXL", stepped["sell"]), ladders._price_at("SOXL", stepped["buy"])] == [46, 39]
    assert _rows("SOXL", 46, 39, stepped["double_used"]) == [
        ("sell", 46, 4),
        ("sell", 50, 4),
        ("buy", 39, 4),
        ("buy", 36, 8),
    ]
    assert _rows("KORU", 7.9, 6.6, koru_used) == [
        ("sell", 7.9, 1),
        ("sell", 8.6, 1),
        ("buy", 6.6, 1),
        ("buy", 6.0, 2),
    ]
    assert _rows("KORU", 8.6, 7.2, koru_used) == [
        ("sell", 8.6, 1),
        ("sell", 9.4, 1),
        ("buy", 7.2, 1),
        ("buy", 6.6, 1),
    ]
    assert _rows("NAIL", 39, 33, []) == [
        ("sell", 39, 10),
        ("sell", 42, 10),
        ("buy", 33, 10),
        ("buy", 30, 10),
    ]
    assert _rows("NAIL", 26, 22, []) == [
        ("sell", 26, 10),
        ("sell", 28, 10),
        ("buy", 22, 20),
        ("buy", 20, 40),
    ]
    assert bot._EXCEPTION_QTY == 150 and bot._EXCEPTION_PRICE == 75
    assert not hasattr(ladders, "_ANCHOR_BUY")

    os.environ["LIVE_TRADING"] = "on"
    saved_rows = {}
    for ticker, sell, buy, used in (
        ("SOXL", 50, 42, soxl_used),
        ("KORU", 7.9, 6.6, koru_used),
        ("NAIL", 39, 33, []),
    ):
        os.environ["LIVE_TICKERS"] = ticker
        state = bot._clean_state(
            {
                "version": 2,
                "tickers": {ticker: _pair(ticker, sell, buy, used)},
                "handled": [],
                "exception_filled": True,
                "exception_id": bot._EXCEPTION_ID,
                "reset_done": {"09": "", "17": ""},
            }
        )
        before = list(state["tickers"][ticker]["double_used"])
        code, out, world = _run(state, [], {})
        assert code == 0, out
        saved_rows[ticker] = _rows(ticker, sell, buy, used)
        assert world["placed"] == [
            (ticker, side, price, qty) for side, price, qty in saved_rows[ticker]
        ]
        assert world["cancelled"] == []
        if ticker == "KORU":
            assert world["exceptions"] == [(100, 4.2)]
        else:
            assert world["exceptions"] == []
        kept = world["saved"]["tickers"][ticker]
        assert kept["sell"] == _level(ticker, sell)
        assert kept["buy"] == _level(ticker, buy)
        assert kept["double_used"] == before

    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    combined = bot._fresh_state()
    combined["exception_filled"] = True
    combined["exception_id"] = bot._EXCEPTION_ID
    opens = []
    for ticker, sell, buy, used in (
        ("NAIL", 39, 33, []),
        ("KORU", 7.9, 6.6, koru_used),
        ("SOXL", 50, 42, soxl_used),
    ):
        combined["tickers"][ticker] = _pair(ticker, sell, buy, used)
        for side, price, qty in saved_rows[ticker]:
            opens.append(
                {"ticker": ticker, "side": side, "price": price, "qty": qty, "orr_no": 1}
            )
    code, out, world = _run(bot._clean_state(combined), opens, {}, mode="intraday")
    assert code == 0, out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert "NEEDS_REVIEW" not in out
    for ticker, sell, buy, used in (
        ("NAIL", 39, 33, []),
        ("KORU", 7.9, 6.6, koru_used),
        ("SOXL", 50, 42, soxl_used),
    ):
        kept = world["saved"]["tickers"][ticker]
        assert kept["sell"] == _level(ticker, sell)
        assert kept["buy"] == _level(ticker, buy)
        assert kept["double_used"] == list(used)


def test_unexpected_stop_names_reason_not_book():
    """A reset stop names the ticker and reason, and does not print the book."""
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    marker = bot._done_marker("17", "NAIL", _TODAY)
    state = {
        "version": 2,
        "ladder_id": "shared",
        "tickers": {
            "NAIL": _pair("NAIL", 39, 33),
            "KORU": _pair("KORU", 7.9, 6.6),
            "SOXL": _pair("SOXL", 50, 42, (_level("SOXL", 42),)),
        },
        "handled": [marker],
        "exception_filled": True,
        "exception_id": bot._EXCEPTION_ID,
        "reset_done": {"09": "", "17": ""},
        "reset_tickers": {"09": {}, "17": {}},
    }
    resting = {
        "ticker": "KORU",
        "side": "buy",
        "price": 6.6,
        "qty": 2,
        "orr_no": "991122",
    }

    def _stop(confirm, cancel_result=True, exists_result=False, place_result=True):
        world = {}
        actions = _actions(world)

        def cancel(ticker, order):
            world["cancelled"].append((ticker, order.get("price"), order.get("orr_no")))
            return cancel_result

        def place(ticker, item, counter):
            if place_result is None:
                return None
            return _actions(world)["place"](ticker, item, counter)

        actions["cancel"] = cancel
        actions["confirm_clear"] = lambda _ticker: confirm
        actions["exists"] = lambda *_args: exists_result
        actions["place"] = place
        code, out, world = _run(state, [resting], world, actions=actions)
        assert "6.6" not in out
        assert "991122" not in out
        assert "SOXL open" not in out
        assert "KORU open" not in out
        assert "NAIL skip done" in out
        saved = world["saved"]["reset_tickers"]["17"]
        assert saved.get("KORU") != _TODAY
        assert saved.get("SOXL") != _TODAY
        assert world["placed"] == []
        assert world["exceptions"] == []
        return code, out, world

    code, out, world = _stop(False)
    assert code == 1
    assert "ERROR unexpected remain KORU" in out
    assert out.count("ERROR unexpected") == 1
    assert world["cancelled"] == [("KORU", 6.6, "991122")]

    code, out, _world = _stop(None)
    assert code == 1
    assert "ERROR unexpected inquiry KORU" in out
    assert "ERROR unexpected remain" not in out

    code, out, world = _stop(True, cancel_result=None)
    assert code == 1
    assert "ERROR unexpected cancel KORU" in out
    assert world["cancelled"] == [("KORU", 6.6, "991122")]

    empty = {
        "version": 2,
        "ladder_id": "shared",
        "tickers": {"KORU": _pair("KORU", 7.9, 6.6)},
        "handled": [],
        "exception_filled": True,
        "exception_id": bot._EXCEPTION_ID,
        "reset_done": {"09": "", "17": ""},
        "reset_tickers": {"09": {}, "17": {}},
    }
    os.environ["LIVE_TICKERS"] = "KORU"
    world = {}
    actions = _actions(world)
    actions["exists"] = lambda *_args: None
    code, out, world = _run(empty, [], world, actions=actions)
    assert code == 1, out
    assert "ERROR unexpected exists KORU" in out
    assert world["placed"] == []
    assert "6.6" not in out

    world = {}
    actions = _actions(world)
    actions["exists"] = lambda *_args: False
    actions["place"] = lambda *_args: None
    code, out, world = _run(empty, [], world, actions=actions)
    assert code == 1, out
    assert "ERROR unexpected place KORU" in out
    assert world["placed"] == []
    assert "6.6" not in out

    class SecretError(Exception):
        pass

    leaked = SecretError("act_no 123456 token 6.6")
    assert bot._unexpected_line("select") == "ERROR unexpected select"
    assert bot._unexpected_line("nope", "NAIL") == "ERROR unexpected error NAIL"
    line = bot._unexpected_type(leaked)
    assert line == "ERROR unexpected SecretError"
    assert "123456" not in line
    assert "act_no" not in line
    assert "token" not in line
    assert "6.6" not in line
    SecretError.__name__ = "has space"
    assert bot._unexpected_type(leaked) == "ERROR unexpected"
    assert "123456" not in bot._unexpected_type(leaked)


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
    assert '- cron: "*/5 0-7 * * 1-5"' in intra
    assert '- cron: "*/5 8-23 * * 1-5"' in intra
    assert '- cron: "*/5 0 * * 6"' in intra
    assert '- cron: "*/5 * * * 1-5"' not in intra
    assert "'ladder-bot'" in reset
    assert "ladder-skip-" in reset
    assert "startsWith(github.event.comment.body, 'reset')" in reset
    assert "group: ladder-bot" in seed
    assert "SEED_FORCE:" in seed
    assert "seed_flags:" in seed
    assert "SEED_FLAGS:" in seed
    assert "exception_filled=true;SOXL.double_used=72" in seed
    assert "dispatches" not in reset
    assert "at most 3 extra passes" in reset
    assert "branches:" in reset
    assert "- main" in intra
    assert "startsWith(github.event.comment.body, 'kick')" in intra


def _live_book():
    return bot._clean_state(
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


def test_evening_followup_chain_places_exception_with_nail():
    """18:11 KST places NAIL and the exception, then two passes finish the slot."""
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    start = datetime(2026, 11, 10, 18, 11, tzinfo=bot.KST)
    assert start.weekday() == 1
    assert bot._reset_slot(start) == "17"
    box = {"state": _live_book()}
    clock = {"now": start}
    sleeps = []
    passes = []
    sent_exceptions = []

    def once():
        moment = clock["now"]
        world = {}
        actions = _actions(world)

        def exception_open():
            return False

        def place_exception(counter, qty=None, exc=None):
            if counter[0] >= bot._MAX_NEW:
                return None
            counter[0] += 1
            spec = exc or {
                "qty": bot._EXCEPTION_QTY,
                "price": bot._EXCEPTION_PRICE,
            }
            if qty is None:
                qty = spec["qty"]
            sent_exceptions.append((qty, spec["price"]))
            return True

        actions["exception_open"] = exception_open
        actions["place_exception"] = place_exception
        code, out, world = _run(box["state"], [], world, when=moment, actions=actions)
        box["state"] = world["saved"]
        passes.append((code, out, list(world["placed"])))
        return code, bot._wants_reset_followup

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] = clock["now"] + timedelta(seconds=seconds)

    code, loop_out = _capture(
        lambda: bot._bounded_reset_followups(once, sleep, lambda: clock["now"])
    )
    assert code == 0
    assert sleeps == [bot._FOLLOWUP_GAP, bot._FOLLOWUP_GAP]
    assert loop_out.count("follow-up reset") == 2
    assert "follow-up limit reached" not in loop_out
    assert len(passes) == 3
    first_code, first_out, first_placed = passes[0]
    assert first_code == 0, first_out
    assert first_placed == _tagged("NAIL", 33, 28)
    assert sent_exceptions == [(150, 75), (100, 4.2)]
    assert "exception placed 2" in first_out
    assert "DEFERRED KORU" in first_out
    assert "DEFERRED SOXL" in first_out
    assert "DEFERRED exception" not in first_out
    assert "FOLLOWUP reset" in first_out
    assert box["state"]["exception_resting"]["slot"] == "17"
    assert box["state"]["exception_resting"]["day"] == "20261110"
    second_code, second_out, second_placed = passes[1]
    assert second_code == 0, second_out
    assert second_placed == _tagged("KORU", 13.5, 11.3)
    assert "NAIL skip done" in second_out
    assert "DEFERRED SOXL" in second_out
    assert "exception placed" not in second_out
    assert "FOLLOWUP reset" in second_out
    third_code, third_out, third_placed = passes[2]
    assert third_code == 0, third_out
    assert third_placed == _tagged("SOXL", 113, 94)
    assert "KORU skip done" in third_out
    assert "exception placed" not in third_out
    assert "FOLLOWUP reset" not in third_out
    assert box["state"]["reset_tickers"]["17"] == {
        "NAIL": "20261110",
        "KORU": "20261110",
        "SOXL": "20261110",
    }
    assert box["state"]["reset_done"]["17"] == "20261110"
    assert box["state"]["exception_filled"] is False
    assert sent_exceptions == [(150, 75), (100, 4.2)]


def test_exception_not_duplicated_when_open_filled_or_resting():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    evening = datetime(2026, 11, 10, 18, 11, tzinfo=bot.KST)
    resting_order = {
        "ticker": "SOXL",
        "side": "sell",
        "price": 75,
        "qty": 150,
        "orr_no": 375,
    }
    code, out, world = _run(_live_book(), [resting_order], {}, when=evening)
    assert code == 0, out
    assert world["exceptions"] == [(100, 4.2)]
    assert "exception placed 1" in out
    assert all(item[1] != 75 for item in world["cancelled"])
    assert world["saved"]["exception_resting"] == {}

    filled = _live_book()
    filled["exception_filled"] = True
    filled["exception_id"] = bot._EXCEPTION_ID
    filled["exception_resting"] = {
        "id": bot._EXCEPTION_ID,
        "slot": "17",
        "day": "20261110",
        "at": int(evening.timestamp()),
    }
    code, out, world = _run(filled, [], {}, when=evening)
    assert code == 0, out
    assert world["exceptions"] == [(100, 4.2)]
    assert all(price != 75 for _qty, price in world["exceptions"])
    assert world["saved"]["exception_filled"] is True
    assert world["saved"]["exception_id"] == bot._EXCEPTION_ID
    assert world["saved"]["exception_resting"] == {}

    resting = _live_book()
    resting["exception_resting"] = {
        "id": bot._EXCEPTION_ID,
        "slot": "17",
        "day": "20261110",
        "at": int(evening.timestamp()),
    }
    code, out, world = _run(resting, [], {"exception_open": False}, when=evening)
    assert code == 0, out
    assert world["exceptions"] == [(100, 4.2)]
    assert "exception placed 1" in out
    assert "DEFERRED exception" not in out

    code, out, world = _run(_live_book(), [], {"exception_open": True}, when=evening)
    assert code == 0, out
    assert world["exceptions"] == [(100, 4.2)]
    assert "exception placed 1" in out

    morning_mark = _live_book()
    morning_mark["exception_resting"] = {
        "id": bot._EXCEPTION_ID,
        "slot": "09",
        "day": "20261110",
        "at": int(evening.timestamp()),
    }
    code, out, world = _run(morning_mark, [], {"exception_open": False}, when=evening)
    assert code == 0, out
    assert world["exceptions"] == [(150, 75), (100, 4.2)]
    assert "exception placed 2" in out
    assert world["saved"]["exception_resting"]["slot"] == "17"
    assert world["saved"]["exception_filled"] is False
    weekend = {
        "exception_resting": {
            "id": bot._EXCEPTION_ID,
            "slot": "",
            "day": "20261107",
            "at": 1,
        }
    }
    assert bot._exception_resting(weekend, None, "20261107")
    assert not bot._exception_resting(weekend, "09", "20261109")
    assert not bot._exception_resting(
        {"exception_resting": {"id": "soxl-buy-200-38", "slot": "17", "day": "20261110"}},
        "17",
        "20261110",
    )


def test_followup_guard_and_loop_limit():
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    evening = datetime(2026, 11, 10, 18, 11, tzinfo=bot.KST)
    near_guard = datetime(2026, 11, 10, 15, 58, tzinfo=bot.KST)
    assert bot._followup_allowed(evening)
    assert bot._followup_allowed(datetime(2026, 11, 10, 17, 10, tzinfo=bot.KST))
    assert not bot._followup_allowed(near_guard)
    assert not bot._followup_allowed(datetime(2026, 11, 10, 16, 5, tzinfo=bot.KST))
    assert not bot._followup_allowed(datetime(2026, 11, 10, 17, 9, tzinfo=bot.KST))
    assert not bot._followup_allowed(datetime(2026, 11, 10, 8, 30, tzinfo=bot.KST))
    assert not bot._followup_allowed(datetime(2026, 11, 10, 9, 9, tzinfo=bot.KST))
    late = datetime(2026, 11, 10, 23, 59, tzinfo=bot.KST)
    assert bot._reset_slot(late) == "17"
    assert not bot._followup_allowed(late)
    assert bot._MAX_FOLLOWUPS == 3
    assert bot._FOLLOWUP_GAP == 120
    assert bot._MAX_NEW == 6

    sleeps = []
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        return 0, True

    def sleep(seconds):
        sleeps.append(seconds)

    code, out = _capture(
        lambda: bot._bounded_reset_followups(always, sleep, lambda: evening)
    )
    assert code == 0
    assert calls["n"] == 1 + bot._MAX_FOLLOWUPS
    assert sleeps == [bot._FOLLOWUP_GAP] * bot._MAX_FOLLOWUPS
    assert "follow-up limit reached" in out
    assert out.count("follow-up reset") == bot._MAX_FOLLOWUPS

    sleeps.clear()
    calls["n"] = 0
    code, out = _capture(
        lambda: bot._bounded_reset_followups(always, sleep, lambda: near_guard)
    )
    assert code == 0
    assert calls["n"] == 1
    assert sleeps == []
    assert "follow-up stopped time guard" in out
    assert "follow-up reset" not in out

    calls["n"] = 0
    sleeps.clear()

    def fail():
        calls["n"] += 1
        return 1, True

    code = bot._bounded_reset_followups(fail, sleep, lambda: evening)
    assert code == 1
    assert calls["n"] == 1
    assert sleeps == []

    clock = {"now": evening}

    def jump(seconds):
        sleeps.append(seconds)
        clock["now"] = datetime(2026, 11, 10, 16, 5, tzinfo=bot.KST)

    calls["n"] = 0
    sleeps.clear()
    code, out = _capture(
        lambda: bot._bounded_reset_followups(always, jump, lambda: clock["now"])
    )
    assert code == 0
    assert calls["n"] == 1
    assert sleeps == [bot._FOLLOWUP_GAP]
    assert "follow-up stopped time guard" in out

    code, out, world = _run(_live_book(), [], {}, when=near_guard)
    assert code == 0, out
    assert world["placed"] == _tagged("NAIL", 33, 28)
    assert world["exceptions"] == [(150, 75), (100, 4.2)]
    assert "exception placed 2" in out
    assert "DEFERRED KORU" in out
    assert "DEFERRED SOXL" in out
    assert "DEFERRED exception" not in out
    assert "follow-up stopped time guard" in out
    assert "FOLLOWUP reset" not in out
    assert bot._wants_reset_followup is False
    assert world["saved"]["exception_resting"]["slot"] == "09"
    assert world["saved"]["reset_tickers"]["09"]["NAIL"] == "20261110"


def test_soxl_old_sizes_do_not_duplicate():
    """Open 2/4-share SOXL orders still count. A fill of one still steps.

    Intraday matches a rung, not the share count, so the book placed at
    the previous size is not cancelled or ordered again. The reset after
    the broker wipe is what quotes the new 4 and 8.
    """
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    used = [_level("SOXL", price) for price in (72, 66, 60, 55, 50, 46, 42, 39, 36, 33, 30)]
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {"SOXL": _pair("SOXL", 36, 30, used)},
            "handled": [],
            "exception_filled": True,
            "exception_id": bot._EXCEPTION_ID,
            "reset_done": {"09": "20261120", "17": "20261120"},
        }
    )
    opens = [
        {"ticker": "SOXL", "side": "sell", "price": price, "qty": 2, "orr_no": 100 + index}
        for index, price in enumerate((36, 39, 42, 46, 50))
    ]
    opens.extend(
        [
            {"ticker": "SOXL", "side": "buy", "price": 30, "qty": 2, "orr_no": 201},
            {"ticker": "SOXL", "side": "buy", "price": 28, "qty": 4, "orr_no": 202},
            {"ticker": "SOXL", "side": "buy", "price": 26, "qty": 4, "orr_no": 203},
        ]
    )
    moment = datetime(2026, 11, 20, 18, 0, tzinfo=bot.KST)
    code, out, world = _run(state, opens, {}, when=moment, mode="intraday")
    assert code == 0, out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert "NEEDS_REVIEW SOXL" not in out
    assert "SOXL ok yes" in out
    kept = world["saved"]["tickers"]["SOXL"]
    assert kept["sell"] == _level("SOXL", 36)
    assert kept["buy"] == _level("SOXL", 30)
    assert kept["double_used"] == used

    key = "e1" * 32
    filled = [order for order in opens if order["price"] != 36]
    fill = {
        "ticker": "SOXL",
        "side": "sell",
        "price": 36,
        "qty": 2,
        "filled_qty": 2,
        "cancelled_qty": 0,
        "idx": _level("SOXL", 36),
        "key": key,
        "orr_no": 100,
    }
    code, out, stepped = _run(
        state, filled, {}, when=moment, fills=[fill], mode="intraday"
    )
    assert code == 0, out
    assert "NEEDS_REVIEW SOXL" not in out
    assert stepped["cancelled"] == []
    assert stepped["placed"] == [("SOXL", "buy", 33, 4)]
    assert all(item[2] != 30 for item in stepped["placed"])
    saved = stepped["saved"]["tickers"]["SOXL"]
    assert saved["sell"] == _level("SOXL", 39)
    assert saved["buy"] == _level("SOXL", 33)
    assert saved["double_used"] == used
    assert key in stepped["saved"]["handled"]


def test_soxl_monday_reset_plan():
    """Monday 10:00 KST reset quotes the new SOXL sizes on an empty book.

    Used 30 is 4. Unused 28 is 8. Both sells from 36 are 4. A lower pair
    of unused buys is 8 and 8.
    """
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    os.environ.pop("RESET_COMMENT", None)
    used = [_level("SOXL", price) for price in (72, 66, 60, 55, 50, 46, 42, 39, 36, 33, 30)]
    for price in (36, 33, 30):
        assert bot._buy_computed("SOXL", _level("SOXL", price), used) == 4
    for price in (28, 26, 24):
        assert bot._buy_computed("SOXL", _level("SOXL", price), used) == 8
    assert bot._buy_computed("SOXL", _level("SOXL", 42), used) == 4
    assert bot._buy_computed("SOXL", _level("SOXL", 42), []) == 4
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {"SOXL": _pair("SOXL", 36, 30, used)},
            "handled": [],
            "exception_filled": True,
            "exception_id": bot._EXCEPTION_ID,
            "reset_done": {"09": "", "17": ""},
        }
    )
    monday = datetime(2026, 11, 23, 10, 0, tzinfo=bot.KST)
    assert bot._reset_slot(monday) == "09"
    assert not bot._orders_blocked(monday)
    code, out, world = _run(state, [], {}, when=monday)
    assert code == 0, out
    assert "NEEDS_REVIEW SOXL" not in out
    assert world["cancelled"] == []
    assert world["exceptions"] == []
    assert sorted(world["placed"]) == [
        ("SOXL", "buy", 28, 8),
        ("SOXL", "buy", 30, 4),
        ("SOXL", "sell", 36, 4),
        ("SOXL", "sell", 39, 4),
    ]
    lower = bot._desired(
        "SOXL", _level("SOXL", 30), _level("SOXL", 26), 1.0, used
    )
    assert [(item["side"], item["price"], item["qty"]) for item in lower] == [
        ("sell", 30, 4),
        ("sell", 33, 4),
        ("buy", 26, 8),
        ("buy", 24, 8),
    ]


def _koru_exc():
    for exc in bot._EXCEPTIONS:
        if exc["id"] == "koru-buy-100-4.2":
            return exc
    raise AssertionError("missing koru exception")


def test_koru_exception_buy_100_at_4_2():
    """Standing KORU buy of 100 at 4.2, beside the ladder's own 2-share buy.

    A partial fill re-places only the remainder. Filling all 100 stops
    the replacement. A 2-share fill is the ladder fill only when it is
    not this order. Cancel-dup does not treat the standing buy as a copy.
    """
    exc = _koru_exc()
    assert [item["id"] for item in bot._EXCEPTIONS] == [
        "soxl-sell-150-75",
        "koru-buy-100-4.2",
    ]
    assert exc == {
        "id": "koru-buy-100-4.2",
        "ticker": "KORU",
        "side": "buy",
        "price": 4.2,
        "qty": 100,
    }
    assert bot._EXCEPTION_ID == "soxl-sell-150-75"
    assert bot._EXCEPTION_QTY == 150 and bot._EXCEPTION_PRICE == 75
    idx = _level("KORU", 4.2)
    assert idx is not None
    assert bot._buy_computed("KORU", idx, []) == 2
    assert bot._buy_computed("KORU", idx, [idx]) == 1
    ladder = _tagged("KORU", 4.6, 4.2)
    assert ("KORU", "buy", 4.2, 2) in ladder
    assert ("KORU", "buy", 3.9, 2) in ladder

    hundred = {
        "ticker": "KORU",
        "side": "buy",
        "price": 4.2,
        "qty": 100,
        "orr_no": 100,
        "ordered_qty": 100,
    }
    two = {"ticker": "KORU", "side": "buy", "price": 4.2, "qty": 2, "orr_no": 2}
    buys, _sells, ignored = bot._split("KORU", [dict(hundred), dict(two)])
    assert ignored == 1
    assert [order["qty"] for order in buys] == [2]
    assert bot._exception_open([hundred], exc)
    assert not bot._exception_open([two], exc)
    assert not bot._exception_order(two)
    assert bot._exception_order(hundred)
    assert bot._ladder_book_empty([hundred])
    assert not bot._ladder_book_empty([hundred, two])

    remainder = {
        "ticker": "KORU",
        "side": "buy",
        "price": 4.2,
        "qty": 2,
        "ordered_qty": 100,
        "orr_no": 100,
    }
    assert bot._exception_order(remainder)
    remembered = {
        "exception_orders": {
            exc["id"]: {
                "filled": False,
                "filled_qty": 98,
                "parts": {},
                "resting": {},
                "order_nos": [100],
            }
        }
    }
    ambiguous = {"ticker": "KORU", "side": "buy", "price": 4.2, "qty": 2, "orr_no": 100}
    assert bot._exception_order(ambiguous, remembered)
    assert not bot._exception_order(two, remembered)
    buys, _sells, ignored = bot._split(
        "KORU", [dict(ambiguous), dict(two)], remembered
    )
    assert ignored == 1
    assert [order["orr_no"] for order in buys] == [2]

    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "KORU,SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    os.environ.pop("CANCEL_DUP", None)
    when = datetime(2026, 11, 10, 18, 11, tzinfo=bot.KST)
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "KORU": _pair("KORU", 4.6, 4.2),
                "SOXL": _pair("SOXL", 113, 94),
            },
            "handled": [],
            "exception_filled": False,
            "reset_done": {"09": "", "17": ""},
            "reset_tickers": {"09": {}, "17": {"SOXL": "20261110"}},
        }
    )
    code, out, world = _run(state, [], {}, when=when)
    assert code == 0, out
    assert world["placed"] == ladder
    assert world["exceptions"] == [(150, 75), (100, 4.2)]
    assert "exception placed 2" in out
    assert "DEFERRED exception" not in out
    assert world["saved"]["tickers"]["KORU"]["double_used"] == []
    assert world["saved"]["exception_id"] == ""
    assert world["saved"]["exception_filled"] is False
    stored = world["saved"]["exception_orders"][exc["id"]]
    assert stored["filled"] is False
    assert stored["filled_qty"] == 0
    assert stored["resting"]["id"] == exc["id"]
    assert stored["resting"]["slot"] == "17"
    assert stored["resting"]["day"] == "20261110"

    book = [
        {"ticker": "KORU", "side": side, "price": price, "qty": qty, "orr_no": 10 + i}
        for i, (side, price, qty) in enumerate(_orders("KORU", 4.6, 4.2))
    ]
    book.append(dict(hundred))
    saved = world["saved"]
    saved["reset_done"] = {"09": "", "17": ""}
    code, out, held = _run(saved, book, {}, when=when)
    assert code == 0, out
    assert held["exceptions"] == []
    assert "exception placed" not in out
    assert held["cancelled"] == []
    assert "KORU ignored 1" in out
    assert held["saved"]["tickers"]["KORU"]["double_used"] == []
    assert ("KORU", "buy", 4.2, 2) not in held["placed"]

    partial_key = "c3" * 32
    fill = {
        "ticker": "KORU",
        "side": "buy",
        "price": 4.2,
        "ordered_qty": 100,
        "filled_qty": 40,
        "cancelled_qty": 0,
        "idx": idx,
        "orr_no": 100,
        "key": partial_key,
    }
    code, out, partial = _run(
        state, [], {"exception_open": False}, when=when, fills=[fill]
    )
    assert code == 0, out
    assert partial["exceptions"] == [(150, 75), (60, 4.2)]
    assert ("KORU", "buy", 4.2, 2) in partial["placed"]
    assert partial["saved"]["tickers"]["KORU"]["sell"] == _level("KORU", 4.6)
    assert partial["saved"]["tickers"]["KORU"]["buy"] == idx
    assert partial["saved"]["tickers"]["KORU"]["double_used"] == []
    assert partial["saved"]["exception_orders"][exc["id"]]["filled_qty"] == 40
    assert partial["saved"]["exception_orders"][exc["id"]]["parts"][partial_key] == 40
    assert partial["saved"]["exception_filled"] is False
    assert partial_key in partial["saved"]["handled"]

    grown_key = "d4" * 32
    grown = dict(fill, filled_qty=90, key=grown_key)
    # Same order reporting a larger fill replaces the previous count on
    # that key only. A different key would add. This key is new, so 40
    # from the first key stays and 90 would overshoot. Use the same key.
    grown = dict(fill, filled_qty=90)
    later = datetime(2026, 11, 11, 18, 11, tzinfo=bot.KST)
    code, out, grew = _run(
        partial["saved"], [], {"exception_open": False}, when=later, fills=[grown]
    )
    assert code == 0, out
    assert grew["exceptions"] == [(150, 75), (10, 4.2)]
    assert grew["saved"]["exception_orders"][exc["id"]]["filled_qty"] == 90
    assert grew["saved"]["exception_orders"][exc["id"]]["filled"] is False
    assert grew["saved"]["tickers"]["KORU"]["double_used"] == []

    rest_key = "e6" * 32
    rest = dict(fill, filled_qty=10, key=rest_key, orr_no=101, ordered_qty=10)
    done_at = datetime(2026, 11, 12, 18, 11, tzinfo=bot.KST)
    code, out, finished = _run(
        grew["saved"],
        [],
        {"exception_open": False},
        when=done_at,
        fills=[grown, rest],
    )
    assert code == 0, out
    assert finished["exceptions"] == [(150, 75)]
    assert "exception placed 1" in out
    assert finished["saved"]["exception_orders"][exc["id"]]["filled"] is True
    assert finished["saved"]["exception_orders"][exc["id"]]["filled_qty"] == 100
    assert finished["saved"]["exception_orders"][exc["id"]]["resting"] == {}
    assert finished["saved"]["tickers"]["KORU"]["double_used"] == []

    after = datetime(2026, 11, 13, 18, 11, tzinfo=bot.KST)
    code, out, stopped = _run(
        finished["saved"], [], {"exception_open": False}, when=after
    )
    assert code == 0, out
    assert stopped["exceptions"] == [(150, 75)]
    assert stopped["saved"]["exception_orders"][exc["id"]]["filled"] is True

    # A 2-share fill of the standing order must not step the ladder or
    # mark the rung used. A separate 2-share ladder fill still does.
    tiny_key = "f7" * 32
    tiny = dict(fill, filled_qty=2, key=tiny_key)
    code, out, tiny_world = _run(
        state, [], {"exception_open": False}, when=when, fills=[tiny]
    )
    assert code == 0, out
    assert tiny_world["exceptions"] == [(150, 75), (98, 4.2)]
    assert tiny_world["placed"] == ladder
    assert tiny_world["saved"]["tickers"]["KORU"]["double_used"] == []
    assert tiny_world["saved"]["exception_orders"][exc["id"]]["filled_qty"] == 2

    ladder_key = "a8" * 32
    ladder_fill = {
        "ticker": "KORU",
        "side": "buy",
        "price": 4.2,
        "ordered_qty": 2,
        "filled_qty": 2,
        "cancelled_qty": 0,
        "idx": idx,
        "orr_no": 2,
        "key": ladder_key,
    }
    code, out, stepped = _run(
        state, [], {"exception_open": False}, when=when, fills=[ladder_fill]
    )
    assert code == 0, out
    assert (100, 4.2) in stepped["exceptions"]
    assert idx in stepped["saved"]["tickers"]["KORU"]["double_used"]
    assert stepped["saved"]["tickers"]["KORU"]["buy"] == _level("KORU", 3.9)
    assert ("KORU", "buy", 4.2, 2) not in stepped["placed"]
    assert stepped["saved"]["exception_orders"].get(exc["id"], {}).get("filled_qty", 0) == 0

    code, out, both_fills = _run(
        state,
        [],
        {"exception_open": False},
        when=when,
        fills=[ladder_fill, fill],
    )
    assert code == 0, out
    assert (60, 4.2) in both_fills["exceptions"]
    assert idx in both_fills["saved"]["tickers"]["KORU"]["double_used"]
    assert both_fills["saved"]["exception_orders"][exc["id"]]["filled_qty"] == 40
    assert both_fills["saved"]["tickers"]["KORU"]["buy"] == stepped["saved"]["tickers"]["KORU"]["buy"]

    # Intraday sees the standing fill and does not follow it.
    os.environ["LIVE_TICKERS"] = "KORU"
    intra_state = bot._clean_state(
        {
            "version": 2,
            "tickers": {"KORU": _pair("KORU", 4.6, 4.2)},
            "handled": [],
            "exception_filled": True,
            "exception_id": bot._EXCEPTION_ID,
            "reset_done": {"09": "20261110", "17": "20261110"},
        }
    )
    intra_book = [
        {"ticker": "KORU", "side": side, "price": price, "qty": qty, "orr_no": 30 + i}
        for i, (side, price, qty) in enumerate(_orders("KORU", 4.6, 4.2))
    ]
    noon = datetime(2026, 11, 10, 12, 0, tzinfo=bot.KST)
    code, out, intra = _run(
        intra_state, intra_book, {}, when=noon, fills=[fill], mode="intraday"
    )
    assert code == 0, out
    assert intra["placed"] == []
    assert intra["cancelled"] == []
    assert intra["exceptions"] == []
    assert intra["saved"]["tickers"]["KORU"]["double_used"] == []
    assert intra["saved"]["tickers"]["KORU"]["buy"] == idx
    assert intra["saved"]["exception_orders"][exc["id"]]["filled_qty"] == 40

    os.environ["LIVE_TICKERS"] = "KORU"
    os.environ["CANCEL_DUP"] = "cancel-dup KORU BUY 4.2 2"
    dup_opens = [
        {
            "ticker": "KORU",
            "side": "buy",
            "price": 4.2,
            "qty": 2,
            "orr_no": 1,
            "orr_dt": "20261110",
        },
        {
            "ticker": "KORU",
            "side": "buy",
            "price": 4.2,
            "qty": 2,
            "orr_no": 3,
            "orr_dt": "20261110",
        },
        dict(hundred, orr_dt="20261110"),
        {
            "ticker": "KORU",
            "side": "buy",
            "price": 4.2,
            "qty": 2,
            "ordered_qty": 100,
            "orr_no": 100,
            "orr_dt": "20261110",
        },
    ]
    cancelled = []

    def _cancel(ticker, order):
        cancelled.append((ticker, order.get("price"), order.get("orr_no"), order.get("qty")))
        return True

    dup_world = {}
    dup_actions = _actions(dup_world)
    dup_actions["cancel"] = _cancel
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "workflow_dispatch",
            when,
            when.astimezone(bot.ET),
            dup_opens,
            [],
            remembered,
            dup_actions,
        )
    )
    assert code == 0, out
    assert cancelled == [("KORU", 4.2, 3, 2)]
    os.environ["CANCEL_DUP"] = "cancel-dup KORU BUY 4.2 2"
    cancelled.clear()
    only = [dup_opens[0], dup_opens[3]]
    code, out = _capture(
        lambda: bot.run(
            "cancel-dup",
            "workflow_dispatch",
            when,
            when.astimezone(bot.ET),
            only,
            [],
            remembered,
            dup_actions,
        )
    )
    assert code == 0, out
    assert cancelled == []
    assert "matches 1" in out
    os.environ.pop("CANCEL_DUP", None)

    # One free slot places the SOXL sell and defers the KORU buy.
    # The next pass places the KORU buy.
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    old_max = bot._MAX_NEW
    bot._MAX_NEW = 5
    try:
        defer_state = bot._clean_state(
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
        code, out, first = _run(defer_state, [], {}, when=when)
        assert code == 0, out
        assert first["exceptions"] == [(150, 75)]
        assert "exception placed 1" in out
        assert "DEFERRED exception" in out
        assert "DEFERRED KORU" in out
        assert bot._wants_reset_followup is True
        code, out, second = _run(first["saved"], [], {}, when=when)
        assert code == 0, out
        assert second["exceptions"] == [(100, 4.2)]
        assert "exception placed 1" in out
        assert second["saved"]["exception_orders"][exc["id"]]["resting"]["slot"] == "17"
        assert second["placed"] == _tagged("KORU", 13.5, 11.3)
    finally:
        bot._MAX_NEW = old_max


def main():
    try:
        test_pairs_and_limits()
        test_batch_selector()
        test_koru_exception_buy_100_at_4_2()
        test_three_resets()
        test_evening_followup_chain_places_exception_with_nail()
        test_exception_not_duplicated_when_open_filled_or_resting()
        test_followup_guard_and_loop_limit()
        test_deferred_not_cancelled_and_exception_survives()
        test_exists_skips_duplicates()
        test_intraday_unchanged()
        test_push_and_time_guard_place_nothing()
        test_seed_force()
        test_seed_flags()
        test_reset_low_rung_sizes()
        test_soxl_sell_qty_and_buy_rules()
        test_shared_ladder()
        test_legacy_koru_state_does_not_trade_5_9()
        test_soxl_exception_sell_150_at_75()
        test_saved_anchor_buy_sizes()
        test_soxl_old_sizes_do_not_duplicate()
        test_soxl_monday_reset_plan()
        test_unexpected_stop_names_reason_not_book()
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
