"""Simulation for the reset cap and SEED_FORCE. No broker calls."""

import io
import json
import os
import sys
import tempfile
from datetime import datetime

import ladder_bot as bot
import ladder_dryrun as ladders
import seed_state

_FIXED = datetime(2026, 10, 29, 17, 15, tzinfo=bot.KST)
_TODAY = "20261029"
_HANDLED = "ab" * 32
_ORIG_NOW = bot._now_kst
_ORIG_STATE = bot._STATE_PATH


def _level(ticker, price):
    return ladders._level(ladders._CFG[ticker]["ladder"], price)


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
        ("sell", 113, 1),
        ("sell", 125, 1),
        ("buy", 94, 1),
        ("buy", 86, 1),
    ]
    assert _orders("KORU", 12.5, 10.3)[0] == ("sell", 12.5, 1)
    assert bot._HARD_MAX == {"NAIL": 20, "KORU": 4, "SOXL": 4}
    assert bot._MAX_NEW == 6
    assert bot._sized("NAIL", 20, 1) == 20
    assert bot._sized("NAIL", 21, 1) is None
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
