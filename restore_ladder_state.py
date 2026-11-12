"""Restore ladder state from the newest workflow artifact when it is newer.

Issue-comment runs cannot write the Actions cache, so a kick saves state
only as an artifact. The next run may restore an older cache. This script
keeps whichever copy has the later saved_at.

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


def _saved_at(data):
    if not isinstance(data, dict):
        return 0
    raw = data.get("saved_at")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def prefer_newer(cache, artifact):
    """Which copy to keep. The artifact wins only when it is strictly newer.

    A missing saved_at is older than any positive one. Two copies with no
    timestamp keep the cache, which is what a cache hit already restored.
    """
    if artifact is None:
        return "cache" if cache is not None else "none"
    if cache is None:
        return "artifact"
    if _saved_at(artifact) > _saved_at(cache):
        return "artifact"
    return "cache"


def _read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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


def _download(token, repo, api):
    try:
        payload = json.loads(
            _get(
                api + "/repos/" + repo + "/actions/artifacts?name=" + _NAME + "&per_page=20",
                token,
            ).decode("utf-8")
        )
    except (HTTPError, URLError, ValueError, TimeoutError, OSError):
        return None
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
        return None
    try:
        blob = _get(download, token)
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
    except (HTTPError, URLError, ValueError, TimeoutError, OSError, zipfile.BadZipFile):
        return None
    if not isinstance(data, dict):
        return None
    return data, raw


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
    downloaded = None
    if token and repo.count("/") == 1:
        downloaded = _download(token, repo, api)
    artifact = downloaded[0] if downloaded else None
    choice = prefer_newer(cache, artifact)
    if choice == "artifact" and downloaded is not None:
        _write(downloaded[1])
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
