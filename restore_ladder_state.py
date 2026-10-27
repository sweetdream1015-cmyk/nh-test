"""Restore ladder state from the newest workflow artifact when cache missed.

Prints only OK cache, OK restore, or OK none. Does not print the file.
"""

import io
import json
import os
import zipfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_PATH = os.environ.get("LADDER_STATE_PATH", ".ladder-state/state.json")
_NAME = "ladder-state"


def _get(url, token):
    request = Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "ladder-state",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read()


def main():
    if os.path.isfile(_PATH):
        print("OK cache")
        return 0
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    if not token or repo.count("/") != 1:
        print("OK none")
        return 0
    try:
        payload = json.loads(
            _get(
                api + "/repos/" + repo + "/actions/artifacts?name=" + _NAME + "&per_page=20",
                token,
            ).decode("utf-8")
        )
    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        print("OK none")
        return 0
    chosen = None
    for artifact in payload.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("name") != _NAME or artifact.get("expired"):
            continue
        chosen = artifact
        break
    download = chosen.get("archive_download_url") if chosen else ""
    if not isinstance(download, str) or not download:
        print("OK none")
        return 0
    try:
        blob = _get(download, token)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith("state.json") and not name.endswith("/")
            ]
            if not names:
                print("OK none")
                return 0
            raw = archive.read(names[0])
        json.loads(raw.decode("utf-8"))
    except (HTTPError, URLError, ValueError, TimeoutError, OSError, zipfile.BadZipFile):
        print("OK none")
        return 0
    directory = os.path.dirname(_PATH)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    fd = os.open(_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    os.chmod(_PATH, 0o600)
    print("OK restore")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("OK none")
        raise SystemExit(0)
