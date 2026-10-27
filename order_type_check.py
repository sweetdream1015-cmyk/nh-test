"""Read-only order-type check for open NAIL, KORU, and SOXL orders.

Uses the same unexecuted inquiry as ladder_bot (today and yesterday, KST,
ost_cns_dit 2). Does not call buy, sell, cancel, or modify.

Prints field names and code counts only. No prices, quantities, order
numbers, accounts, or response bodies.
"""

import os
import sys
from collections import Counter
from datetime import datetime

import gb_readonly_check as gb
import ladder_bot as bot

# Buy/sell Input_0 from the gbstock spec. 00/61/62/63 are the limit codes.
# Neither request has a day-order, GTC, expiry, or valid-until field.
SPEC = (
    "spec ahi_nmn_pr_tp_cd 00 is 지정가 (limit; no session named); 61 is premarket limit; 62 is after-hours limit; 63 is day-session limit",
    "spec buy and sell have no day-versus-GTC, expiry, or valid-until parameter",
    "spec the only type field on buy and sell is ahi_nmn_pr_tp_cd; fc_orr_uit_pr is required for 00, 61, 62, and 63",
)
_TICKERS = ("NAIL", "KORU", "SOXL")
_SESSION = (
    "ahi_nmn_pr_tp_cd",
    "ahi_nmn_pr_tp_cd_nm",
    "fc_mkt_dit_cd",
    "fc_mkt_dit_cd_nm",
    "fc_sec_trd_nat_cd",
    "mkt_dit_cd_nm",
)


def _blocked(name):
    low = name.lower()
    return any(
        part in low
        for part in (
            "qty",
            "uit_pr",
            "cns_pr",
            "orr_pr",
            "bse_pr",
            "amt",
            "act_no",
            "acct",
            "orr_no",
            "iem_cd",
            "cus_no",
        )
    )


def _related(name):
    if not isinstance(name, str) or _blocked(name):
        return False
    low = name.lower()
    if "pr_tp_cd" in low or "ahi_" in low or "ato_" in low or "dit_cd" in low:
        return True
    return any(
        part in low
        for part in ("gtc", "valid", "expir", "vld_dt", "day_order", "dayorder")
    )


def _session_name(name):
    if name in _SESSION:
        return True
    low = name.lower()
    return "mkt" in low or "sess" in low or low.startswith("ahi_nmn_pr_tp")


def _scalar(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not value.is_integer():
            return None
        return str(int(value))
    if isinstance(value, str):
        text = " ".join(value.split())
        if not text:
            return "(blank)"
        if len(text) > 40:
            return None
        for secret in gb._secrets:
            if isinstance(secret, str) and len(secret) >= 4 and secret in text:
                return None
        return text
    return None


def _date(value):
    text = _scalar(value)
    if text and len(text) == 8 and text.isdigit():
        return text
    return None


def report(rows):
    """Print the four read-only summaries. `rows` are raw unexecuted objects."""
    counts = {}
    dates = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        found = _date(row.get("orr_dt"))
        if found:
            dates.add(found)
        for name, value in row.items():
            if not _related(name):
                continue
            text = _scalar(value)
            if text is None:
                continue
            counts.setdefault(name, Counter())[text] += 1
    names = sorted(counts)
    print("fields " + (" ".join(names) if names else "none"))
    for name in names:
        for value, count in sorted(counts[name].items()):
            print(f"{name} {value} {count}")
    print("orr_dt " + (" ".join(sorted(dates)) if dates else "none"))
    present = sorted(
        {
            name
            for row in rows
            if isinstance(row, dict)
            for name in row
            if _session_name(name) and not _blocked(name)
        }
    )
    session_counts = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for name in present:
            text = _scalar(row.get(name))
            if text is None:
                continue
            session_counts.setdefault(name, Counter())[text] += 1
    if not session_counts:
        print("session none")
        return
    for name in sorted(session_counts):
        for value, count in sorted(session_counts[name].items()):
            print(f"session {name} {value} {count}")


def _open_rows(account, now_kst):
    merged = []
    seen = set()
    for orr_dt in bot._dates(now_kst):
        rows = bot._pages(
            gb.UNEXECUTED_URL,
            {
                "orr_dt": orr_dt,
                "act_no": account,
                "oss_sby_dit_cd": "0",
                "sot_dit": "0",
                "ost_cns_dit": "2",
            },
            "Output_0",
        )
        if rows is None:
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            parsed = bot._parse_row(row)
            if not parsed or parsed["ticker"] not in _TICKERS:
                continue
            if parsed["qty"] <= 0:
                continue
            if parsed["key"] in seen:
                continue
            seen.add(parsed["key"])
            merged.append(row)
    return merged


def main():
    for line in SPEC:
        print(line)
    key = os.environ.get("NHPLUG_APP_KEY", "")
    secret = os.environ.get("NHPLUG_APP_SECRET", "")
    if not key.strip() or not secret.strip():
        print("secrets missing")
        return 1
    gb._key = key
    gb._secret = secret
    gb._secrets.extend((key, secret))
    gb._issue_token = bot._quiet_issue
    gb._emit = lambda name, status, payload: gb._rsp(payload)
    now_kst = datetime.now(bot.KST)
    token = bot._load_token() or bot._quiet_issue(key, secret)
    if not token:
        print("ERROR http")
        return 1
    gb._token = token
    gb._secrets.append(token)
    status, _headers, payload = gb._post_bearer(gb.ACCTINFO_URL, gb._json_body({}), {})
    if status != 200:
        print("ERROR http")
        return 1
    account = gb._select_account(payload)
    if not account:
        print("ERROR unexpected")
        return 1
    rows = _open_rows(account, now_kst)
    if rows is None:
        print("ERROR http")
        return 1
    report(rows)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("ERROR unexpected")
        sys.exit(1)
