"""Read-only account list. OAuth, then one /n2/acctinfo call.

Prints a masked account number and acct_type for each row, and whether
an account whose digits contain 20112 (201-12) is present. No orders,
no inquiry, no state file.
"""

import os
import sys

import gb_readonly_check as gb

_SUFFIX = "20112"


def _safe_type(value):
    if isinstance(value, bool):
        return "?"
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return "?"
    text = value.strip()
    if text.isalnum() and 1 <= len(text) <= 4:
        return text
    return "?"


def report_lines(rows):
    """Masked lines plus `201-12 present yes|no`. Never the full number."""
    lines = []
    present = False
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("acct_no")
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int):
            raw = str(raw)
        if not isinstance(raw, str) or not raw.strip():
            continue
        account = raw.strip()
        if account not in gb._secrets:
            gb._secrets.append(account)
        if _SUFFIX in gb._digits(account):
            present = True
        lines.append(
            "account %s type %s" % (gb._mask_account(account), _safe_type(row.get("acct_type")))
        )
    lines.append("201-12 present %s" % ("yes" if present else "no"))
    return lines


def account_rows(payload):
    """Output_0, or None when the block is missing or not a list."""
    rows = payload.get("Output_0") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    return rows


def main():
    key = os.environ.get("NHPLUG_APP_KEY", "")
    secret = os.environ.get("NHPLUG_APP_SECRET", "")
    if not isinstance(key, str) or not isinstance(secret, str):
        print("secrets missing")
        return 1
    if not key.strip() or not secret.strip():
        print("secrets missing")
        return 1
    gb._key = key
    gb._secret = secret
    gb._secrets.extend((key, secret))
    token = gb._issue_token(key, secret)
    if not token:
        return 1
    gb._token = token
    status, _headers, payload = gb._post_bearer(gb.ACCTINFO_URL, gb._json_body({}), {})
    if status != 200 or not isinstance(payload, dict):
        shown = "none" if status is None else status
        print("acctinfo HTTP %s" % (shown,))
        return 1
    print("acctinfo HTTP %s" % (status,))
    rows = account_rows(payload)
    if rows is None:
        print("ERROR account not found")
        return 1
    for line in report_lines(rows):
        print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("ERROR unexpected")
        sys.exit(1)
