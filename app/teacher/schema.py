"""The shape of the teacher's answers, and how they are checked.

The language model is asked for JSON matching ``TEACHER_SCHEMA``: what to
say aloud plus the teaching notes for the screen. The answer is validated
before anything is shown or spoken (``parse_turn``): missing fields get
defaults, wrong types are coerced or dropped, lists are capped, text is
trimmed, and the spoken text is cleaned of markdown and JSON so the voice
never reads out notation. An answer without anything to say is replaced by
a polite request to repeat, and flagged.

The schema keeps to features both OpenAI's strict mode and Anthropic's
structured outputs accept: objects with every property required,
``additionalProperties: false``, strings, booleans, arrays and string
enums.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PRIORITIES = ("meaning", "grammar", "fluency")      # Levels 1, 2 and 3
CATEGORIES = ("meaning", "grammar", "tense", "agreement", "article", "preposition",
              "word_choice", "vocabulary", "word_order", "naturalness", "register",
              "pronunciation", "other")
MODES = ("conversation", "focused", "practice_feedback", "explanation", "interview_question",
         "interview_feedback", "listening", "roleplay")
LEVELS = ("", "A1", "A2", "B1", "B2", "C1", "C2")


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


_STRING = {"type": "string"}
_BOOL = {"type": "boolean"}

TEACHER_SCHEMA = _object({
    "reply_text": {"type": "string",
                   "description": "Exactly what the teacher says aloud, natural spoken English"},
    "intended_meaning": {"type": "string",
                         "description": "What the learner meant, when it differs from what they said"},
    "corrections": {"type": "array", "items": _object({
        "priority": {"type": "string", "enum": list(PRIORITIES)},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "you_said": _STRING,
        "better": _STRING,
        "explanation": _STRING,
    })},
    "natural_alternative": _STRING,
    "vocabulary_notes": {"type": "array", "items": _object({
        "phrase": _STRING, "meaning": _STRING, "example": _STRING})},
    "follow_up_question": _STRING,
    "practice_target": {"type": "string",
                        "description": "A phrase the learner should say now, or empty"},
    "teaching_mode": {"type": "string", "enum": list(MODES)},
    "recognition_doubt": {"type": "boolean",
                          "description": "The transcript looks like a speech recognition error"},
    "uncertainty": _STRING,
    "level_estimate": {"type": "string", "enum": list(LEVELS)},
    "interview_feedback": _object({
        "answered_question": _STRING, "structure": _STRING, "language": _STRING,
        "delivery": _STRING, "improved_answer": _STRING}),
    "end_session": _BOOL,
})

SUMMARY_SCHEMA = _object({
    "went_well": {"type": "array", "items": _STRING},
    "main_improvement": _STRING,
    "new_phrase": _STRING,
    "new_phrase_meaning": _STRING,
    "next_exercise": _STRING,
    "spoken_summary": _STRING,
})

FALLBACK_REPLY = "Sorry, I lost my thread there. Could you say that again?"

LIMITS = {"reply_text": 900, "intended_meaning": 200, "natural_alternative": 200,
          "follow_up_question": 200, "practice_target": 160, "uncertainty": 160}


@dataclass
class Correction:
    priority: str
    category: str
    you_said: str
    better: str
    explanation: str


@dataclass
class VocabNote:
    phrase: str
    meaning: str
    example: str


@dataclass
class TeacherTurn:
    reply_text: str
    intended_meaning: str = ""
    corrections: list = field(default_factory=list)
    natural_alternative: str = ""
    vocabulary_notes: list = field(default_factory=list)
    follow_up_question: str = ""
    practice_target: str = ""
    teaching_mode: str = "conversation"
    recognition_doubt: bool = False
    uncertainty: str = ""
    level_estimate: str = ""
    interview_feedback: dict = field(default_factory=dict)
    end_session: bool = False
    problems: list = field(default_factory=list)      # what validation had to fix

    @property
    def spoken(self) -> str:
        return speakable(self.reply_text)

    def history_text(self) -> str:
        """Compact JSON-ish record of this turn for the conversation history."""
        import json
        record = {"reply_text": self.reply_text}
        if self.practice_target:
            record["practice_target"] = self.practice_target
        if self.corrections:
            record["corrections"] = [{"you_said": c.you_said, "better": c.better}
                                     for c in self.corrections]
        return json.dumps(record, ensure_ascii=False)


@dataclass
class Summary:
    went_well: list
    main_improvement: str = ""
    new_phrase: str = ""
    new_phrase_meaning: str = ""
    next_exercise: str = ""
    spoken_summary: str = ""


def _text(value, limit: int = 300) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value) if isinstance(value, (int, float)) else ""
    value = " ".join(value.split())
    if len(value) > limit:
        cut = value[:limit]
        stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        value = cut[:stop + 1] if stop > limit // 2 else cut.rstrip() + "…"
    return value


def _choice(value, options, default):
    return value if value in options else default


def parse_turn(raw, correction_limit: int = 2) -> TeacherTurn:
    problems = []
    if not isinstance(raw, dict):
        return TeacherTurn(FALLBACK_REPLY, problems=["answer was not an object"])
    reply = _text(raw.get("reply_text"), LIMITS["reply_text"])
    corrections = []
    items = raw.get("corrections") if isinstance(raw.get("corrections"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        better = _text(item.get("better"), 200)
        if not better:
            continue
        corrections.append(Correction(
            _choice(item.get("priority"), PRIORITIES, "grammar"),
            _choice(item.get("category"), CATEGORIES, "other"),
            _text(item.get("you_said"), 200), better, _text(item.get("explanation"), 240)))
    # meaning first, then grammar, then fluency; then the learner's limit
    corrections.sort(key=lambda c: PRIORITIES.index(c.priority))
    if len(corrections) > correction_limit:
        problems.append(f"{len(corrections)} corrections, kept {correction_limit}")
        corrections = corrections[:correction_limit]
    # the same mistake twice, or a "correction" that changes nothing
    unique, seen = [], set()
    for correction in corrections:
        key = correction.better.lower()
        if key in seen or correction.better.strip(" .!?").lower() == correction.you_said.strip(" .!?").lower():
            continue
        seen.add(key)
        unique.append(correction)
    notes = []
    items = raw.get("vocabulary_notes") if isinstance(raw.get("vocabulary_notes"), list) else []
    for item in items[:3]:
        if isinstance(item, dict) and _text(item.get("phrase")):
            notes.append(VocabNote(_text(item.get("phrase"), 80), _text(item.get("meaning"), 200),
                                   _text(item.get("example"), 200)))
    feedback = raw.get("interview_feedback") if isinstance(raw.get("interview_feedback"), dict) else {}
    feedback = {key: _text(feedback.get(key), 600 if key == "improved_answer" else 240)
                for key in ("answered_question", "structure", "language", "delivery", "improved_answer")}
    turn = TeacherTurn(
        reply_text=reply,
        intended_meaning=_text(raw.get("intended_meaning"), LIMITS["intended_meaning"]),
        corrections=unique,
        natural_alternative=_text(raw.get("natural_alternative"), LIMITS["natural_alternative"]),
        vocabulary_notes=notes,
        follow_up_question=_text(raw.get("follow_up_question"), LIMITS["follow_up_question"]),
        practice_target=_text(raw.get("practice_target"), LIMITS["practice_target"]),
        teaching_mode=_choice(raw.get("teaching_mode"), MODES, "conversation"),
        recognition_doubt=raw.get("recognition_doubt") is True,
        uncertainty=_text(raw.get("uncertainty"), LIMITS["uncertainty"]),
        level_estimate=_choice(raw.get("level_estimate"), LEVELS, ""),
        interview_feedback={k: v for k, v in feedback.items() if v},
        end_session=raw.get("end_session") is True,
        problems=problems,
    )
    if not speakable(turn.reply_text):
        turn.problems.append("nothing to say")
        turn.reply_text = turn.follow_up_question or FALLBACK_REPLY
    return turn


def parse_summary(raw) -> Summary:
    if not isinstance(raw, dict):
        return Summary([], spoken_summary="Well done today. See you next time.")
    went = raw.get("went_well") if isinstance(raw.get("went_well"), list) else []
    return Summary(
        [_text(item, 160) for item in went[:3] if _text(item, 160)],
        _text(raw.get("main_improvement"), 240),
        _text(raw.get("new_phrase"), 100),
        _text(raw.get("new_phrase_meaning"), 200),
        _text(raw.get("next_exercise"), 200),
        _text(raw.get("spoken_summary"), 500) or "Well done today. See you next time.",
    )


_MARKDOWN = re.compile(r"(\*+|__|`+|#+\s*|^\s*[-•]\s+)", re.MULTILINE)
_BRACES = re.compile(r"[{}\[\]<>]")
_KEYS = re.compile(r'"?\b(reply_text|corrections|practice_target|teaching_mode)\b"?\s*:', re.I)


def speakable(text: str) -> str:
    """Text safe to send to the voice: no markdown, braces or field names."""
    if not text:
        return ""
    text = _KEYS.sub("", text)
    text = _MARKDOWN.sub("", text)
    text = _BRACES.sub("", text)
    text = text.replace("\\n", " ")
    return " ".join(text.split()).strip(" ,;")
