"""Secrets: API keys from the environment or the data folder's ``.env``.

Only a tiny subset of the dotenv format is read (``NAME=value`` lines,
optional quotes, ``#`` comments); nothing is executed. The environment
wins over the file. Values are never logged: :func:`describe` reports only
whether a key is set.
"""

from __future__ import annotations

import logging
import os
import stat

log = logging.getLogger("mlearning.env")

KNOWN_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY")


def parse(text: str) -> dict:
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if name:
            values[name] = value
    return values


def load(path: str) -> dict:
    """The file's values (empty if it is missing or unreadable)."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc)
        return {}
    try:
        mode = os.stat(path).st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            log.warning("%s is readable by other users; run: chmod 600 %s", path, path)
    except OSError:
        pass
    return parse(text)


class Secrets:
    """Read-only view of the keys mLearning uses."""

    def __init__(self, path: str, environ=None):
        self.path = path
        self._file = load(path)
        self._environ = os.environ if environ is None else environ

    def get(self, name: str) -> str:
        value = self._environ.get(name)
        if value:
            return value.strip()
        return (self._file.get(name) or "").strip()

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    def describe(self) -> dict:
        """{name: "set" | "missing"} -- never the value."""
        return {name: ("set" if self.has(name) else "missing") for name in KNOWN_KEYS}


def create_from_template(template: str, path: str) -> bool:
    """Copy the template to ``path`` with owner-only permissions, never over a file."""
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(template, encoding="utf-8") as handle:
        text = handle.read()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(text)
    return True
