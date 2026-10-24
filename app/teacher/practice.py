"""Comparing a repeat attempt with the sentence the learner was asked to say.

This compares *recognised words* with the target, nothing more: a missing
word may be a pronunciation problem, a recognition error, an accent the
recogniser handles badly or a quiet microphone, and the result says only
what was recognised. Nothing here scores pronunciation.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

_CONTRACTIONS = {
    "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "he's": "he is", "she's": "she is", "it's": "it is", "that's": "that is",
    "there's": "there is", "what's": "what is", "let's": "let us", "i've": "i have",
    "you've": "you have", "we've": "we have", "they've": "they have", "i'll": "i will",
    "you'll": "you will", "we'll": "we will", "they'll": "they will", "i'd": "i would",
    "you'd": "you would", "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "can't": "cannot", "won't": "will not", "isn't": "is not", "aren't": "are not",
    "wasn't": "was not", "weren't": "were not", "couldn't": "could not",
    "wouldn't": "would not", "shouldn't": "should not", "haven't": "have not",
    "hasn't": "has not", "can not": "cannot",
}
_NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
            "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_WORD = re.compile(r"[a-z0-9']+")


def words(text: str) -> list:
    text = (text or "").lower().replace("’", "'")
    for short, long in _CONTRACTIONS.items():
        text = re.sub(rf"\b{re.escape(short)}\b", long, text)
    found = []
    for word in _WORD.findall(text):
        word = word.strip("'")
        if word:
            found.append(_NUMBERS.get(word, word))
    return found


@dataclass
class PracticeResult:
    target: str
    heard: str
    matched: int
    total: int
    missing: list = field(default_factory=list)
    substituted: list = field(default_factory=list)   # (expected, heard)
    extra: list = field(default_factory=list)

    @property
    def score(self) -> float:
        return self.matched / self.total if self.total else 0.0

    @property
    def all_words(self) -> bool:
        return self.total > 0 and self.matched == self.total and not self.extra

    def describe(self) -> str:
        """For the teacher: what was recognised, without guessing why."""
        if not self.heard.strip():
            return "Nothing was recognised."
        parts = [f"Recognised {self.matched} of {self.total} words of the target."]
        if self.missing:
            parts.append("Not recognised: " + ", ".join(self.missing[:6]) + ".")
        if self.substituted:
            parts.append("Heard instead: " + ", ".join(f"'{h}' for '{e}'" for e, h in self.substituted[:4]) + ".")
        if self.extra:
            parts.append("Extra words: " + ", ".join(self.extra[:6]) + ".")
        parts.append("Recognition errors are possible; do not call these pronunciation mistakes "
                     "unless the pattern is clear.")
        return " ".join(parts)

    def short(self) -> str:
        """For the screen."""
        if not self.heard.strip():
            return "I didn't catch that"
        if self.all_words:
            return "Every word recognised"
        if self.missing:
            return f"{self.matched}/{self.total} words · missed: {', '.join(self.missing[:3])}"
        return f"{self.matched}/{self.total} words recognised"


def compare(target: str, heard: str) -> PracticeResult:
    expected, got = words(target), words(heard)
    matcher = difflib.SequenceMatcher(a=expected, b=got, autojunk=False)
    result = PracticeResult(target, heard, 0, len(expected))
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            result.matched += a1 - a0
        elif op == "delete":
            result.missing += expected[a0:a1]
        elif op == "insert":
            result.extra += got[b0:b1]
        else:  # replace
            pairs = list(zip(expected[a0:a1], got[b0:b1]))
            result.substituted += pairs
            if a1 - a0 > b1 - b0:
                result.missing += expected[a0 + (b1 - b0):a1]
            elif b1 - b0 > a1 - a0:
                result.extra += got[b0 + (a1 - a0):b1]
    return result
