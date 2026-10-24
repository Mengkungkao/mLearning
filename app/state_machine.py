"""The tutor's lifecycle as an explicit state machine.

Only the engine thread changes state, and only along the transitions in
``TRANSITIONS``; anything else is refused and logged, so a late callback
can never move the tutor somewhere it should not be (two voices at once,
an answer to a question nobody asked). Each state also has a deadline
(``DEADLINES``): the engine's watchdog turns a state that outlives it into
``ERROR`` with Retry, so a hung request or a silent device can never leave
the tutor stuck.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

log = logging.getLogger("mlearning.state")

INITIALISING = "INITIALISING"
IDLE = "IDLE"
LISTENING = "LISTENING"
PROCESSING_SPEECH = "PROCESSING_SPEECH"
GENERATING_RESPONSE = "GENERATING_RESPONSE"
SPEAKING = "SPEAKING"
PRACTICE_WAITING = "PRACTICE_WAITING"
PAUSED = "PAUSED"
ERROR = "ERROR"
SHUTTING_DOWN = "SHUTTING_DOWN"

STATES = (INITIALISING, IDLE, LISTENING, PROCESSING_SPEECH, GENERATING_RESPONSE, SPEAKING,
          PRACTICE_WAITING, PAUSED, ERROR, SHUTTING_DOWN)

TRANSITIONS = {
    INITIALISING: {IDLE, ERROR, SHUTTING_DOWN},
    # IDLE: the home screens, or a lesson waiting for "hold to talk".
    IDLE: {LISTENING, GENERATING_RESPONSE, SPEAKING, PRACTICE_WAITING, PAUSED, ERROR,
           SHUTTING_DOWN},
    # LISTENING -> LISTENING re-arms after a dropped turn; -> SPEAKING when
    # the learner asks the teacher to repeat.
    LISTENING: {LISTENING, PROCESSING_SPEECH, SPEAKING, IDLE, PRACTICE_WAITING, PAUSED, ERROR,
                SHUTTING_DOWN},
    # A transcript can be a voice command ("repeat that" -> SPEAKING) or empty
    # (-> LISTENING again).
    PROCESSING_SPEECH: {GENERATING_RESPONSE, SPEAKING, LISTENING, PRACTICE_WAITING, IDLE,
                        PAUSED, ERROR, SHUTTING_DOWN},
    GENERATING_RESPONSE: {SPEAKING, IDLE, PAUSED, ERROR, SHUTTING_DOWN},
    # SPEAKING -> SPEAKING restarts the voice (slower, or the next part).
    SPEAKING: {SPEAKING, LISTENING, PRACTICE_WAITING, IDLE, PAUSED, ERROR, SHUTTING_DOWN},
    PRACTICE_WAITING: {PRACTICE_WAITING, LISTENING, SPEAKING, GENERATING_RESPONSE, IDLE, PAUSED,
                       ERROR, SHUTTING_DOWN},
    PAUSED: {IDLE, LISTENING, PRACTICE_WAITING, SPEAKING, GENERATING_RESPONSE, ERROR,
             SHUTTING_DOWN},
    ERROR: {IDLE, LISTENING, PROCESSING_SPEECH, GENERATING_RESPONSE, SPEAKING, PAUSED,
            SHUTTING_DOWN},
    SHUTTING_DOWN: set(),
}

# Seconds a state may last before the watchdog calls it stuck (None: no limit).
# LISTENING has its own bounds (no-speech timeout, longest turn) in the VAD.
DEADLINES = {
    INITIALISING: 30.0,
    PROCESSING_SPEECH: 60.0,
    GENERATING_RESPONSE: 75.0,
    SPEAKING: None,             # set per utterance from the text's length
    SHUTTING_DOWN: 10.0,
}


class InvalidTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self, on_change: Optional[Callable[[str, str, str], None]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.state = INITIALISING
        self.reason = "start"
        self.clock = clock
        self.entered_at = clock()
        self.deadline: Optional[float] = DEADLINES.get(INITIALISING)
        self.on_change = on_change
        self.history = []            # (time, from, to, reason), the last 50

    def can(self, target: str) -> bool:
        return target in TRANSITIONS[self.state]

    def go(self, target: str, reason: str = "", deadline: Optional[float] = None) -> bool:
        """Move to ``target``. Returns False (and changes nothing) if not allowed."""
        if target not in TRANSITIONS:
            raise InvalidTransition(f"unknown state {target}")
        if not self.can(target):
            log.warning("refused transition %s -> %s (%s)", self.state, target, reason)
            return False
        previous = self.state
        self.state = target
        self.reason = reason
        self.entered_at = self.clock()
        self.deadline = deadline if deadline is not None else DEADLINES.get(target)
        self.history.append((self.entered_at, previous, target, reason))
        del self.history[:-50]
        log.info("state %s -> %s (%s)", previous, target, reason)
        if self.on_change is not None:
            self.on_change(previous, target, reason)
        return True

    def must(self, target: str, reason: str = "", deadline: Optional[float] = None) -> None:
        if not self.go(target, reason, deadline):
            raise InvalidTransition(f"{self.state} -> {target} ({reason})")

    @property
    def elapsed(self) -> float:
        return self.clock() - self.entered_at

    def overdue(self) -> bool:
        return self.deadline is not None and self.elapsed > self.deadline

    def is_(self, *states: str) -> bool:
        return self.state in states
