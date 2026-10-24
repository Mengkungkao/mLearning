"""Spoken commands, recognised on the device from the transcript.

Only short utterances that are clearly a command are taken ("Repeat
that.", "Could you speak more slowly, please?"); anything longer, or a
command word inside a real sentence ("I want to stop smoking"), goes to
the teacher as conversation. Teaching requests ("What does that mean?",
"Correct my sentence") are deliberately not commands: the teacher answers
them.
"""

from __future__ import annotations

import re
from typing import Optional

REPEAT, SLOWER, FASTER, PAUSE, FINISH, INTERVIEW = (
    "repeat", "slower", "faster", "pause", "finish", "interview")

_POLITE = re.compile(r"^(?:(?:ok(?:ay)?|um+|uh+|so|sorry|excuse me|teacher|hey)[, ]+)*"
                     r"(?:(?:can|could|would|will) you |please |let's |let us |i want to |i'd like to )?")
_TAIL = re.compile(r"(?:,? ?(?:please|for me|thanks|thank you|now))*$")

_COMMANDS = (
    (REPEAT, ("repeat", "repeat that", "repeat it", "repeat that again", "say that again",
              "say it again", "pardon", "pardon me", "come again", "one more time",
              "repeat the sentence", "repeat the question")),
    (SLOWER, ("speak more slowly", "speak slowly", "speak slower", "more slowly", "slower",
              "slow down", "talk slower", "talk more slowly", "speak a bit slower",
              "speak a little slower", "say it slowly", "say it more slowly",
              "say that more slowly", "say that slowly", "repeat that slowly",
              "repeat it slowly", "repeat that more slowly")),
    (FASTER, ("speak faster", "faster", "speak more quickly", "speed up", "talk faster",
              "normal speed", "speak normally", "speak at normal speed")),
    (PAUSE, ("pause", "pause the lesson", "stop listening", "stop", "take a break",
             "pause please", "hold on a minute")),
    (FINISH, ("finish the lesson", "end the lesson", "finish", "stop the lesson",
              "finish for today", "that's all for today", "that is all for today",
              "let's stop here", "stop here", "end lesson", "finish lesson", "we can finish",
              "i'm done for today", "i am done for today")),
    (INTERVIEW, ("practise interviews", "practice interviews", "practise an interview",
                 "practice an interview", "interview practice", "start an interview",
                 "practise a job interview", "practice a job interview")),
)


def normalise(text: str) -> str:
    text = (text or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z' ]+", " ", text)
    return " ".join(text.split())


def detect(text: str) -> Optional[str]:
    words = normalise(text)
    if not words or len(words.split()) > 9:
        return None
    core = _TAIL.sub("", _POLITE.sub("", words)).strip()
    for name, phrases in _COMMANDS:
        if core in phrases:
            return name
    return None
