"""Restore the newest ladder state from cache or a workflow artifact.

An issue_comment run can place orders and upload a ladder-state artifact,
then fail to save the Actions cache: GitHub gives that event a read-only
cache token. The next run restores the old cache. Keeping whichever file
was restored first repeats the orders the kick already sent.

This script loads ladder-state artifacts from recent ladder-intraday,
ladder-reset, ladder-cancel-dup, ladder-adjust, and seed-state runs of
every event (schedule, issue_comment, workflow_dispatch, push). It keeps
the copy whose saved_at and seq, stored inside the file, are newer. A
successful cache restore does not win by itself.

The artifact API redirects the zip to blob storage. The GitHub bearer
token must not follow that redirect: blob storage rejects it, the
download fails, and the stale cache would be kept.

Prints only OK cache, OK restore, or OK none. Does not print the file.
"""

import io
import json
import os
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

_PATH = os.environ.get("LADDER_STATE_PATH", ".ladder-state/state.json")
_NAME = "ladder-state"
_WORKFLOWS = (
    "ladder-intraday.yml",
    "ladder-reset.yml",
    "ladder-cancel-dup.yml",
    "ladder-adjust.yml",
    "seed-state.yml",
)
# No event= filter. issue_comment runs are included with schedule and dispatch.
_RUN_STATUSES = ("success", "failure")
_RUN_PAGE = 10
_RUN_CAP = 12
_DOWNLOAD_CAP = 12


def _saved_at(data):
    if not isinstance(data, dict):
        return 0
    raw = data.get("saved_at")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def _seq(data):
    if not isinstance(data, dict):
        return 0
    raw = data.get("seq")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def _order_key(data):
    """Newer saves sort later. A missing stamp is zero, so an old file loses."""
    return (_saved_at(data), _seq(data))


def choose_state(cache, records):
    """Pick the newest copy by the stamp inside the state.

    `records` is a list of {"state": dict, "raw": bytes}. List order is
    ignored. Equal stamps keep the cache, which is already on disk.
    Returns ("cache"|"artifact"|"none", raw or None).
    """
    best_key = None
    best_raw = None
    if isinstance(cache, dict):
        best_key = _order_key(cache)
    for record in records or []:
        if not isinstance(record, dict):
            continue
        state = record.get("state")
        raw = record.get("raw")
        if not isinstance(state, dict) or not isinstance(raw, (bytes, bytearray)):
            continue
        key = _order_key(state)
        if best_key is None or key > best_key:
            best_key = key
            best_raw = bytes(raw)
    if best_key is None:
        return "none", None
    if best_raw is not None:
        return "artifact", best_raw
    return "cache", None


def prefer_newer(cache, artifact):
    """Which single copy to keep. The artifact wins only when it is strictly newer.

    A missing saved_at is older than any positive one. Two copies with no
    timestamp keep the cache, which is what a cache hit already restored.
    """
    records = []
    if isinstance(artifact, dict):
        records.append({"state": artifact, "raw": b"1"})
    choice, _raw = choose_state(cache if isinstance(cache, dict) else None, records)
    return choice


def workflow_run_urls(api, repo):
    """Run lists for every state workflow. Status is not an event filter."""
    base = api.rstrip("/") + "/repos/" + repo + "/actions/workflows/"
    urls = []
    for name in _WORKFLOWS:
        for status in _RUN_STATUSES:
            urls.append(
                base
                + quote(name)
                + "/runs?per_page="
                + str(_RUN_PAGE)
                + "&status="
                + status
            )
    return urls


def repo_artifacts_url(api, repo):
    return (
        api.rstrip("/")
        + "/repos/"
        + repo
        + "/actions/artifacts?name="
        + quote(_NAME)
        + "&per_page=30"
    )


def newest_run_ids(rows, limit):
    """Unique run ids, newest created_at first. Input order is ignored."""
    ordered = sorted(rows or [], key=lambda item: item[0], reverse=True)
    seen = set()
    chosen = []
    for _created, run_id in ordered:
        if run_id in seen:
            continue
        seen.add(run_id)
        chosen.append(run_id)
        if len(chosen) >= limit:
            break
    return chosen


def _run_rows(payload):
    rows = []
    if not isinstance(payload, dict):
        return rows
    for run in payload.get("workflow_runs") or []:
        if not isinstance(run, dict):
            continue
        run_id = run.get("id")
        created = run.get("created_at")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
            continue
        if not isinstance(created, str):
            created = ""
        rows.append((created, run_id))
    return rows


def _artifact_row(artifact):
    if not isinstance(artifact, dict):
        return None
    if artifact.get("name") != _NAME or artifact.get("expired"):
        return None
    art_id = artifact.get("id")
    download = artifact.get("archive_download_url")
    created = artifact.get("created_at")
    run = artifact.get("workflow_run")
    run_id = run.get("id") if isinstance(run, dict) else None
    if isinstance(art_id, bool) or not isinstance(art_id, int) or art_id < 1:
        return None
    if not isinstance(download, str) or not download:
        return None
    if not isinstance(created, str):
        created = ""
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1:
        run_id = None
    return {
        "id": art_id,
        "created_at": created,
        "download": download,
        "run_id": run_id,
    }


def runs_missing_artifact(run_ids, rows):
    """Runs whose ladder-state artifact was not in the repo list.

    The repo list can omit an issue_comment upload or return an older
    page first. Those runs are fetched on their own.
    """
    covered = set()
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get("run_id"), int):
            covered.add(row["run_id"])
    missing = []
    for run_id in run_ids or []:
        if run_id not in covered:
            missing.append(run_id)
    return missing


def _dedupe_newest(rows, limit):
    by_id = {}
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get("id"), int):
            by_id[row["id"]] = row
    ordered = sorted(
        by_id.values(),
        key=lambda row: row.get("created_at") or "",
        reverse=True,
    )
    return ordered[:limit]


class _DropAuthRedirect(HTTPRedirectHandler):
    """Follow the artifact redirect without the GitHub bearer token.

    Blob storage answers 401 InvalidAuthenticationInfo when that header
    is forwarded, and the caller would then keep the stale cache.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl
        )
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def _opener():
    return build_opener(_DropAuthRedirect())


def _get(url, token):
    request = Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "ladder-state",
        },
    )
    with _opener().open(request, timeout=30) as response:
        return response.read()


def _read_json(fetch, url):
    try:
        raw = fetch(url)
    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        return None
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


def _state_from_zip(blob):
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith("state.json") and not name.endswith("/")
            ]
            if not names:
                return None
            raw = archive.read(names[0])
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, OSError, zipfile.BadZipFile, UnicodeError):
        return None
    if not isinstance(data, dict) or not isinstance(raw, (bytes, bytearray)):
        return None
    return data, bytes(raw)


def _download_state(fetch, url):
    try:
        blob = fetch(url)
    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        return None
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        return None
    return _state_from_zip(blob)


def load_records(fetch, api, repo):
    """Download candidate state files. `fetch(url)` returns bytes or None."""
    rows = []
    run_rows = []
    listed = _read_json(fetch, repo_artifacts_url(api, repo))
    if isinstance(listed, dict):
        for artifact in listed.get("artifacts") or []:
            row = _artifact_row(artifact)
            if row is not None:
                rows.append(row)
    for url in workflow_run_urls(api, repo):
        payload = _read_json(fetch, url)
        run_rows.extend(_run_rows(payload))
    run_ids = newest_run_ids(run_rows, _RUN_CAP)
    for run_id in runs_missing_artifact(run_ids, rows):
        payload = _read_json(
            fetch,
            api.rstrip("/")
            + "/repos/"
            + repo
            + "/actions/runs/"
            + str(run_id)
            + "/artifacts?per_page=20",
        )
        if not isinstance(payload, dict):
            continue
        for artifact in payload.get("artifacts") or []:
            row = _artifact_row(artifact)
            if row is not None:
                rows.append(row)
    records = []
    for row in _dedupe_newest(rows, _DOWNLOAD_CAP):
        parsed = _download_state(fetch, row["download"])
        if parsed is None:
            continue
        state, raw = parsed
        records.append({"state": state, "raw": raw})
    return records


def _read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(raw):
    directory = os.path.dirname(_PATH)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    fd = os.open(_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    os.chmod(_PATH, 0o600)


def main():
    cache = _read_state(_PATH) if os.path.isfile(_PATH) else None
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    records = []
    if token and repo.count("/") == 1:
        records = load_records(lambda url: _get(url, token), api, repo)
    choice, raw = choose_state(cache, records)
    if choice == "artifact" and raw:
        _write(raw)
        print("OK restore")
        return 0
    if os.path.isfile(_PATH):
        print("OK cache")
        return 0
    print("OK none")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("OK none")
        raise SystemExit(0)
