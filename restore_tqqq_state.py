"""Restore the newest TQQQ state from cache or a tqqq-state artifact.

Uses the ladder restore machinery with a different path, artifact name,
and workflow list. The ladder file and the ladder-state artifact are
not read or written. Globals are put back before this returns.
"""

import os

import restore_ladder_state as restore

_PATH = os.environ.get("TQQQ_STATE_PATH", ".tqqq-state/state.json")
_NAME = "tqqq-state"
_WORKFLOWS = ("tqqq-exception.yml",)


def _blocked(path):
    text = path.replace("\\", "/")
    return "ladder-state" in text


def main():
    path = os.environ.get("TQQQ_STATE_PATH", _PATH)
    if _blocked(path):
        print("OK none")
        return 0
    old = (restore._PATH, restore._NAME, restore._WORKFLOWS)
    restore._PATH = path
    restore._NAME = _NAME
    restore._WORKFLOWS = _WORKFLOWS
    try:
        return restore.main()
    finally:
        restore._PATH, restore._NAME, restore._WORKFLOWS = old


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        print("OK none")
        raise SystemExit(0)
