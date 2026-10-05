"""
Locate the directory where user-editable config files (sensors.json,
indices.json) live.

When running as a PyInstaller one-file build, sys.frozen is set and the exe
lives in a real folder next to which we keep the editable JSON. When running
from source we keep them in the project root. This keeps user-added indices and
sensors persistent across runs and next to the .exe the user actually ships.
"""

from __future__ import annotations

import os
import sys


def app_dir() -> str:
    """Folder containing the running .exe (frozen) or the project root (source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # zonalstats/paths.py -> project root is one level up from the package.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path(filename: str) -> str:
    return os.path.join(app_dir(), filename)
