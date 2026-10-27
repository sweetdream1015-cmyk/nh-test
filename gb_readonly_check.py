"""Read-only NH PLUG overseas check: token, acctinfo, balance, unexecuted.

Prints HTTP status, rsp_cd, rsp_msg, row counts, and NAIL/SOXL/KORU
presence or unexecuted counts. Never prints credentials, tokens, account
numbers, balances, prices, or response bodies. No orders and no files.

Response blocks (openapi field names, not inferred):
- POST /n2/acctinfo: Output_0[] {acct_no, acct_type} plus rsp_cd, rsp_msg.
  acct_type "01" is production general. Output_0 is required.
- POST /gbstock/inquiry/v1/balance: Output_0 is the aggregate object
  (not printed). Holdings rows are Output_1[] and iem_cd is the ticker.
  The spec omits a block when it has no rows, so a missing Output_1 is
  zero holdings. Output_1 present but not an array is a missing block.
- POST /gbstock/inquiry/v1/unexecuted: Output_0[] with iem_cd and
  fc_sec_trd_nat_cd. Same absent-block rule. ost_cns_dit "2" is unexecuted.
- gbstock schemas name the status envelope message.msg_code / message.usr_msg.
  rsp_cd / rsp_msg are the common and x-response-status fields. Printed
  status uses rsp_cd / rsp_msg when present, otherwise those message fields.
- Continuation uses response headers cts and cts_flag (Y = next, N = stop).
  The next request repeats both headers. rsp_cd 00165 and 00218 also mean
  another page. Stop when the full cts value repeats.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

LIVE = "https://api.nhplug.com:8443"
TOKEN_URL = LIVE + "/oauth2/token"
ACCTINFO_URL = LIVE + "/n2/acctinfo"
BALANCE_URL = LIVE + "/gbstock/inquiry/v1/balance"
UNEXECUTED_URL = LIVE + "/gbstock/inquiry/v1/unexecuted"

TICKERS = ("NAIL", "SOXL", "KORU")
MIN_INTERVAL = 0.3
_CONTINUE = {"00165", "00218"}
_KST = timezone(timedelta(hours=9))

_last_call = 0.0
_secrets = []
_token = ""
_key = ""
_secret = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _throttle():
    global _last_call
    if _last_call:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
    _last_call = time.monotonic()


def _read_json(raw):
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _header_map(headers):
    out = {}
    if headers is None:
        return out
    for key, value in headers.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key.lower()] = value
    return out


def _post(url, body, headers):
    _throttle()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=20) as resp:
            return resp.status, _header_map(resp.headers), _read_json(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        return exc.code, _header_map(exc.headers), _read_json(raw)
    except Exception:
        return None, {}, {}


def _one_line(value):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:80]


def _redact(text):
    cleaned = text
    for secret in _secrets:
        if isinstance(secret, str) and len(secret) >= 8 and secret in cleaned:
            cleaned = cleaned.replace(secret, "")
    return " ".join(cleaned.split())[:80]


def _rsp(payload):
    if not isinstance(payload, dict):
        return "", ""
    rsp_cd = payload.get("rsp_cd")
    rsp_msg = payload.get("rsp_msg")
    message = payload.get("message")
    if not isinstance(message, dict):
        message = {}
    if not (isinstance(rsp_cd, str) and rsp_cd.strip()):
        rsp_cd = message.get("msg_code")
    if not (isinstance(rsp_msg, str) and rsp_msg.strip()):
        rsp_msg = message.get("usr_msg")
    return _redact(_one_line(rsp_cd)), _redact(_one_line(rsp_msg))


def _emit(name, status, payload):
    rsp_cd, rsp_msg = _rsp(payload)
    print(f"{name} HTTP {status}")
    print(f"{name} rsp_cd {rsp_cd}")
    print(f"{name} rsp_msg {rsp_msg}")
    return rsp_cd, rsp_msg


def _empty_ok(rsp_msg):
    return "완료" in rsp_msg or "없습니다" in rsp_msg


def _json_body(input0):
    return json.dumps({"Input_0": input0}, ensure_ascii=False).encode("utf-8")


def _auth_headers(token, extra):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }
    headers.update(extra)
    return headers


def _post_bearer(url, body, extra):
    global _token
    status, headers, payload = _post(url, body, _auth_headers(_token, extra))
    if status != 401:
        return status, headers, payload
    refreshed = _issue_token(_key, _secret)
    if not refreshed:
        return status, headers, payload
    _token = refreshed
    return _post(url, body, _auth_headers(_token, extra))


def _issue_token(key, secret):
    query = urllib.parse.urlencode(
        (
            ("appkey", key),
            ("appsecretkey", secret),
            ("grant_type", "client_credentials"),
            ("scope", "oob"),
        )
    )
    status, _, payload = _post(
        TOKEN_URL + "?" + query,
        b"",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status is None:
        print("token request failed")
        return ""
    _emit("token", status, payload)
    if status != 200 or not isinstance(payload, dict):
        return ""
    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip():
        return ""
    _secrets.append(token)
    return token


def _select_account(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("Output_0"), list):
        return ""
    cust_no = payload.get("cust_no")
    if isinstance(cust_no, str) and len(cust_no) >= 8:
        _secrets.append(cust_no)
    for row in payload["Output_0"]:
        if not isinstance(row, dict):
            continue
        acct_type = row.get("acct_type")
        acct_no = row.get("acct_no")
        if not isinstance(acct_type, str) or acct_type.strip() != "01":
            continue
        if isinstance(acct_no, str) and acct_no.strip():
            account = acct_no.strip()
            _secrets.append(account)
            return account
    return ""


def _list_rows(payload, block_name):
    if not isinstance(payload, dict):
        return None, "missing"
    if block_name not in payload:
        return [], "absent"
    value = payload.get(block_name)
    if isinstance(value, list):
        return value, "ok"
    return None, "badtype"


def _needs_next(flag, cts, rsp_cd, prev_cts):
    if not cts or (prev_cts is not None and cts == prev_cts):
        return False
    if flag.upper() == "N":
        return False
    if flag.upper() == "Y":
        return True
    return rsp_cd in _CONTINUE


def _paged(name, url, input0, block_name):
    rows = []
    prev_cts = None
    cts = ""
    flag = ""
    for _ in range(30):
        extra = {}
        if cts:
            extra["cts"] = cts
            if flag:
                extra["cts_flag"] = flag
        status, headers, payload = _post_bearer(url, _json_body(input0), extra)
        if status is None:
            print(f"{name} request failed")
            return None
        rsp_cd, rsp_msg = _emit(name, status, payload)
        if status != 200:
            return None
        page, state = _list_rows(payload, block_name)
        if state == "badtype" or page is None:
            return None
        new_cts = headers.get("cts", "").strip()
        new_flag = headers.get("cts_flag", "").strip()
        # A repeated full cts is not a new page. Keep earlier rows only.
        if prev_cts is not None and new_cts and new_cts == prev_cts:
            return rows
        rows.extend(page)
        continued = _needs_next(new_flag, new_cts, rsp_cd, prev_cts)
        if not continued:
            if new_flag.upper() == "Y" and not new_cts:
                return None
            if rsp_cd in _CONTINUE and not new_cts and new_flag.upper() != "N":
                return None
            if state == "absent" and not rows and not _empty_ok(rsp_msg):
                return None
            return rows
        prev_cts = new_cts
        cts = new_cts
        flag = new_flag
    return None


def _country(row):
    if not isinstance(row, dict):
        return ""
    code = row.get("fc_sec_trd_nat_cd")
    if isinstance(code, str):
        return code.strip()
    return ""


def _ticker(row):
    """iem_cd is the ticker (spec example AAPL, length 12). Drop padding."""
    if not isinstance(row, dict):
        return ""
    code = row.get("iem_cd")
    if isinstance(code, int) and not isinstance(code, bool):
        code = str(code)
    if not isinstance(code, str):
        return ""
    cleaned = "".join(ch for ch in code if ord(ch) >= 32)
    return cleaned.strip()


def _code(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _num(value):
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = str(int(value)) if value.is_integer() else format(value, "f").rstrip("0").rstrip(".")
    elif isinstance(value, str):
        text = value.strip()
    else:
        return ""
    return _redact(text)


# oss_sby_dit_cd on the unexecuted request and Output_0: 1.매도 2.매수
_SIDE = {"1": "sell", "2": "buy"}


def _print_unexecuted(rows):
    # Keys only. Values of the first row are not printed here.
    if rows and isinstance(rows[0], dict):
        keys = [key for key in rows[0].keys() if isinstance(key, str)]
        print("unexecuted fields " + " ".join(keys))
    else:
        print("unexecuted fields")
    counts = {}
    order = []
    for row in rows:
        symbol = _redact(_ticker(row) or "(blank)")
        if symbol not in counts:
            order.append(symbol)
            counts[symbol] = 0
        counts[symbol] += 1
    for symbol in order:
        print(f"unexecuted symbol {symbol} {counts[symbol]}")
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _redact(_ticker(row) or "(blank)")
        side = _code(row.get("oss_sby_dit_cd"))
        side_name = _SIDE.get(side, "")
        status = _code(row.get("oss_ato_orr_sts_cd"))
        qty = _num(row.get("orr_qty"))
        rem = _num(row.get("ny_cns_orr_qty"))
        price = _num(row.get("fc_orr_uit_pr"))
        parts = [symbol, f"side {side}"]
        if side_name:
            parts.append(side_name)
        if status:
            parts.append(f"status {status}")
        parts.append(f"qty {qty}")
        parts.append(f"rem {rem}")
        parts.append(f"px {price}")
        print(" ".join(parts))


def _balance_rows(rows):
    kept = []
    for row in rows:
        country = _country(row)
        if country and country != "200":
            continue
        kept.append(row)
    return kept


def _unexecuted_rows(rows):
    return [row for row in rows if _country(row) == "200"]


def main():
    global _token, _key, _secret
    key = os.environ.get("NHPLUG_APP_KEY", "")
    secret = os.environ.get("NHPLUG_APP_SECRET", "")
    if not key.strip() or not secret.strip():
        print("secrets missing")
        return 1
    _key = key
    _secret = secret
    _secrets.extend((key, secret))

    token = _issue_token(key, secret)
    if not token:
        return 1
    _token = token

    status, _, payload = _post_bearer(ACCTINFO_URL, _json_body({}), {})
    if status is None:
        print("acctinfo request failed")
        return 1
    _emit("acctinfo", status, payload)
    if status != 200:
        return 1
    account = _select_account(payload)
    if not account:
        return 1

    balance = _paged(
        "balance",
        BALANCE_URL,
        {
            "act_no": account,
            "qut_iqr_dit_cd": "9",
            "fc_sec_trd_nat_cd": "200",
            "cur_cd": "USD",
        },
        "Output_1",
    )
    if balance is None:
        return 1
    holdings = _balance_rows(balance)

    order_date = datetime.now(_KST).strftime("%Y%m%d")
    unexecuted = _paged(
        "unexecuted",
        UNEXECUTED_URL,
        {
            "orr_dt": order_date,
            "act_no": account,
            "oss_sby_dit_cd": "0",
            "sot_dit": "0",
            "ost_cns_dit": "2",
        },
        "Output_0",
    )
    if unexecuted is None:
        return 1
    open_orders = _unexecuted_rows(unexecuted)

    held = {ticker: 0 for ticker in TICKERS}
    for row in holdings:
        ticker = _ticker(row)
        if ticker in held:
            held[ticker] += 1
    open_counts = {ticker: 0 for ticker in TICKERS}
    for row in open_orders:
        ticker = _ticker(row).upper()
        if ticker in open_counts:
            open_counts[ticker] += 1

    print(f"holdings {len(holdings)}")
    print(f"unexecuted {len(open_orders)}")
    for ticker in TICKERS:
        present = "yes" if held[ticker] else "no"
        print(f"{ticker} holding {present}")
        print(f"{ticker} unexecuted {open_counts[ticker]}")
    _print_unexecuted(open_orders)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("request failed")
        sys.exit(1)
