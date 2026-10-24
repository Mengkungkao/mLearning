"""The learning record on disk: local, owner-only, inspectable, deletable.

    <data>/learning/profile.json          the LearnerProfile
    <data>/learning/lessons/<id>.json     one summary per lesson (no conversation)
    <data>/learning/transcripts/<id>.json only with Keep transcripts on; kept 30 days

Files are written atomically (temporary file, then rename) with mode 0600
in 0700 folders. A profile that cannot be read is kept as
``profile.json.broken-<time>`` and a fresh profile starts: progress is
never silently overwritten. ``delete_all`` removes the whole learning
folder and nothing outside it.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from typing import Optional

from .profile import LearnerProfile

log = logging.getLogger("mlearning.storage")

TRANSCRIPT_DAYS = 30
MAX_LESSONS = 500


def write_json(path: str, data) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=1, ensure_ascii=False, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class LearningStorage:
    def __init__(self, root: str, wall=time.time):
        self.root = os.path.abspath(root)
        self.wall = wall
        self.problem = ""

    @property
    def profile_path(self) -> str:
        return os.path.join(self.root, "profile.json")

    def lessons_dir(self) -> str:
        return os.path.join(self.root, "lessons")

    def transcripts_dir(self) -> str:
        return os.path.join(self.root, "transcripts")

    # ------------------------------------------------------------ profile
    def load_profile(self) -> LearnerProfile:
        try:
            with open(self.profile_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            profile = LearnerProfile(created=time.strftime("%Y-%m-%d", time.localtime(self.wall())))
            return profile
        except (OSError, ValueError) as exc:
            kept = f"{self.profile_path}.broken-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                os.replace(self.profile_path, kept)
            except OSError:
                pass
            self.problem = f"learning record unreadable, kept as {os.path.basename(kept)}"
            log.warning("%s (%s)", self.problem, exc.__class__.__name__)
            return LearnerProfile(created=time.strftime("%Y-%m-%d", time.localtime(self.wall())))
        return LearnerProfile.from_dict(data)

    def save_profile(self, profile: LearnerProfile) -> None:
        write_json(self.profile_path, profile.to_dict())

    # ------------------------------------------------------------ lessons
    def save_lesson(self, lesson_id: str, record: dict) -> None:
        write_json(os.path.join(self.lessons_dir(), f"{lesson_id}.json"), record)
        self._prune(self.lessons_dir(), keep=MAX_LESSONS)

    def lessons(self, count: int = 20) -> list:
        directory = self.lessons_dir()
        try:
            names = sorted((n for n in os.listdir(directory) if n.endswith(".json")), reverse=True)
        except FileNotFoundError:
            return []
        records = []
        for name in names[:count]:
            try:
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    records.append(json.load(handle))
            except (OSError, ValueError):
                continue
        return records

    def save_transcript(self, lesson_id: str, lines: list) -> None:
        write_json(os.path.join(self.transcripts_dir(), f"{lesson_id}.json"),
                   {"lesson": lesson_id, "lines": [list(line) for line in lines]})
        self.prune_transcripts()

    def prune_transcripts(self, days: int = TRANSCRIPT_DAYS) -> int:
        directory = self.transcripts_dir()
        cutoff = self.wall() - days * 86400
        removed = 0
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            return 0
        for name in names:
            path = os.path.join(directory, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                continue
        return removed

    def delete_transcripts(self) -> None:
        self._remove_inside(self.transcripts_dir())

    def _prune(self, directory: str, keep: int) -> None:
        try:
            names = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
        except FileNotFoundError:
            return
        for name in names[:-keep] if len(names) > keep else []:
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass

    # ------------------------------------------------------------- delete
    def delete_all(self) -> None:
        """Remove the whole learning record (profile, lessons, transcripts)."""
        self._remove_inside(self.root, allow_root=True)

    def _remove_inside(self, path: str, allow_root: bool = False) -> None:
        path = os.path.abspath(path)
        root = self.root
        inside = path == root if allow_root else path.startswith(root + os.sep)
        if not inside or os.path.islink(path):
            raise ValueError(f"refusing to delete {path}")
        if os.path.isdir(path):
            shutil.rmtree(path)

    def size_summary(self) -> Optional[str]:
        total = 0
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(folder, name))
                except OSError:
                    pass
        return f"{total / 1024:.0f} KB" if total else None
