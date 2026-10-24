"""Where mLearning keeps its files.

Code lives in the version folder mFruit OS replaces on every update
(``WHISPLAY_OS_APP_DIR``, the working directory). Everything that must
survive an update -- settings, secrets, the learning profile -- lives in
the data folder (``WHISPLAY_OS_APP_DATA``), which mFruit OS snapshots before
each update. Run on its own, the data folder is ``~/.local/share/mlearning``.
"""

from __future__ import annotations

import os

APP_ID = "mlearning"
APP_NAME = "mLearning"
CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    base = os.environ.get("WHISPLAY_OS_APP_DATA")
    if not base:
        xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        base = os.path.join(xdg, APP_ID)
    return base


def ensure_data_dir() -> str:
    path = data_dir()
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def env_file() -> str:
    return os.path.join(data_dir(), ".env")


def settings_file() -> str:
    return os.path.join(data_dir(), "settings.json")


def learning_dir() -> str:
    return os.path.join(data_dir(), "learning")


def under_mfruit_os() -> bool:
    """True when mFruit OS (mfruit-run) started this process."""
    return bool(os.environ.get("WHISPLAY_OS_APP_DATA") or os.environ.get("MFRUIT_SESSION"))
