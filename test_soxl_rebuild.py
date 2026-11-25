"""SOXL is 20 shares, plus the 400 at 18.7, and the one-shot rebuild."""

import os

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


def test_exception_is_400_at_18_7_and_sell_stays():
    ids = [item["id"] for item in bot._EXCEPTIONS]
    assert ids[0] == "soxl-sell-150-75"
    assert "soxl-buy-400-18-7" in ids
    exc = bot._EXCEPTION_BY_ID["soxl-buy-400-18-7"]
    assert exc["side"] == "buy" and exc["qty"] == 400
    assert abs(exc["price"] - 18.7) < 1e-9
    assert ladders._level_of("SOXL", 18.7) is None
    state = bot._clean_state({})
    assert bot._exception_remaining(state, exc) == 400
    assert bot._exception_remaining(state) == 150
    state["exception_id"] = bot._EXCEPTION_ID
    state["exception_filled"] = True
    assert bot._exception_done(state) is True
    assert bot._exception_done_for(state, exc) is False


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
    assert "exception plan place sell 75 qty 150" in out
    assert "exception plan place buy 18.7 qty 400" in out
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
    assert "SOXL plan ignore sell 75 qty 150" in out
    assert "SOXL plan cancel sell 75" not in out
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
    assert all(price != 75 for _ticker, price, _number in world["cancelled"])
    assert all(
        not (ticker == "SOXL" and price == 75) for ticker, price, _number in world["cancelled"]
    )
    assert sorted(world["placed"]) == [
        ("SOXL", "buy", 28, 20),
        ("SOXL", "buy", 30, 20),
        ("SOXL", "sell", 36, 20),
        ("SOXL", "sell", 39, 20),
    ]
    assert (400, 18.7) in world["exceptions"]
    assert (150, 75) not in world["exceptions"]
    saved = world["saved"]
    assert saved["tickers"]["SOXL"]["sell"] == _level(36)
    assert saved["tickers"]["SOXL"]["buy"] == _level(30)
    assert saved["tickers"]["NAIL"]["sell"] == sim._pair("NAIL", 39, 33)["sell"]
    assert saved["tickers"]["KORU"]["buy"] == sim._pair("KORU", 7.2, 6.0)["buy"]
    assert _KEY in saved["handled"]
    bucket = saved["exception_orders"]["soxl-buy-400-18-7"]
    assert bucket["filled"] is False
    assert bucket["resting"]["id"] == "soxl-buy-400-18-7"


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
        test_exception_is_400_at_18_7_and_sell_stays()
        test_dry_rebuild_keeps_36_30_despite_the_30_fill()
        test_live_rebuild_cancels_only_soxl_ladder_orders()
        test_later_reset_and_intraday_keep_36_30()
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
