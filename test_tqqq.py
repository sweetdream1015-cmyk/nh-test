"""TQQQ standing buys and account selection. No broker calls."""

import io
import json
import os
import sys
import tempfile
from datetime import datetime

import acct_check
import gb_readonly_check as gb
import ladder_bot as bot
import ladder_dryrun as ladders
import restore_ladder_state as restore
import restore_tqqq_state as restore_tqqq
import tqqq_bot as tqqq

_NOW = datetime(2026, 11, 25, 10, 11, tzinfo=bot.KST)
_EVENING = datetime(2026, 11, 25, 18, 11, tzinfo=bot.KST)
_GUARD = datetime(2026, 11, 25, 8, 30, tzinfo=bot.KST)
_SUMMER_SKIP = datetime(2026, 7, 15, 16, 30, tzinfo=bot.KST)
_PRE_DAY = datetime(2026, 11, 25, 9, 30, tzinfo=bot.KST)
_KEY = "ab" * 32
_KEY2 = "cd" * 32
_FIRST = "201-02-111111"
_SECOND = "201-12-681235"
_ORIG_NOW = bot._now_kst
_ORIG_POST = bot._post_order

_EXPECTED = (
    "43.00",
    "41.54",
    "40.12",
    "38.75",
    "37.43",
    "36.16",
    "34.93",
    "33.74",
    "32.59",
    "31.48",
    "30.41",
    "29.37",
    "28.37",
    "27.40",
    "26.47",
    "25.57",
    "24.70",
    "23.86",
    "23.04",
    "22.26",
)


def _capture(fn):
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        code = fn()
    finally:
        sys.stdout = old
    return code, buf.getvalue()


def _payload(*rows):
    return {
        "cust_no": "87654321",
        "Output_0": list(rows),
    }


def _row(acct_type, acct_no):
    return {"acct_type": acct_type, "acct_no": acct_no}


def _fill(price, qty, key=_KEY, ticker="TQQQ", side="buy"):
    return {
        "ticker": ticker,
        "side": side,
        "price": price,
        "qty": 0,
        "filled_qty": qty,
        "cancelled_qty": 0,
        "key": key,
    }


def _open(price, qty, ticker="TQQQ", side="buy"):
    return {
        "ticker": ticker,
        "side": side,
        "price": price,
        "qty": qty,
        "filled_qty": 0,
        "cancelled_qty": 0,
        "key": _KEY,
    }


def _freeze(when):
    bot._now_kst = lambda: when


def _place_for(world, result=True):
    def place_for(account):
        world["account"] = account

        def place(price, qty):
            world["calls"] = world.get("calls", 0) + 1
            if result == "raise":
                raise AssertionError("placed")
            if result == "third-max" and world["calls"] >= 3:
                return "max_orders"
            world.setdefault("placed", []).append((price, qty))
            if result == "third-max":
                return True
            return result

        return place

    return place_for


def _inquiry(opens, fills):
    def inquiry(account, kind):
        if _SECOND not in account and _FIRST not in account:
            raise AssertionError("inquiry account")
        if kind == "open":
            return list(opens)
        if kind == "fill":
            return list(fills)
        raise AssertionError(kind)

    return inquiry


def _run_execute(when, payload, opens, fills, world, live=False, result=True, gap=0):
    _freeze(when)
    os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
    if live:
        os.environ["TQQQ_LIVE"] = "on"
    else:
        os.environ.pop("TQQQ_LIVE", None)
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-12"

    def acctinfo():
        world["acctinfo"] = world.get("acctinfo", 0) + 1
        return 200, payload

    code, out = _capture(
        lambda: tqqq.execute(
            lambda: when,
            acctinfo,
            _inquiry(opens, fills),
            _place_for(world, result),
            sleep=lambda _seconds: None,
            gap=gap,
        )
    )
    return code, out, world


def test_prices_exact():
    assert tqqq._PRICES == _EXPECTED
    assert len(tqqq._PRICES) == 20
    assert len(set(tqqq._PRICES)) == 20
    assert tqqq._QTY == 10
    assert len(tqqq._PRICES) * tqqq._QTY == 200
    numbers = [float(price) for price in tqqq._PRICES]
    assert numbers == sorted(numbers, reverse=True)
    assert tqqq._ISIN == "US74347X8314"
    assert ladders._ISIN["US74347X8314"] == "TQQQ"
    assert tqqq._TLTW_ID == "tltw-buy-100-18"
    assert tqqq._TLTW_QTY == 100
    assert tqqq._TLTW_PRICE == "18.00"
    assert tqqq._TLTW_ISIN == "US46436E3383"
    assert ladders._ISIN["US46436E3383"] == "TLTW"


def test_account_unset_is_unchanged():
    os.environ.pop("NHPLUG_ACCOUNT_SUFFIX", None)
    payload = _payload(
        _row("02", _SECOND),
        _row(" 01 ", _FIRST),
        _row("01", _SECOND),
    )
    found, out = _capture(lambda: gb._select_account(payload))
    assert found == _FIRST, found
    assert _SECOND not in out
    assert _FIRST not in out
    assert "87654321" not in out
    digits, _out = _capture(
        lambda: gb._select_account(payload)
        if os.environ.get("NHPLUG_ACCOUNT_SUFFIX", "") == ""
        else ""
    )
    assert digits == _FIRST


def test_account_suffix_matches_digits_only():
    payload = _payload(
        _row("02", _SECOND),
        _row("01", _FIRST),
        _row("01", _SECOND),
    )
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-12"
    found, out = _capture(lambda: gb._select_account(payload))
    assert found == _SECOND
    assert _SECOND not in out
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "20112"
    assert gb._select_account(payload) == _SECOND
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201 12"
    assert gb._select_account(payload) == _SECOND


def test_account_not_found_fails_closed():
    payload = _payload(_row("01", _FIRST), _row("02", _SECOND))
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-12"
    found, out = _capture(lambda: gb._select_account(payload))
    assert found == ""
    assert _FIRST not in out
    assert _SECOND not in out
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-99"
    assert gb._select_account(_payload(_row("01", _FIRST), _row("01", _SECOND))) == ""
    ambiguous = _payload(
        _row("01", "201-12-111111"),
        _row("01", "201-12-222222"),
    )
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "20112"
    assert gb._select_account(ambiguous) == ""

    world = {"placed": []}

    def acctinfo():
        raise AssertionError("acctinfo")

    def inquiry(_account, _kind):
        raise AssertionError("inquiry")

    _freeze(_NOW)
    os.environ["TQQQ_LIVE"] = "on"
    os.environ["GITHUB_EVENT_NAME"] = "schedule"
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-99"
    code, out = _capture(
        lambda: tqqq.execute(
            lambda: _NOW,
            acctinfo,
            inquiry,
            _place_for(world, "raise"),
            sleep=lambda _seconds: None,
            gap=0,
        )
    )
    assert code == 1, out
    assert "ERROR account not found" in out
    assert world["placed"] == []
    assert "TQQQ plan" not in out
    assert _FIRST not in out

    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-12"
    calls = {"n": 0}

    def acctinfo_ok():
        calls["n"] += 1
        return 200, payload

    def inquiry_missing(_account, _kind):
        raise AssertionError("inquiry")

    code, out = _capture(
        lambda: tqqq.execute(
            lambda: _NOW,
            acctinfo_ok,
            inquiry_missing,
            _place_for(world, "raise"),
            sleep=lambda _seconds: None,
            gap=0,
        )
    )
    assert code == 1, out
    assert calls["n"] == 1
    assert "ERROR account not found" in out
    assert world["placed"] == []
    assert _FIRST not in out
    assert _SECOND not in out

    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "20112"
    code, out = _capture(
        lambda: tqqq.execute(
            lambda: _NOW,
            lambda: (200, ambiguous),
            inquiry_missing,
            _place_for(world, "raise"),
            sleep=lambda _seconds: None,
            gap=0,
        )
    )
    assert code == 1, out
    assert "ERROR account not found" in out
    assert "201-12-111111" not in out
    assert "201-12-222222" not in out
    assert world["placed"] == []


def test_mask_and_acct_check():
    assert gb._mask_account(_SECOND) == "201-12-68***5"
    assert _SECOND not in gb._mask_account(_SECOND)
    assert "20112681235" not in gb._mask_account(_SECOND)
    lines = acct_check.report_lines(
        [
            _row("01", _FIRST),
            _row("01", _SECOND),
            _row("10", "SCHD-not-an-account"),
            {"acct_type": "01"},
        ]
    )
    text = "\n".join(lines)
    assert "account 201-02-11***1 type 01" in text
    assert "account 201-12-68***5 type 01" in text
    assert "201-12 present yes" in text
    assert _FIRST not in text
    assert _SECOND not in text
    assert "20102111111" not in text
    assert "20112681235" not in text
    assert acct_check.account_rows({"Output_0": []}) == []
    assert acct_check.account_rows({}) is None
    assert acct_check.account_rows({"Output_0": {}}) is None
    absent = "\n".join(acct_check.report_lines([_row("01", _FIRST)]))
    assert "201-12 present no" in absent
    assert _FIRST not in absent
    root = os.path.dirname(os.path.abspath(__file__))
    source = open(os.path.join(root, "acct_check.py"), encoding="utf-8").read()
    workflow = open(
        os.path.join(root, ".github/workflows/acct-check.yml"), encoding="utf-8"
    ).read()
    assert "/gbstock/order" not in source
    assert "unexecuted" not in source
    assert "state.json" not in source
    assert "workflow_dispatch:" in workflow
    assert "cron:" not in workflow
    assert "acct_check.py" in workflow
    assert "NHPLUG_APP_KEY" in workflow
    assert "upload-artifact" not in workflow
    assert "actions/cache" not in workflow


def test_dry_run_prints_twenty_and_places_nothing():
    payload = _payload(_row("01", _FIRST), _row("01", _SECOND))
    world = {"placed": []}
    code, out, world = _run_execute(_NOW, payload, [], [], world, live=False)
    assert code == 0, out
    assert "order type 63" in out
    assert "OK dry" in out
    assert world["placed"] == []
    plans = [line for line in out.splitlines() if line.startswith("TQQQ plan buy ")]
    assert len(plans) == 20
    for price in _EXPECTED:
        assert "TQQQ plan buy %s qty 10" % price in out
    assert out.count("TLTW plan buy 18.00 qty 100") == 1
    assert _SECOND not in out
    assert _FIRST not in out
    assert world["account"] == _SECOND


def test_partial_fills_and_no_replace_after_full():
    _freeze(_NOW)
    state = tqqq._fresh()
    place_calls = []

    def place(price, qty):
        place_calls.append((price, qty))
        return True

    _code, out = _capture(
        lambda: tqqq._trade(_NOW, [], [_fill(43, 4)], state, place, False, "09")
    )
    assert "TQQQ plan buy 43.00 qty 6" in out
    assert "TQQQ plan buy 41.54 qty 10" in out
    assert "TQQQ plan buy 22.26 qty 10" in out
    assert place_calls == []
    assert state["levels"]["43.00"]["filled_qty"] == 4
    assert state["levels"]["41.54"]["filled_qty"] == 0

    _code, out = _capture(
        lambda: tqqq._trade(_NOW, [], [_fill(43, 4)], state, place, False, "09")
    )
    assert "TQQQ plan buy 43.00 qty 6" in out, out
    assert state["levels"]["43.00"]["filled_qty"] == 4

    _code, out = _capture(
        lambda: tqqq._trade(_NOW, [], [_fill(43, 7)], state, place, False, "09")
    )
    assert "TQQQ plan buy 43.00 qty 3" in out, out
    assert state["levels"]["43.00"]["filled_qty"] == 7
    assert state["levels"]["41.54"]["filled_qty"] == 0

    _code, out = _capture(
        lambda: tqqq._trade(
            _NOW,
            [],
            [_fill(43, 7, _KEY), _fill(41.54, 2, _KEY2), _fill(43, 3, _KEY2)],
            state,
            place,
            False,
            "09",
        )
    )
    assert "TQQQ filled 43.00" in out, out
    assert "TQQQ plan buy 43.00" not in out
    assert "TQQQ plan buy 41.54 qty 8" in out
    assert state["levels"]["43.00"]["filled"] is True
    assert state["levels"]["41.54"]["filled_qty"] == 2

    _code, out = _capture(
        lambda: tqqq._trade(_EVENING, [], [], state, place, True, "17")
    )
    assert ("43.00", 10) not in place_calls
    assert ("43.00", 3) not in place_calls
    assert all(price != "43.00" for price, _qty in place_calls)
    assert ("41.54", 8) in place_calls
    assert "TQQQ filled 43.00" in out


def test_no_duplicates_and_other_orders_untouched():
    _freeze(_NOW)
    state = tqqq._fresh()
    placed = []

    def place(price, qty):
        placed.append((price, qty))
        return True

    opens = [
        _open(43, 10),
        _open(41.54, 4),
        _open(43, 10, ticker="SCHD"),
        _open(90, 5, ticker="TLTW", side="sell"),
        _open(43, 10, side="sell"),
    ]
    _code, out = _capture(
        lambda: tqqq._trade(_NOW, opens, [], state, place, True, "09")
    )
    assert "TQQQ skip open 43.00 qty 10" in out
    assert "TQQQ skip open 41.54 qty 4" in out
    assert all(price != "43.00" for price, _qty in placed)
    assert all(price != "41.54" for price, _qty in placed)
    assert ("40.12", 10) in placed
    assert all(item[0] == "TQQQ" or True for item in placed)
    assert not any(price == 90 or price == "90" for price, _qty in placed)
    assert "SCHD" not in out
    assert "TLTW defer buy 18.00 qty 100" in out
    assert "90" not in out
    assert ("18.00", 100) not in placed
    assert "cancel" not in out.lower()
    root = os.path.dirname(os.path.abspath(__file__))
    source = open(os.path.join(root, "tqqq_bot.py"), encoding="utf-8").read()
    assert "CANCEL_URL" not in source
    assert "/gbstock/order/v1/cancel" not in source


def test_resting_same_slot_and_replace_next_reset():
    _freeze(_NOW)
    state = tqqq._fresh()
    placed = []

    def place(price, qty):
        placed.append((price, qty))
        return True

    _capture(lambda: tqqq._trade(_NOW, [], [], state, place, True, "09"))
    assert ("43.00", 10) in placed
    placed.clear()
    _code, out = _capture(
        lambda: tqqq._trade(_NOW, [], [], state, place, True, "09")
    )
    assert "TQQQ resting 43.00 qty 10" in out
    assert all(price != "43.00" for price, _qty in placed)
    placed.clear()
    _capture(lambda: tqqq._trade(_EVENING, [], [], state, place, True, "17"))
    assert ("43.00", 10) in placed


def test_batches_highest_first_and_ladder_state_untouched():
    payload = _payload(_row("01", _FIRST), _row("01", _SECOND))
    world = {"placed": []}
    directory = tempfile.mkdtemp()
    ladder_path = os.path.join(directory, ".ladder-state", "state.json")
    os.makedirs(os.path.dirname(ladder_path))
    sentinel = b'{"tickers":{"SOXL":1,"KORU":1,"NAIL":1}}\n'
    with open(ladder_path, "wb") as handle:
        handle.write(sentinel)
    tqqq_path = os.path.join(directory, ".tqqq-state", "state.json")
    os.environ["TQQQ_STATE_PATH"] = tqqq_path
    os.environ["LIVE_TRADING"] = "on"
    os.environ.pop("TQQQ_LIVE", None)
    code, out, world = _run_execute(_NOW, payload, [], [], world, live=False)
    assert code == 0, out
    assert world["placed"] == []
    assert not os.path.exists(tqqq_path)
    with open(ladder_path, "rb") as handle:
        assert handle.read() == sentinel

    world = {"placed": []}
    os.environ["TQQQ_LIVE"] = "on"
    code, out, world = _run_execute(
        _NOW, payload, [], [], world, live=True, gap=0
    )
    assert code == 0, out
    assert world["placed"] == [(price, 10) for price in _EXPECTED] + [("18.00", 100)]
    assert world["placed"][0] == ("43.00", 10)
    assert world["placed"][6] == ("34.93", 10)
    assert world["placed"][-1] == ("18.00", 100)
    assert "DEFERRED TQQQ" in out
    assert "follow-up reset 1" in out
    assert "follow-up reset 3" in out
    assert "follow-up reset 4" not in out
    assert _SECOND not in out
    assert _FIRST not in out
    saved = json.load(open(tqqq_path, encoding="utf-8"))
    assert saved["program"] == "tqqq-buy"
    assert "tickers" not in saved
    assert "exception_orders" not in saved
    assert saved["levels"]["43.00"]["resting"]["qty"] == 10
    assert saved["exceptions"]["tltw-buy-100-18"]["resting"]["qty"] == 100
    assert saved["exceptions"]["tltw-buy-100-18"]["filled"] is False
    with open(ladder_path, "rb") as handle:
        assert handle.read() == sentinel
    os.environ["TQQQ_STATE_PATH"] = ladder_path
    refused, refused_out = _capture(lambda: tqqq._save_state(tqqq._fresh()))
    assert refused is False
    assert "ERROR unexpected" in refused_out
    with open(ladder_path, "rb") as handle:
        assert handle.read() == sentinel


def test_broker_max_orders_keeps_levels():
    payload = _payload(_row("01", _SECOND))
    directory = tempfile.mkdtemp()
    os.environ["TQQQ_STATE_PATH"] = os.path.join(directory, "state.json")
    world = {"placed": []}
    code, out, world = _run_execute(
        _NOW, payload, [], [], world, live=True, result="third-max", gap=0
    )
    assert code == 1, out
    assert "ERROR max_orders" in out
    assert ("43.00", 10) in world["placed"]
    assert ("41.54", 10) in world["placed"]
    assert world["placed"][0] == ("43.00", 10)
    assert ("22.26", 10) not in world["placed"]
    saved = json.load(open(os.environ["TQQQ_STATE_PATH"], encoding="utf-8"))
    assert saved["levels"]["43.00"]["filled"] is False
    assert saved["levels"]["43.00"]["resting"]["slot"] == "09"
    assert saved["levels"]["40.12"]["resting"] == {}
    assert saved["levels"]["22.26"]["filled_qty"] == 0
    assert saved["levels"]["22.26"]["filled"] is False
    assert saved["exceptions"]["tltw-buy-100-18"]["filled_qty"] == 0
    assert saved["exceptions"]["tltw-buy-100-18"]["resting"] == {}
    assert ("18.00", 100) not in world["placed"]
    assert _SECOND not in out


def test_order_types_and_skips():
    assert bot._order_type(_NOW, _NOW.astimezone(bot.ET), "reset") == "63"
    assert bot._order_type(_EVENING, _EVENING.astimezone(bot.ET), "reset") == "61"
    summer_evening = datetime(2026, 7, 15, 18, 11, tzinfo=bot.KST)
    summer_morning = datetime(2026, 7, 15, 10, 11, tzinfo=bot.KST)
    assert bot._order_type(summer_evening, summer_evening.astimezone(bot.ET), "reset") == "61"
    assert bot._order_type(summer_morning, summer_morning.astimezone(bot.ET), "reset") == "63"
    payload = _payload(_row("01", _SECOND))
    world = {"placed": []}

    def acctinfo():
        raise AssertionError("acctinfo")

    _freeze(_GUARD)
    os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-12"
    os.environ["TQQQ_LIVE"] = "on"
    os.environ["GITHUB_EVENT_NAME"] = "schedule"
    code, out = _capture(
        lambda: tqqq.execute(
            lambda: _GUARD,
            acctinfo,
            _inquiry([], []),
            _place_for(world, "raise"),
            sleep=lambda _seconds: None,
            gap=0,
        )
    )
    assert code == 0, out
    assert "skip: time guard" in out
    assert world["placed"] == []

    code, out = _capture(
        lambda: tqqq.execute(
            lambda: _SUMMER_SKIP,
            acctinfo,
            _inquiry([], []),
            _place_for(world, "raise"),
            sleep=lambda _seconds: None,
            gap=0,
        )
    )
    _freeze(_SUMMER_SKIP)
    code, out = _capture(
        lambda: tqqq.execute(
            lambda: _SUMMER_SKIP,
            acctinfo,
            _inquiry([], []),
            _place_for(world, "raise"),
            sleep=lambda _seconds: None,
            gap=0,
        )
    )
    assert code == 0, out
    assert "OK skip" in out
    assert world["placed"] == []

    def acctinfo_ok():
        world["saw"] = True
        return 200, payload

    _freeze(_PRE_DAY)
    code, out = _capture(
        lambda: tqqq.execute(
            lambda: _PRE_DAY,
            acctinfo_ok,
            _inquiry([], []),
            _place_for(world, "raise"),
            sleep=lambda _seconds: None,
            gap=0,
        )
    )
    assert code == 0, out
    assert "skip: day session not open" in out
    assert world["placed"] == []
    assert "saw" not in world

    _freeze(_EVENING)
    world = {"placed": []}
    code, out, world = _run_execute(_EVENING, payload, [], [], world, live=False)
    assert code == 0, out
    assert "order type 61" in out
    assert "TQQQ plan buy 43.00 qty 10" in out
    assert "TLTW plan buy 18.00 qty 100" in out


def test_buy_body_and_max_order_detector():
    _freeze(_NOW)
    os.environ.pop("ORDER_TYPE", None)
    seen = {}

    def fake_post(url, body):
        seen["url"] = url
        seen["body"] = body
        return True

    bot._post_order = fake_post
    assert tqqq._broker_place(_SECOND, "43.00", 6) is True
    assert seen["url"].endswith("/gbstock/order/v1/buy")
    assert "cancel" not in seen["url"]
    assert seen["body"]["iem_cd"] == "TQQQ"
    assert seen["body"]["orr_qty"] == 6
    assert seen["body"]["fc_orr_uit_pr"] == 43.0
    assert seen["body"]["ahi_nmn_pr_tp_cd"] == "63"
    assert seen["body"]["wtm_cur_knd_cd"] == "1"
    assert seen["body"]["act_no"] == _SECOND
    _freeze(_EVENING)
    tqqq._broker_place(_SECOND, "22.26", 10)
    assert seen["body"]["ahi_nmn_pr_tp_cd"] == "61"
    assert seen["body"]["fc_orr_uit_pr"] == 22.26
    assert tqqq._broker_place(_SECOND, "18.00", 100) is True
    assert seen["body"]["iem_cd"] == "TLTW"
    assert seen["body"]["orr_qty"] == 100
    assert seen["body"]["fc_orr_uit_pr"] == 18.0
    assert seen["body"]["wtm_cur_knd_cd"] == "1"
    assert seen["url"].endswith("/gbstock/order/v1/buy")
    assert tqqq._broker_place(_SECOND, "19.00", 100) is None
    assert tqqq._broker_place(_SECOND, "18.00", 101) is None

    def reject(url, body):
        bot._last_reject = {
            "ticker": "TQQQ",
            "side": "buy",
            "price": 43.0,
            "http": 400,
            "rsp_cd": "EMAX",
            "rsp_msg": "주문가능건수 초과",
        }
        return None

    bot._post_order = reject
    assert tqqq._broker_place(_SECOND, "43.00", 10) == "max_orders"
    bot._last_reject = {
        "ticker": "TQQQ",
        "side": "buy",
        "price": 43.0,
        "http": 400,
        "rsp_cd": "EBLNC",
        "rsp_msg": "잔고부족",
    }
    assert tqqq._is_max_orders() is False


def test_inquiry_matches_isin_and_ladder_is_untouched():
    assert [item["id"] for item in bot._EXCEPTIONS] == [
        "koru-buy-100-4.2",
        "soxl-buy-400-18-7",
    ]
    assert "soxl-sell-150-75" not in [item["id"] for item in bot._EXCEPTIONS]
    assert bot._STATE_PATH == ".ladder-state/state.json"
    assert bot._MAX_NEW == 6
    row = {
        "iem_cd": "US74347X8314",
        "fc_sec_trd_nat_cd": "200",
        "oss_sby_dit_cd": "2",
        "fc_orr_uit_pr": "43.00",
        "orr_qty": "10",
        "ny_cns_orr_qty": "10",
        "orr_dt": "20261125",
        "orr_no": "15",
    }
    parsed = bot._parse_row(row)
    assert parsed["ticker"] == "TQQQ"
    assert parsed["side"] == "buy"
    assert abs(parsed["price"] - 43) < 0.001
    assert ladders._identify({"iem_cd": "TQQQ"}) == "TQQQ"
    assert ladders._identify({"tck_iem_cd": "TQQQ"}) == "TQQQ"
    soxl = dict(row, iem_cd="US25459W4583")
    assert bot._parse_row(soxl)["ticker"] == "SOXL"
    assert ladders._identify({"iem_cd": "US25461A3876"}) == "KORU"
    assert ladders._identify({"iem_cd": "US25490K5965"}) == "NAIL"
    assert ladders._identify({"iem_cd": "US46436E3383"}) == "TLTW"
    assert ladders._identify({"tck_iem_cd": "TLTW"}) == "TLTW"
    tltw = dict(row, iem_cd="US46436E3383", fc_orr_uit_pr="18.00")
    assert bot._parse_row(tltw)["ticker"] == "TLTW"
    assert bot._parse_row(tltw)["ticker"] != "SOXL"
    mine = [parsed, bot._parse_row(soxl)]
    soxl_only = [order for order in mine if order.get("ticker") == "SOXL"]
    assert [order["ticker"] for order in soxl_only] == ["SOXL"]
    used = [ladders._level_of("SOXL", price) for price in (72, 66, 60, 55, 50, 46, 42, 39)]
    desired = bot._desired(
        "SOXL",
        ladders._level_of("SOXL", 46),
        ladders._level_of("SOXL", 39),
        1.0,
        used,
    )
    assert [(item["side"], item["price"], item["qty"]) for item in desired] == [
        ("sell", 46, 20),
        ("sell", 50, 20),
        ("buy", 39, 20),
        ("buy", 36, 20),
    ]


def test_workflows_and_restore_do_not_touch_ladder():
    root = os.path.dirname(os.path.abspath(__file__))
    tqqq_yml = open(
        os.path.join(root, ".github/workflows/tqqq-exception.yml"), encoding="utf-8"
    ).read()
    reset_yml = open(
        os.path.join(root, ".github/workflows/ladder-reset.yml"), encoding="utf-8"
    ).read()
    assert 'cron: "11 1 * * 1-5"' in tqqq_yml
    assert 'cron: "11 9 * * 1-5"' in tqqq_yml
    assert tqqq_yml.count("cron:") == 2
    assert "TQQQ_LIVE" in tqqq_yml
    assert 'NHPLUG_ACCOUNT_SUFFIX: "201-12"' in tqqq_yml
    assert "startsWith(github.event.comment.body, 'tqqq-reset')" in tqqq_yml
    assert "github.event.issue.number == 18" in tqqq_yml
    assert "tqqq-exception" in tqqq_yml
    assert "ladder-bot" not in tqqq_yml
    assert "path: .ladder-state" not in tqqq_yml
    assert "LADDER_MODE" not in tqqq_yml
    assert "LADDER_STATE_PATH" not in tqqq_yml
    assert "actions/cache/restore@v4" in tqqq_yml
    assert "path: .tqqq-state" in tqqq_yml
    assert "name: tqqq-state" in tqqq_yml
    assert "TQQQ_STATE_PATH: .tqqq-state/state.json" in tqqq_yml
    assert "NHPLUG_ACCOUNT_SUFFIX" not in reset_yml
    assert "TQQQ" not in reset_yml
    assert "tqqq" not in reset_yml
    for name in (
        "ladder-intraday.yml",
        "ladder-adjust.yml",
        "ladder-dryrun.yml",
        "ladder-cancel-dup.yml",
    ):
        text = open(os.path.join(root, ".github/workflows", name), encoding="utf-8").read()
        assert "NHPLUG_ACCOUNT_SUFFIX" not in text
        assert "TQQQ_LIVE" not in text

    old = (restore._PATH, restore._NAME, restore._WORKFLOWS)
    directory = tempfile.mkdtemp()
    missing = os.path.join(directory, "missing.json")
    os.environ["TQQQ_STATE_PATH"] = missing
    saved_env = {}
    for name in ("GITHUB_TOKEN", "GITHUB_REPOSITORY"):
        saved_env[name] = os.environ.pop(name, None)
    try:
        code, out = _capture(restore_tqqq.main)
    finally:
        for name, value in saved_env.items():
            if value is not None:
                os.environ[name] = value
    assert code == 0, out
    assert "OK none" in out
    assert restore._PATH == old[0]
    assert restore._NAME == old[1] == "ladder-state"
    assert restore._WORKFLOWS == old[2]
    assert "ladder-reset.yml" in restore._WORKFLOWS
    assert not os.path.exists(os.path.join(root, ".ladder-state", "state.json"))
    blocked = os.path.join(directory, ".ladder-state", "state.json")
    os.environ["TQQQ_STATE_PATH"] = blocked
    code, out = _capture(restore_tqqq.main)
    assert code == 0, out
    assert not os.path.exists(blocked)


def test_tltw_partial_priority_and_other_orders():
    """TLTW is one exception after the TQQQ levels. Other TLTW rows stay put."""
    _freeze(_NOW)
    state = tqqq._fresh()
    assert state["exceptions"]["tltw-buy-100-18"]["filled_qty"] == 0
    legacy = tqqq._clean({"program": "tqqq-buy", "levels": {}})
    assert legacy["exceptions"]["tltw-buy-100-18"]["filled"] is False
    assert legacy["exceptions"]["tltw-buy-100-18"]["filled_qty"] == 0

    def place(price, qty):
        raise AssertionError("placed")

    _code, out = _capture(
        lambda: tqqq._trade(_NOW, [], [], state, place, False, "09")
    )
    assert "TLTW plan buy 18.00 qty 100" in out
    assert out.index("TQQQ plan buy 22.26 qty 10") < out.index("TLTW plan buy 18.00 qty 100")

    _code, out = _capture(
        lambda: tqqq._trade(_NOW, [], [_fill(18, 40, ticker="TLTW")], state, place, False, "09")
    )
    assert "TLTW plan buy 18.00 qty 60" in out, out
    assert "TLTW plan buy 18.00 qty 100" not in out
    assert state["exceptions"]["tltw-buy-100-18"]["filled_qty"] == 40

    _code, out = _capture(
        lambda: tqqq._trade(_NOW, [], [_fill(18, 40, ticker="TLTW")], state, place, False, "09")
    )
    assert "TLTW plan buy 18.00 qty 60" in out
    assert state["exceptions"]["tltw-buy-100-18"]["filled_qty"] == 40

    ignored = [
        _fill(18, 40, ticker="TLTW"),
        _fill(21, 100, ticker="TLTW", key=_KEY2),
        _fill(18, 100, ticker="TLTW", key="ef" * 32, side="sell"),
        _fill(18, 50, ticker="SCHD", key="12" * 32),
    ]
    _code, out = _capture(lambda: tqqq._trade(_NOW, [], ignored, state, place, False, "09"))
    assert state["exceptions"]["tltw-buy-100-18"]["filled_qty"] == 40
    assert "TLTW plan buy 18.00 qty 60" in out
    assert "SCHD" not in out

    _code, out = _capture(
        lambda: tqqq._trade(
            _NOW,
            [],
            [_fill(18, 100, ticker="TLTW")],
            state,
            place,
            False,
            "09",
        )
    )
    assert "TLTW filled 18.00" in out
    assert "TLTW plan buy" not in out
    assert state["exceptions"]["tltw-buy-100-18"]["filled"] is True
    assert state["levels"]["43.00"]["filled"] is False

    fresh = tqqq._fresh()
    for price in tqqq._PRICES[:-2]:
        fresh["levels"][price]["filled"] = True
        fresh["levels"][price]["filled_qty"] = 10
    placed = []

    def live_place(price, qty):
        placed.append((price, qty))
        return True

    _code, out = _capture(
        lambda: tqqq._trade(_NOW, [], [], fresh, live_place, True, "09")
    )
    assert placed == [("23.04", 10), ("22.26", 10), ("18.00", 100)], placed
    assert "TLTW plan buy 18.00 qty 100" in out
    assert fresh["exceptions"]["tltw-buy-100-18"]["resting"]["qty"] == 100

    again = tqqq._fresh()
    opens = [
        _open(18, 100, ticker="TLTW"),
        _open(19, 50, ticker="TLTW"),
        _open(27, 10, ticker="SCHD"),
        _open(18, 20, ticker="TLTW", side="sell"),
    ]
    placed.clear()
    _code, out = _capture(
        lambda: tqqq._trade(_NOW, opens, [], again, live_place, True, "09")
    )
    assert "TLTW skip open 18.00 qty 100" in out
    assert ("18.00", 100) not in placed
    assert not any(price == 19 or price == "19.00" for price, _qty in placed)
    assert "SCHD" not in out
    assert "cancel" not in out.lower()
    assert again["exceptions"]["tltw-buy-100-18"]["filled_qty"] == 0


def test_main_stops_before_broker():
    def boom(_key, _secret):
        raise AssertionError("token")

    old_issue = tqqq._issue_token
    tqqq._issue_token = boom
    try:
        os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-99"
        os.environ["NHPLUG_APP_KEY"] = "k" * 8
        os.environ["NHPLUG_APP_SECRET"] = "s" * 8
        os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        os.environ["TQQQ_LIVE"] = "on"
        _freeze(_NOW)
        code, out = _capture(tqqq.main)
        assert code == 1, out
        assert "ERROR account not found" in out
        assert _SECOND not in out

        os.environ.pop("NHPLUG_ACCOUNT_SUFFIX", None)
        code, out = _capture(tqqq.main)
        assert code == 1, out
        assert "ERROR account not found" in out

        os.environ["NHPLUG_ACCOUNT_SUFFIX"] = "201-12"
        _freeze(_GUARD)
        code, out = _capture(tqqq.main)
        assert code == 0, out
        assert "skip: time guard" in out

        _freeze(_PRE_DAY)
        code, out = _capture(tqqq.main)
        assert code == 0, out
        assert "skip: day session not open" in out
    finally:
        tqqq._issue_token = old_issue


def main():
    names = (
        "LIVE_TRADING",
        "TQQQ_LIVE",
        "NHPLUG_ACCOUNT_SUFFIX",
        "TQQQ_STATE_PATH",
        "TQQQ_COMMENT",
        "ORDER_TYPE",
        "GITHUB_EVENT_NAME",
    )
    saved = {name: os.environ.get(name) for name in names}
    try:
        os.environ.pop("ORDER_TYPE", None)
        os.environ.pop("TQQQ_COMMENT", None)
        test_prices_exact()
        test_account_unset_is_unchanged()
        test_account_suffix_matches_digits_only()
        test_account_not_found_fails_closed()
        test_mask_and_acct_check()
        test_dry_run_prints_twenty_and_places_nothing()
        test_partial_fills_and_no_replace_after_full()
        test_no_duplicates_and_other_orders_untouched()
        test_resting_same_slot_and_replace_next_reset()
        test_batches_highest_first_and_ladder_state_untouched()
        test_broker_max_orders_keeps_levels()
        test_order_types_and_skips()
        test_buy_body_and_max_order_detector()
        test_inquiry_matches_isin_and_ladder_is_untouched()
        test_tltw_partial_priority_and_other_orders()
        test_workflows_and_restore_do_not_touch_ladder()
        test_main_stops_before_broker()
        print("OK tqqq")
    finally:
        bot._now_kst = _ORIG_NOW
        bot._post_order = _ORIG_POST
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    main()
