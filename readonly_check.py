"""Read-only NH PLUG check: OAuth token, then one /n2/acctinfo call.

Prints status lines only. Never prints bodies, tokens, account numbers,
names, request parameters, or credentials. No orders, writes, or files.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LIVE = "https://api.nhplug.com:8443"
TOKEN_URL = LIVE + "/oauth2/token"
ACCTINFO_URL = LIVE + "/n2/acctinfo"

_ERROR_KEYS = (
    "error",
    "error_code",
    "code",
    "msg_cd",
    "message",
    "msg",
    "error_description",
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _read_json(raw):
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _post(url, body, headers):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=20) as resp:
            return resp.status, _read_json(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        return exc.code, _read_json(raw)
    except Exception:
        return None, {}


def _as_short(value, depth=0):
    if depth > 2:
        return None
    if isinstance(value, str):
        text = " ".join(value.split())
        if not text:
            return None
        return text[:80]
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        for key in _ERROR_KEYS:
            found = _as_short(value.get(key), depth + 1)
            if found:
                return found
    return None


def _safe_error(payload, secrets):
    text = _as_short(payload)
    if not text:
        return None
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 8 and secret in text:
            return None
    return text


def _access_token(payload):
    if not isinstance(payload, dict):
        return ""
    token = payload.get("access_token")
    if isinstance(token, str) and token.strip():
        return token
    return ""


def _account_count(payload):
    rows = payload.get("Output_0") if isinstance(payload, dict) else None
    if isinstance(rows, list):
        return len(rows)
    return 0


def _token_request(key, secret):
    query = urllib.parse.urlencode(
        (
            ("appkey", key),
            ("appsecretkey", secret),
            ("grant_type", "client_credentials"),
            ("scope", "oob"),
        )
    )
    return _post(
        TOKEN_URL + "?" + query,
        b"",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )


def _acctinfo_request(token):
    body = json.dumps({"Input_0": {}}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + token,
    }
    return _post(ACCTINFO_URL, body, headers)


def main():
    key = os.environ.get("NHPLUG_APP_KEY", "")
    secret = os.environ.get("NHPLUG_APP_SECRET", "")
    if not key.strip() or not secret.strip():
        print("secrets missing")
        return 1

    status, payload = _token_request(key, secret)
    token = _access_token(payload) if status == 200 else ""
    if status is None:
        print("token returned: no")
        print("request failed")
        return 1

    print(f"token HTTP {status}")
    print("token returned: yes" if token else "token returned: no")
    if status != 200 or not token:
        err = _safe_error(payload, (key, secret))
        if err:
            print(err)
        return 1

    time.sleep(0.25)
    acct_status, acct_payload = _acctinfo_request(token)
    if acct_status is None:
        print("request failed")
        return 1

    print(f"acctinfo HTTP {acct_status}")
    if acct_status != 200:
        err = _safe_error(acct_payload, (key, secret, token))
        if err:
            print(err)
        return 1

    print(_account_count(acct_payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
