"""Manual one-ticker adjust, and a reset that must not re-quote a filled anchor."""

import os
from datetime import datetime

import ladder_bot as bot
import ladder_dryrun as ladders
import test_reset_batch as sim

_ORIG_NOW = bot._now_kst
_PREMARKET = datetime(2026, 11, 9, 18, 45, tzinfo=bot.KST)
_GUARD = datetime(2026, 11, 9, 16, 45, tzinfo=bot.KST)
_SATURDAY = datetime(2026, 11, 7, 6, 0, tzinfo=bot.KST)


def _level(ticker, price):
    return ladders._level_of(ticker, price)


def _used():
    return [_level("SOXL", price) for price in (72, 66, 60, 55, 50, 46, 42, 39)]


def _state():
    return bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "NAIL": sim._pair("NAIL", 39, 33),
                "KORU": sim._pair("KORU", 7.2, 6.0),
                "SOXL": sim._pair("SOXL", 50, 42, _used()),
            },
            "handled": [],
            "exception_filled": False,
            "exception_id": "",
            "reset_done": {"09": "20261109", "17": "20261109"},
            "reset_tickers": {
                "09": {"NAIL": "20261109", "KORU": "20261109", "SOXL": "20261109"},
                "17": {"NAIL": "20261109", "KORU": "20261109", "SOXL": "20261109"},
            },
        }
    )


def _opens():
    return [
        {"ticker": "SOXL", "side": "buy", "price": 39, "qty": 2, "orr_no": 1},
        {"ticker": "SOXL", "side": "buy", "price": 42, "qty": 2, "orr_no": 2},
        {"ticker": "SOXL", "side": "sell", "price": 50, "qty": 2, "orr_no": 3},
        {"ticker": "SOXL", "side": "sell", "price": 55, "qty": 2, "orr_no": 4},
        {"ticker": "SOXL", "side": "buy", "price": 37.5, "qty": 200, "orr_no": 5},
        {"ticker": "NAIL", "side": "buy", "price": 33, "qty": 10, "orr_no": 6},
        {"ticker": "NAIL", "side": "sell", "price": 39, "qty": 10, "orr_no": 7},
        {"ticker": "KORU", "side": "buy", "price": 6.0, "qty": 2, "orr_no": 8},
        {"ticker": "KORU", "side": "sell", "price": 7.2, "qty": 1, "orr_no": 9},
    ]


def _env(preview, sell="46", buy="39", step="none"):
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    os.environ["LADDER_MODE"] = "adjust"
    os.environ["ADJUST_TICKER"] = "SOXL"
    os.environ["ADJUST_SELL"] = sell
    os.environ["ADJUST_BUY"] = buy
    os.environ["ADJUST_STEP"] = step
    os.environ["ADJUST_PREVIEW"] = "true" if preview else "false"


def _clear_env():
    for name in (
        "ADJUST_TICKER",
        "ADJUST_SELL",
        "ADJUST_BUY",
        "ADJUST_STEP",
        "ADJUST_PREVIEW",
        "LADDER_MODE",
        "ORDER_TYPE",
        "MAX_QTY_MULT",
    ):
        os.environ.pop(name, None)


def _run(state, opens, when, preview, sell="46", buy="39", step="none"):
    _env(preview, sell, buy, step)
    bot._now_kst = lambda: when
    world = {"placed": [], "cancelled": [], "exceptions": []}
    actions = sim._actions(world)

    def place_exception(_counter):
        raise AssertionError("adjust placed the 37.5 exception")

    actions["place_exception"] = place_exception
    code, out = sim._capture(
        lambda: bot.run(
            "adjust",
            "workflow_dispatch",
            when,
            when.astimezone(bot.ET),
            opens,
            [],
            state,
            actions,
        )
    )
    return code, out, world


def _assert_plan(out):
    assert "SOXL anchor sell 46 buy 39" in out
    assert "SOXL plan cancel buy 42 qty 2" in out
    assert "SOXL plan cancel sell 55 qty 2" in out
    assert "SOXL plan place sell 46 qty 2" in out
    assert "SOXL plan place buy 36 qty 4" in out
    assert "SOXL plan keep buy 39 qty 2" in out
    assert "SOXL plan keep sell 50 qty 2" in out
    assert "SOXL plan ignore buy 37.5 qty 200" in out
    assert "NAIL unchanged" in out
    assert "KORU unchanged" in out
    assert "SOXL plan cancel buy 39" not in out
    assert "SOXL plan cancel sell 50" not in out
    assert "SOXL plan place buy 42" not in out
    assert "SOXL plan place sell 55" not in out


def _assert_others(saved, before):
    assert saved["tickers"]["NAIL"] == before["tickers"]["NAIL"]
    assert saved["tickers"]["KORU"] == before["tickers"]["KORU"]
    assert saved["reset_done"] == before["reset_done"]
    assert saved["reset_tickers"] == before["reset_tickers"]
    assert saved["handled"] == before["handled"]
    assert saved["exception_filled"] == before["exception_filled"]
    assert saved["exception_id"] == before["exception_id"]
    soxl = saved["tickers"]["SOXL"]
    assert soxl["sell"] == _level("SOXL", 46)
    assert soxl["buy"] == _level("SOXL", 39)
    assert _level("SOXL", 42) in soxl["double_used"]
    assert _level("SOXL", 39) in soxl["double_used"]
    assert _level("SOXL", 36) not in soxl["double_used"]


def test_target_book_quantities():
    pair = sim._pair("SOXL", 46, 39, _used())
    desired = bot._desired("SOXL", pair["sell"], pair["buy"], 1.0, pair["double_used"])
    assert [(item["side"], item["price"], item["qty"]) for item in desired] == [
        ("sell", 46, 2),
        ("sell", 50, 2),
        ("buy", 39, 2),
        ("buy", 36, 4),
    ]


def test_preview_prints_delta_and_sends_nothing():
    when = _PREMARKET
    assert when.astimezone(bot.ET).hour == 4
    assert when.astimezone(bot.ET).minute == 45
    assert bot._order_type(when, when.astimezone(bot.ET), "adjust") == "61"
    assert bot._window_open("adjust", when, when.astimezone(bot.ET))
    assert not bot._orders_blocked(when)
    before = _state()
    code, out, world = _run(before, _opens(), when, preview=True)
    assert code == 0, out
    assert "order type 61" in out
    _assert_plan(out)
    assert "OK preview" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert "saved" not in world
    assert "37.5" in out
    assert "orr_no" not in out


def test_live_adjust_syncs_soxl_only():
    before = _state()
    code, out, world = _run(before, _opens(), _PREMARKET, preview=False)
    assert code == 0, out
    _assert_plan(out)
    assert "order type 61" in out
    assert "OK live" in out
    assert "exception placed" not in out
    assert sorted(world["placed"]) == [
        ("SOXL", "buy", 36, 4),
        ("SOXL", "sell", 46, 2),
    ]
    assert sorted((ticker, price) for ticker, price, _number in world["cancelled"]) == [
        ("SOXL", 42),
        ("SOXL", 55),
    ]
    assert all(number != 5 for _ticker, _price, number in world["cancelled"])
    _assert_others(world["saved"], before)


def test_step_buy_one_matches_explicit_anchors():
    code, out, world = _run(
        _state(), _opens(), _PREMARKET, preview=False, sell="", buy="", step="buy"
    )
    assert code == 0, out
    _assert_plan(out)
    assert sorted(world["placed"]) == [
        ("SOXL", "buy", 36, 4),
        ("SOXL", "sell", 46, 2),
    ]
    assert _level("SOXL", 36) not in world["saved"]["tickers"]["SOXL"]["double_used"]
    step_text = _run(
        _state(),
        _opens(),
        _PREMARKET,
        preview=True,
        sell="",
        buy="",
        step="step buy one",
    )[1]
    assert "SOXL anchor sell 46 buy 39" in step_text
    assert "OK preview" in step_text


def test_other_hours_and_bad_anchor():
    assert bot._orders_blocked(_GUARD)
    code, out, world = _run(_state(), _opens(), _GUARD, preview=False)
    assert code == 0, out
    assert "skip: time guard" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert "saved" not in world

    saturday = _SATURDAY
    et = saturday.astimezone(bot.ET)
    assert saturday.weekday() == 5
    assert et.weekday() == 4
    closed = datetime(2026, 11, 2, 17, 30, tzinfo=bot.KST)
    closed_et = closed.astimezone(bot.ET)
    assert not bot._orders_blocked(closed)
    assert not bot._window_open("adjust", closed, closed_et)
    code, out, world = _run(_state(), _opens(), closed, preview=False)
    assert code == 0, out
    assert "OK skip" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert "saved" not in world

    assert not bot._window_open("reset", saturday, et)
    assert bot._window_open("adjust", saturday, et)
    assert bot._order_type(saturday, et, "adjust") == "62"
    code, out, world = _run(_state(), _opens(), saturday, preview=False)
    assert code == 0, out
    assert "order type 62" in out
    assert "OK live" in out
    assert sorted(world["placed"]) == [
        ("SOXL", "buy", 36, 4),
        ("SOXL", "sell", 46, 2),
    ]
    assert {ticker for ticker, _price, _number in world["cancelled"]} == {"SOXL"}

    code, out, world = _run(
        _state(), _opens(), _PREMARKET, preview=False, sell="46", buy="37.5"
    )
    assert code == 1, out
    assert "ERROR config" in out
    assert world["placed"] == []
    assert world["cancelled"] == []
    assert "saved" not in world

    code, out, world = _run(
        _state(), _opens(), _PREMARKET, preview=False, sell="46", buy="39", step="buy"
    )
    assert code == 1, out
    assert "ERROR config" in out
    assert world["placed"] == []


def test_reset_does_not_requote_unhandled_anchor_fill():
    """A buy fill during the wipe window is applied before the next reset."""
    os.environ["LIVE_TRADING"] = "on"
    os.environ["LIVE_TICKERS"] = "NAIL,KORU,SOXL"
    os.environ.pop("ORDER_TYPE", None)
    os.environ.pop("MAX_QTY_MULT", None)
    key = "cd" * 32
    state = bot._clean_state(
        {
            "version": 2,
            "tickers": {
                "NAIL": sim._pair("NAIL", 39, 33),
                "KORU": sim._pair("KORU", 7.2, 6.0),
                "SOXL": sim._pair("SOXL", 50, 42, _used()),
            },
            "handled": [],
            "exception_filled": True,
            "exception_id": bot._EXCEPTION_ID,
            "reset_done": {"09": "", "17": ""},
            "reset_tickers": {
                "09": {},
                "17": {"NAIL": sim._TODAY, "KORU": sim._TODAY},
            },
        }
    )
    fill = {
        "ticker": "SOXL",
        "side": "buy",
        "price": 42,
        "qty": 2,
        "filled_qty": 2,
        "cancelled_qty": 0,
        "idx": _level("SOXL", 42),
        "key": key,
    }
    opens = _opens()
    code, out, world = sim._run(state, opens, {}, fills=[fill])
    assert code == 0, out
    assert ("SOXL", "buy", 42, 2) not in world["placed"]
    assert sorted(world["placed"]) == [
        ("SOXL", "buy", 36, 4),
        ("SOXL", "buy", 39, 2),
        ("SOXL", "sell", 46, 2),
        ("SOXL", "sell", 50, 2),
    ]
    assert all(price != 37.5 for _ticker, price, _number in world["cancelled"])
    assert {ticker for ticker, _price, _number in world["cancelled"]} == {"SOXL"}
    saved = world["saved"]
    assert saved["tickers"]["SOXL"]["sell"] == _level("SOXL", 46)
    assert saved["tickers"]["SOXL"]["buy"] == _level("SOXL", 39)
    assert _level("SOXL", 36) not in saved["tickers"]["SOXL"]["double_used"]
    assert key in saved["handled"]
    assert saved["tickers"]["NAIL"] == state["tickers"]["NAIL"]
    assert saved["tickers"]["KORU"] == state["tickers"]["KORU"]
    assert "exception placed" not in out

    wiped = [order for order in opens if order["orr_no"] in (5, 6, 7, 8, 9)]
    code, out, wiped_world = sim._run(state, wiped, {}, fills=[fill])
    assert code == 0, out
    assert wiped_world["cancelled"] == []
    assert ("SOXL", "buy", 42, 2) not in wiped_world["placed"]
    assert ("SOXL", "buy", 36, 4) in wiped_world["placed"]
    assert ("SOXL", "sell", 46, 2) in wiped_world["placed"]
    assert wiped_world["saved"]["tickers"]["SOXL"]["buy"] == _level("SOXL", 39)
    assert all(item[0] == "SOXL" for item in wiped_world["placed"])

    handled = bot._clean_state(state)
    handled["handled"] = [key]
    code, out, again = sim._run(handled, opens, {}, fills=[fill])
    assert code == 0, out
    assert ("SOXL", "buy", 42, 2) in again["placed"]
    assert again["saved"]["tickers"]["SOXL"]["sell"] == _level("SOXL", 50)
    assert again["saved"]["tickers"]["SOXL"]["buy"] == _level("SOXL", 42)


def _without_exception(opens=None):
    rows = _opens() if opens is None else opens
    return [order for order in rows if order.get("price") != 37.5]


def _run_free(state, opens, when, preview, ticker="SOXL", sell="46", buy="39", step="none", mutate=None):
    _env(preview, sell, buy, step)
    os.environ["ADJUST_TICKER"] = ticker
    bot._now_kst = lambda: when
    world = {"placed": [], "cancelled": [], "exceptions": []}
    actions = sim._actions(world)
    if mutate is not None:
        mutate(actions, world)
    code, out = sim._capture(
        lambda: bot.run(
            "adjust",
            "workflow_dispatch",
            when,
            when.astimezone(bot.ET),
            opens,
            [],
            state,
            actions,
        )
    )
    return code, out, world


def test_adjust_places_missing_exception_once():
    """A SOXL adjust sends 200@37.5 when it is missing, and not twice."""
    when = _PREMARKET
    assert bot._reset_slot(when) == "17"
    missing = _without_exception()
    code, out, world = _run_free(_state(), missing, when, preview=False)
    assert code == 0, out
    assert "exception plan place buy 37.5 qty 200" in out
    assert "exception placed 1" in out
    assert world["exceptions"] == [(200, 37.5)]
    assert ("SOXL", "sell", 46, 2) in world["placed"]
    assert ("SOXL", "buy", 36, 4) in world["placed"]
    saved = world["saved"]
    assert saved["exception_filled"] is False
    assert saved["exception_id"] == ""
    assert saved["exception_placed"]["17"] == "20261109"
    assert saved["reset_tickers"]["17"]["SOXL"] == "20261109"
    assert all(price != 37.5 for _ticker, price, _number in world["cancelled"])

    rested = _without_exception()
    rested = [order for order in rested if order.get("price") not in (42, 55)]
    rested.extend(
        (
            {"ticker": "SOXL", "side": "sell", "price": 46, "qty": 2, "orr_no": 11},
            {"ticker": "SOXL", "side": "buy", "price": 36, "qty": 4, "orr_no": 12},
        )
    )
    code, out, again = _run_free(saved, rested, when, preview=False)
    assert code == 0, out
    assert again["exceptions"] == []
    assert "exception placed" not in out
    assert again["placed"] == []

    def seen_open(actions, _world):
        actions["exception_open"] = lambda: True

    code, out, fresh = _run_free(_state(), missing, when, preview=False, mutate=seen_open)
    assert code == 0, out
    assert fresh["exceptions"] == []
    assert "exception placed" not in out
    assert fresh["saved"]["exception_placed"]["17"] == "20261109"

    filled = _state()
    filled["exception_filled"] = True
    filled["exception_id"] = bot._EXCEPTION_ID
    code, out, world = _run_free(filled, missing, when, preview=False)
    assert code == 0, out
    assert world["exceptions"] == []
    assert "exception placed" not in out
    assert world["saved"]["exception_filled"] is True
    assert world["saved"]["exception_id"] == bot._EXCEPTION_ID

    code, out, world = _run_free(_state(), missing, when, preview=True)
    assert code == 0, out
    assert "exception plan place buy 37.5 qty 200" in out
    assert "OK preview" in out
    assert world["exceptions"] == []
    assert world["placed"] == []
    assert "saved" not in world


def test_adjust_other_ticker_leaves_exception():
    missing = _without_exception()
    code, out, world = _run_free(
        _state(), missing, _PREMARKET, preview=False, ticker="NAIL", sell="42", buy="36"
    )
    assert code == 0, out
    assert world["exceptions"] == []
    assert "exception placed" not in out
    assert "exception plan place" not in out
    assert saved_ticker(world) == "NAIL"


def saved_ticker(world):
    changed = [
        name
        for name, pair in world["saved"]["tickers"].items()
        if pair != _state()["tickers"][name]
    ]
    assert changed == ["NAIL"]
    return "NAIL"


def test_adjust_slot_mark_blocks_reset_replace():
    """Orders written by adjust are not cancelled by a later reset the same slot."""
    state = _state()
    state["reset_done"] = {"09": "20261109", "17": ""}
    state["reset_tickers"] = {
        "09": {"NAIL": "20261109", "KORU": "20261109", "SOXL": "20261109"},
        "17": {},
    }
    state = bot._clean_state(state)
    code, out, world = _run_free(state, _opens(), _PREMARKET, preview=False)
    assert code == 0, out
    assert "exception placed" not in out
    assert world["saved"]["reset_tickers"]["17"]["SOXL"] == "20261109"
    assert "NAIL" not in world["saved"]["reset_tickers"]["17"]
    kept = [
        order
        for order in _opens()
        if not (order["ticker"] == "SOXL" and order["price"] in (42, 55))
    ]
    kept.extend(
        (
            {"ticker": "SOXL", "side": "sell", "price": 46, "qty": 2, "orr_no": 11},
            {"ticker": "SOXL", "side": "buy", "price": 36, "qty": 4, "orr_no": 12},
        )
    )
    os.environ["LADDER_MODE"] = "reset"
    code, out, reset = sim._run(world["saved"], kept, {}, when=_PREMARKET)
    assert code == 0, out
    assert all(item[0] != "SOXL" for item in reset["cancelled"])
    assert "SOXL skip done" in out
    assert reset["exceptions"] == []


def test_workflow_file():
    root = os.path.dirname(os.path.abspath(__file__))
    text = open(
        os.path.join(root, ".github/workflows/ladder-adjust.yml"), encoding="utf-8"
    ).read()
    seed = open(
        os.path.join(root, ".github/workflows/seed-state.yml"), encoding="utf-8"
    ).read()
    assert "name: ladder-adjust" in text
    assert "workflow_dispatch:" in text
    assert "cron:" not in text
    assert "LADDER_MODE: adjust" in text
    assert "ADJUST_PREVIEW:" in text
    assert "default: true" in text
    assert "group: ladder-bot" in text
    assert "ticker:" in text
    assert "sell:" in text
    assert "buy:" in text
    assert "step:" in text
    assert 'default: "none"' in text
    assert "seed_force" not in text
    assert "seed_flags:" in seed
    assert "SEED_FORCE:" in seed


def main():
    try:
        test_target_book_quantities()
        test_preview_prints_delta_and_sends_nothing()
        test_live_adjust_syncs_soxl_only()
        test_step_buy_one_matches_explicit_anchors()
        test_other_hours_and_bad_anchor()
        test_reset_does_not_requote_unhandled_anchor_fill()
        test_adjust_places_missing_exception_once()
        test_adjust_other_ticker_leaves_exception()
        test_adjust_slot_mark_blocks_reset_replace()
        test_workflow_file()
        print("passed 10 failed 0")
        print("OK adjust")
    finally:
        bot._now_kst = _ORIG_NOW
        _clear_env()
        os.environ.pop("LIVE_TRADING", None)
        os.environ.pop("LIVE_TICKERS", None)


if __name__ == "__main__":
    main()
