"""Settings: one table with each setting's meaning, default and validation.

The learner changes the ``menu`` settings on the device (Settings screen);
the advanced ones (models, audio devices, voice detection) are edited in
``settings.json`` in the data folder. Every value is validated on load: an
unknown key is dropped, an invalid value falls back to its default, and a
file that is not JSON is kept as ``settings.json.broken-<time>`` for
inspection while defaults are used. Writes are atomic (temporary file,
then rename) and owner-only.

Secrets (API keys) are not settings: see ``envfile.py``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("mlearning.config")


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    default: Any
    choices: tuple = ()          # ((value, text), ...) for a fixed list
    kind: str = "choice"         # choice | bool | int | float | text
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    menu: bool = True            # shown in the device's Settings list
    help: str = ""

    def valid(self, value: Any) -> bool:
        if self.kind == "choice":
            return any(value == option for option, _ in self.choices)
        if self.kind == "bool":
            return isinstance(value, bool)
        if self.kind == "int":
            return isinstance(value, int) and not isinstance(value, bool) and self._in_range(value)
        if self.kind == "float":
            return isinstance(value, (int, float)) and not isinstance(value, bool) and self._in_range(value)
        if self.kind == "text":
            return isinstance(value, str) and len(value) <= 200
        return False

    def _in_range(self, value) -> bool:
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True

    def text(self, value: Any) -> str:
        if self.kind == "choice":
            for option, label in self.choices:
                if option == value:
                    return label
        if self.kind == "bool":
            return "On" if value else "Off"
        return str(value)


VOICES = ("alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")

_TABLE = (
    # -- how the lesson feels (device Settings) -------------------------------
    Setting("listening_mode", "Listening", "hands_free",
            (("hands_free", "Hands-free"), ("hold", "Hold to talk")),
            help="Hands-free listens after the teacher speaks; hold to talk records only while held"),
    Setting("patience", "Pause before reply", "normal",
            (("short", "Short"), ("normal", "Normal"), ("long", "Long")),
            help="How long a silence ends your turn: short 0.8 s, normal 1.3 s, long 2.2 s"),
    Setting("speech_speed", "Teacher speed", "normal",
            (("slow", "Slow"), ("normal", "Normal"), ("fast", "Fast"))),
    Setting("corrections", "Corrections", "normal",
            (("few", "Few"), ("normal", "Normal"), ("many", "Many")),
            help="At most 1, 2 or 3 corrections in a turn"),
    Setting("style", "Teaching style", "natural",
            (("natural", "Natural talk"), ("focused", "Focused")),
            help="Natural: conversation first; focused: explain and practise every mistake"),
    Setting("level", "My level", "auto",
            (("auto", "Automatic"), ("beginner", "Beginner"), ("intermediate", "Intermediate"),
             ("advanced", "Advanced"))),
    Setting("variety", "English", "australian",
            (("australian", "Australian"), ("british", "British"), ("american", "American")),
            help="Spelling, words and expressions the teacher prefers"),
    Setting("voice", "Teacher voice", "nova", tuple((v, v.capitalize()) for v in VOICES)),
    Setting("session_minutes", "Lesson length", 15,
            ((10, "10 min"), (15, "15 min"), (20, "20 min"), (30, "30 min"), (0, "No limit")),
            help="The teacher suggests finishing after this long"),
    Setting("cues", "Sounds", True, kind="bool",
            help="A short blip when the teacher starts listening"),
    Setting("keep_transcripts", "Keep transcripts", "off",
            (("off", "Off"), ("30days", "30 days")),
            help="Off: only lesson summaries and learning progress are saved"),
    Setting("llm_provider", "Teacher AI", "openai",
            (("openai", "OpenAI"), ("anthropic", "Claude"))),
    Setting("daily_goal_minutes", "Daily goal", 10,
            ((5, "5 min"), (10, "10 min"), (15, "15 min"), (20, "20 min"), (30, "30 min"))),
    # -- interview practice (Interview screen) --------------------------------
    Setting("interview_job", "Job", "", kind="text", menu=False,
            help="Empty: the interviewer asks which job you are preparing for"),
    Setting("interview_type", "Interview type", "general",
            (("general", "General"), ("behavioural", "Behavioural"), ("technical", "Technical"),
             ("mixed", "Mixed")), menu=False),
    Setting("interview_difficulty", "Difficulty", "standard",
            (("easy", "Easy"), ("standard", "Standard"), ("hard", "Hard")), menu=False),
    Setting("interview_feedback", "Feedback", "standard",
            (("gentle", "Gentle"), ("standard", "Standard"), ("thorough", "Thorough")), menu=False),
    # -- advanced (settings.json only) ----------------------------------------
    Setting("openai_asr_model", "ASR model", "whisper-1", kind="text", menu=False),
    Setting("openai_llm_model", "OpenAI teacher model", "gpt-4o", kind="text", menu=False),
    Setting("openai_tts_model", "TTS model", "tts-1-hd", kind="text", menu=False),
    Setting("anthropic_model", "Claude model", "claude-opus-5-5", kind="text", menu=False),
    Setting("capture_device", "Microphone", "auto", kind="text", menu=False,
            help="auto, or an ALSA device such as plughw:3 or capture"),
    Setting("playback_device", "Speaker", "auto", kind="text", menu=False,
            help="auto, or an ALSA device such as plughw:3 or playback"),
    Setting("vad_start_db", "Speech starts above noise by", 10.0, kind="float",
            minimum=3, maximum=30, menu=False),
    Setting("vad_end_db", "Speech continues above noise by", 6.0, kind="float",
            minimum=1, maximum=25, menu=False),
    Setting("vad_min_speech_dbfs", "Quietest speech", -52.0, kind="float",
            minimum=-80, maximum=-20, menu=False),
    Setting("no_speech_timeout", "Stop listening after silence (s)", 30, kind="int",
            minimum=5, maximum=300, menu=False),
    Setting("max_utterance", "Longest turn (s)", 60, kind="int", minimum=5, maximum=180,
            menu=False),
    Setting("request_timeout", "Network timeout (s)", 30, kind="int", minimum=5, maximum=120,
            menu=False),
)

SETTINGS = {setting.key: setting for setting in _TABLE}
MENU_KEYS = tuple(setting.key for setting in _TABLE if setting.menu)

PATIENCE_SECONDS = {"short": 0.8, "normal": 1.3, "long": 2.2}
SPEED_FACTORS = {"slow": 0.8, "normal": 1.0, "fast": 1.2}
CORRECTION_LIMITS = {"few": 1, "normal": 2, "many": 3}


@dataclass
class Settings:
    """The validated settings; ``save()`` writes them back atomically."""
    path: Optional[str] = None
    values: dict = field(default_factory=lambda: {k: s.default for k, s in SETTINGS.items()})
    problem: str = ""            # why the file could not be used, if it could not

    def __getitem__(self, key: str):
        return self.values[key]

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value) -> bool:
        setting = SETTINGS[key]
        if not setting.valid(value):
            raise ValueError(f"{key}: invalid value {value!r}")
        changed = self.values.get(key) != value
        self.values[key] = value
        return changed

    def step(self, key: str, direction: int = 1):
        """The next (or previous) value of a choice or bool setting; returns it."""
        setting = SETTINGS[key]
        if setting.kind == "bool":
            self.values[key] = not self.values[key]
        elif setting.kind == "choice":
            options = [option for option, _ in setting.choices]
            index = options.index(self.values[key]) if self.values[key] in options else 0
            self.values[key] = options[(index + direction) % len(options)]
        else:
            raise ValueError(f"{key} cannot be stepped")
        return self.values[key]

    def peek(self, key: str, direction: int = 1):
        """The value ``step`` would set, without setting it."""
        before = self.values[key]
        value = self.step(key, direction)
        self.values[key] = before
        return value

    def text(self, key: str) -> str:
        return SETTINGS[key].text(self.values[key])

    def reset(self) -> None:
        self.values = {k: s.default for k, s in SETTINGS.items()}

    # derived values
    @property
    def end_silence(self) -> float:
        return PATIENCE_SECONDS[self.values["patience"]]

    @property
    def speed_factor(self) -> float:
        return SPEED_FACTORS[self.values["speech_speed"]]

    @property
    def correction_limit(self) -> int:
        return CORRECTION_LIMITS[self.values["corrections"]]

    def save(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump({"version": 1, "settings": self.values}, out, indent=2, sort_keys=True)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def validate(raw: dict) -> tuple:
    """(values, [problems]) from a decoded settings mapping."""
    values = {k: s.default for k, s in SETTINGS.items()}
    problems = []
    for key, value in raw.items():
        setting = SETTINGS.get(key)
        if setting is None:
            problems.append(f"unknown setting {key!r} ignored")
            continue
        if setting.kind == "float" and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if setting.valid(value):
            values[key] = value
        else:
            problems.append(f"{key}: invalid value {value!r}, using {setting.default!r}")
    return values, problems


def load(path: str) -> Settings:
    settings = Settings(path=path)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return settings
    except (OSError, ValueError) as exc:
        kept = f"{path}.broken-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(path, kept)
        except OSError:
            kept = path
        settings.problem = f"settings file unreadable ({exc.__class__.__name__}); kept as {os.path.basename(kept)}"
        log.warning("%s; defaults loaded", settings.problem)
        return settings
    raw = data.get("settings", data) if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    settings.values, problems = validate(raw)
    for problem in problems:
        log.warning("settings: %s", problem)
    return settings
