"""The learner's profile: only what helps the teacher teach.

Kept: lesson minutes per day, recurring mistake categories with at most
three short examples each, phrases learned, per-turn level estimates,
interview and listening counts, and the last lesson's summary. Not kept:
recordings (never), and full conversations unless the learner turns on
Keep transcripts.

The working level is not taken from one turn or one lesson: it needs at
least ``MIN_ESTIMATES`` estimates from at least two lessons, and is their
median.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional

LEVEL_ORDER = ("A1", "A2", "B1", "B2", "C1", "C2")
MIN_ESTIMATES = 12
MAX_ESTIMATES = 60
MAX_PHRASES = 200
MAX_EXAMPLES = 3

CATEGORY_NAMES = {
    "meaning": "meaning", "grammar": "grammar", "tense": "verb tenses",
    "agreement": "subject-verb agreement", "article": "articles (a, the)",
    "preposition": "prepositions", "word_choice": "word choice", "vocabulary": "vocabulary",
    "word_order": "word order", "naturalness": "natural phrasing", "register": "tone and register",
    "pronunciation": "pronunciation", "other": "other",
}


def today(wall=time.time) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(wall()))


@dataclass
class LearnerProfile:
    version: int = 1
    created: str = ""
    sessions: int = 0
    minutes_total: float = 0.0
    days: dict = field(default_factory=dict)              # "YYYY-MM-DD" -> minutes
    level_estimates: list = field(default_factory=list)   # [session id, level]
    mistakes: dict = field(default_factory=dict)          # category -> {count, examples, last}
    phrases: list = field(default_factory=list)           # {phrase, meaning, example, added, practised}
    interview: dict = field(default_factory=lambda: {"sessions": 0, "questions": 0, "last": ""})
    listening: dict = field(default_factory=dict)         # activity id -> [1/0, ...]
    last_summary: dict = field(default_factory=dict)

    # ------------------------------------------------------------- record
    def record_turn(self, session_id: str, turn) -> None:
        day = today()
        for correction in turn.corrections:
            entry = self.mistakes.setdefault(correction.category, {"count": 0, "examples": [], "last": ""})
            entry["count"] += 1
            entry["last"] = day
            if correction.you_said and correction.better:
                example = [correction.you_said[:120], correction.better[:120]]
                if example not in entry["examples"]:
                    entry["examples"] = (entry["examples"] + [example])[-MAX_EXAMPLES:]
        for note in turn.vocabulary_notes:
            self.add_phrase(note.phrase, note.meaning, note.example)
        if turn.level_estimate in LEVEL_ORDER:
            self.level_estimates.append([session_id, turn.level_estimate])
            del self.level_estimates[:-MAX_ESTIMATES]

    def record_session(self, minutes: float, activity_mode: str, activity_id: str,
                       summary=None, interview_questions: int = 0,
                       listening_results: Optional[list] = None) -> None:
        day = today()
        self.sessions += 1
        self.minutes_total = round(self.minutes_total + minutes, 2)
        self.days[day] = round(self.days.get(day, 0.0) + minutes, 2)
        if len(self.days) > 400:
            for old in sorted(self.days)[:-400]:
                del self.days[old]
        if activity_mode == "interview":
            self.interview["sessions"] += 1
            self.interview["questions"] += interview_questions
            self.interview["last"] = day
        if listening_results:
            history = self.listening.setdefault(activity_id, [])
            history.extend(1 if ok else 0 for ok in listening_results)
            del history[:-20]
        if summary is not None:
            self.last_summary = {"date": day, "main_improvement": summary.main_improvement,
                                 "next_exercise": summary.next_exercise,
                                 "new_phrase": summary.new_phrase}
            if summary.new_phrase:
                self.add_phrase(summary.new_phrase, summary.new_phrase_meaning, "")

    def add_phrase(self, phrase: str, meaning: str = "", example: str = "") -> bool:
        phrase = " ".join((phrase or "").split())[:80]
        if not phrase:
            return False
        for entry in self.phrases:
            if entry["phrase"].lower() == phrase.lower():
                if meaning and not entry.get("meaning"):
                    entry["meaning"] = meaning
                return False
        self.phrases.append({"phrase": phrase, "meaning": meaning[:200], "example": example[:200],
                             "added": today(), "practised": 0})
        del self.phrases[:-MAX_PHRASES]
        return True

    def remove_phrase(self, phrase: str) -> bool:
        before = len(self.phrases)
        self.phrases = [p for p in self.phrases if p["phrase"] != phrase]
        return len(self.phrases) != before

    def practised(self, phrase: str) -> None:
        for entry in self.phrases:
            if entry["phrase"] == phrase:
                entry["practised"] = entry.get("practised", 0) + 1

    # -------------------------------------------------------------- read
    def working_level(self) -> str:
        """Median CEFR estimate, or "" while there is too little evidence."""
        if len(self.level_estimates) < MIN_ESTIMATES:
            return ""
        if len({session for session, _ in self.level_estimates}) < 2:
            return ""
        ranks = sorted(LEVEL_ORDER.index(level) for _, level in self.level_estimates
                       if level in LEVEL_ORDER)
        return LEVEL_ORDER[ranks[len(ranks) // 2]]

    def top_mistakes(self, count: int = 3) -> list:
        """[(category, entry)] most frequent first."""
        ranked = sorted(self.mistakes.items(), key=lambda item: (-item[1]["count"], item[0]))
        return [(category, entry) for category, entry in ranked if category != "other"][:count]

    def minutes_on(self, day: str) -> float:
        return float(self.days.get(day, 0.0))

    def streak(self, wall=time.time) -> int:
        """Consecutive days with practice, ending today or yesterday."""
        count = 0
        moment = wall()
        started = False
        for offset in range(0, 400):
            day = time.strftime("%Y-%m-%d", time.localtime(moment - offset * 86400))
            if self.days.get(day, 0) > 0:
                count += 1
                started = True
            elif offset == 0 and not started:
                continue
            else:
                break
        return count

    def listening_success(self, activity_id: str) -> str:
        history = self.listening.get(activity_id, [])[-6:]
        if not history:
            return ""
        return f"{sum(history)} of the last {len(history)} attempts succeeded"

    def notes_for_teacher(self) -> str:
        notes = []
        level = self.working_level()
        if level:
            notes.append(f"Over several lessons their English has been around CEFR {level}.")
        mistakes = self.top_mistakes(3)
        if mistakes:
            described = []
            for category, entry in mistakes:
                name = CATEGORY_NAMES.get(category, category)
                example = entry["examples"][-1] if entry["examples"] else None
                described.append(f"{name} (e.g. '{example[0]}' -> '{example[1]}')" if example else name)
            notes.append("Recurring mistakes to watch for: " + "; ".join(described) + ".")
        recent = [p["phrase"] for p in self.phrases[-5:]]
        if recent:
            notes.append("Phrases learned recently (reuse them when natural): " + ", ".join(recent) + ".")
        if self.last_summary.get("next_exercise"):
            notes.append(f"Suggested at the end of the last lesson: {self.last_summary['next_exercise']}")
        return " ".join(notes)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LearnerProfile":
        profile = cls()
        if not isinstance(data, dict):
            return profile
        for key, default in asdict(profile).items():
            value = data.get(key, default)
            if isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if isinstance(value, type(default)):
                setattr(profile, key, value)
        return profile
