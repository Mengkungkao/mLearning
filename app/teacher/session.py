"""One lesson: the conversation so far and what the learner practised.

The conversation sent to the teacher is append-only and alternates
learner / teacher. A learner turn is committed together with the teacher's
answer, after the answer arrived: if the request fails and the learner
taps Retry, the same turn is sent again instead of appearing twice.
Older turns beyond ``MAX_MESSAGES`` are dropped from what is sent (the
opening stays), which bounds request size on long lessons.
"""

from __future__ import annotations

import time
from typing import Optional

from .modes import Activity
from .schema import TeacherTurn

MAX_MESSAGES = 24


class Session:
    def __init__(self, activity: Activity, system: str, keep_transcript: bool = False,
                 clock=time.monotonic, wall=time.time):
        self.activity = activity
        self.system = system
        self.keep_transcript = keep_transcript
        self.clock = clock
        self.started = clock()
        self.started_wall = wall()
        self.id = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started_wall))
        self.messages: list = []
        self.entries: list = []          # per learner turn: text, corrections, phrases
        self.transcript: list = []       # (who, text) when keep_transcript
        self.last_turn: Optional[TeacherTurn] = None
        self.last_learner = ""
        self.practice_target = ""
        self.practice_attempts = 0
        self.time_up_noted = False
        self.learner_seconds = 0.0
        self.interview_questions = 0
        self.listening_results: list = []   # True/False per checked attempt

    @property
    def minutes(self) -> float:
        return (self.clock() - self.started) / 60.0

    def request_messages(self, pending_user: str) -> list:
        """What to send: the kept history plus the new learner turn."""
        history = self.messages
        if len(history) > MAX_MESSAGES:
            history = history[:2] + history[-(MAX_MESSAGES - 2):]
            if history[2]["role"] != "user":
                history = history[:2] + history[3:]
        return history + [{"role": "user", "content": pending_user}]

    def commit(self, user_content: str, turn: TeacherTurn, learner_text: str = "",
               typed: bool = False) -> None:
        """Record a completed exchange."""
        self.messages.append({"role": "user", "content": user_content})
        self.messages.append({"role": "assistant", "content": turn.history_text()})
        self.last_turn = turn
        if learner_text:
            self.last_learner = learner_text
            self.entries.append({
                "learner": learner_text,
                "corrections": [{"you_said": c.you_said, "better": c.better,
                                 "category": c.category, "priority": c.priority}
                                for c in turn.corrections],
                "phrases": [n.phrase for n in turn.vocabulary_notes],
                "level": turn.level_estimate,
                "typed": typed,
            })
            if self.keep_transcript:
                self.transcript.append(("learner", learner_text))
        if self.keep_transcript:
            self.transcript.append(("teacher", turn.reply_text))
        if turn.teaching_mode in ("interview_question", "interview_feedback"):
            self.interview_questions += 1
        target = turn.practice_target
        if target and target == self.practice_target:
            self.practice_attempts += 1
        else:
            self.practice_attempts = 0
        self.practice_target = target

    @property
    def turns(self) -> int:
        return len(self.entries)
