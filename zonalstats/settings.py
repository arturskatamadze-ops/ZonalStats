"""
Remember the last session so the app opens ready to run.

A field trial is processed the same way flight after flight: same camera, same
statistics, same indices, same output folder. Making the user re-enter all of
that every launch is the single biggest source of pointless input in the app,
so everything the main window collects is written to ``settings.json`` (next to
sensors.json / indices.json) on exit and restored on start.

Everything here is fail-soft: a missing, unreadable or corrupt settings file
must never stop the program starting, so every entry point swallows its errors
and falls back to defaults. Nothing in here is required for correctness — it is
purely a convenience layer over the real config.
"""

from __future__ import annotations

import json
import os

from .paths import config_path

SETTINGS_FILE = "settings.json"
SCHEMA_VERSION = 1
MAX_RECENT = 8


def settings_path() -> str:
    return config_path(SETTINGS_FILE)


def load_settings() -> dict:
    """Return the saved settings, or an empty dict if there are none/unusable.

    Callers treat every key as optional, so an empty dict simply means "use the
    built-in defaults" — the same path a first-ever launch takes.
    """
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # A future version may reorganise keys; refusing to read a newer file is
    # safer than half-applying it.
    if data.get("version", SCHEMA_VERSION) > SCHEMA_VERSION:
        return {}
    return data


def save_settings(data: dict) -> bool:
    """Write *data* as the new settings. Returns True on success.

    Failure is reported by the return value rather than an exception: the caller
    is usually a window-close handler, where a raised error would be both
    invisible and disruptive.
    """
    payload = dict(data)
    payload["version"] = SCHEMA_VERSION
    try:
        with open(settings_path(), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        return True
    except (OSError, TypeError, ValueError):
        return False


def remember_recent(recents, path: str) -> list:
    """Return *recents* with *path* moved to the front, de-duplicated and capped.

    Comparison is case-insensitive on the normalised path so the same file
    picked via two different spellings (``D:/x`` vs ``D:\\x``) is one entry.
    """
    if not path:
        return list(recents or [])[:MAX_RECENT]
    norm = os.path.normcase(os.path.normpath(path))
    out = [path]
    for p in recents or []:
        try:
            if os.path.normcase(os.path.normpath(p)) != norm:
                out.append(p)
        except (OSError, ValueError, TypeError):
            continue
    return out[:MAX_RECENT]


def existing_only(paths) -> list:
    """Drop entries that no longer exist on disk.

    Recent-file menus that list deleted files are worse than useless, and a
    restored path pointing at a moved drive would show the user a red error on
    a window they have not touched yet.
    """
    out = []
    for p in paths or []:
        try:
            if p and os.path.exists(p):
                out.append(p)
        except (OSError, ValueError, TypeError):
            continue
    return out
