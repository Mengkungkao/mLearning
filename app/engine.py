"""The tutor engine: one thread that owns the lesson.

Everything that changes the lesson happens here, on the engine thread, in
response to an event in its queue: a request from the screen (start a
lesson, hold to talk, pause, retry), a result from a worker (transcript,
teacher's answer, voice finished) or a timer. Workers -- speech
recognition, the teacher, the voice -- run on their own threads and only
post results back.

Turn ids keep the order. Every result carries the turn it belongs to; the
engine moves to a new turn whenever the learner interrupts, retries or
leaves, and drops results for any other turn. A slow answer to a question
the learner has already abandoned is therefore never spoken, and two
voices can never overlap.

Nothing waits forever: every state with a worker has a deadline (state
machine ``DEADLINES``, and a per-utterance one for speaking), and the
watchdog turns an overdue state into ERROR with Retry / Details / Back.
What failed is kept (``pending``), so Retry repeats exactly that step.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from . import paths
from .audio import dsp
from .audio.capture import CAPTURE_ERROR, NO_SPEECH, SPEECH_START, UTTERANCE
from .audio.playback import PlayResult
from .audio.vad import VadConfig
from .config import SENSITIVITY_DB, SETTINGS, Settings
from .envfile import Secrets
from .learning.profile import LearnerProfile
from .learning.storage import LearningStorage
from .providers import registry
from .providers.base import TIMEOUT, UNAVAILABLE, ProviderError
from .state_machine import (ERROR, GENERATING_RESPONSE, IDLE, INITIALISING, LISTENING, PAUSED,
                            PRACTICE_WAITING, PROCESSING_SPEECH, SHUTTING_DOWN, SPEAKING,
                            StateMachine)
from .teacher import commands, modes, practice, prompts
from .teacher.schema import (SUMMARY_SCHEMA, TEACHER_SCHEMA, Summary, TeacherTurn, parse_summary,
                             parse_turn)
from .teacher.session import Session

log = logging.getLogger("mlearning.engine")
lifecycle = logging.getLogger("mlearning.lifecycle")

# After the speaker stops, the room still rings and ALSA's buffer drains:
# the microphone opens this much later so the teacher's last syllable is not
# heard as the learner's first. (Acoustic tail, not a race fix.)
ECHO_GUARD = 0.3
MIN_HOLD_SECONDS = 0.35
MAX_EMPTY_IN_A_ROW = 3
NOTICE_SECONDS = 2.5


@dataclass
class ErrorInfo:
    title: str
    remedy: str
    details: list = field(default_factory=list)
    stage: str = ""                 # what Retry repeats
    kind: str = ""


@dataclass
class Pending:
    """The step in progress, kept so Retry can repeat it."""
    stage: str                      # transcribe | respond | speak | summary | listen
    pcm: bytes = b""
    user_content: str = ""
    learner_text: str = ""
    typed: bool = False
    text: str = ""
    speed: float = 1.0


@dataclass(frozen=True)
class View:
    """What the screen needs, replaced (never mutated) after every change."""
    state: str = INITIALISING
    in_lesson: bool = False
    activity_id: str = ""
    page: str = ""
    listening_mode: str = "hands_free"
    mic_on: bool = False
    hearing: bool = False
    holding: bool = False
    last_transcript: str = ""
    turn: Optional[TeacherTurn] = None
    speaking_text: str = ""
    practice_target: str = ""
    practice_result: str = ""
    error: Optional[ErrorInfo] = None
    notice: str = ""
    summary: Optional[Summary] = None
    finishing: bool = False
    can_teach: bool = False
    can_speak: bool = False
    problems: tuple = ()
    remote: tuple = ()
    lesson_started: float = 0.0
    lesson_minutes: int = 0


class MicError(Exception):
    pass


class SpeakerError(Exception):
    pass


STAGE_NAMES = {"transcribe": "Speech recognition", "respond": "The teacher", "summary": "The teacher",
               "speak": "The voice", "init": "Starting up"}


class Engine:
    def __init__(self, settings: Settings, secrets: Secrets, storage: LearningStorage,
                 audio_in=None, audio_out=None, providers: Optional[registry.Providers] = None,
                 clock: Callable[[], float] = time.monotonic, threaded: bool = True,
                 build_providers: Callable = registry.build, text_only: bool = False):
        self.settings = settings
        self.secrets = secrets
        self.storage = storage
        self.clock = clock
        self.threaded = threaded
        self.build_providers = build_providers
        self.text_only = text_only      # terminal lessons: typed in, printed out, no voice
        self.audio_in = audio_in
        self.audio_out = audio_out
        self.providers = providers or registry.Providers()
        self._fixed_providers = providers is not None
        self.profile: LearnerProfile = storage.load_profile()
        self.sm = StateMachine(on_change=self._state_changed, clock=clock)
        self.queue: "queue.Queue" = queue.Queue()
        self.listeners: list = []
        self.turn = 0
        self.session: Optional[Session] = None
        self.pending: Optional[Pending] = None
        self.error: Optional[ErrorInfo] = None
        self.summary: Optional[Summary] = None
        self.finishing = False
        self.finish_after_speaking = False
        self.time_up_pending = False
        self.empty_in_a_row = 0
        self.last_transcript = ""
        self.practice_result = ""
        self.speaking_text = ""
        self.last_audio = b""
        self.last_audio_rate = 0
        self.hearing = False
        self.holding = False
        self.notice = ""
        self.notice_until = 0.0
        self.timers: list = []          # [due, name, turn]
        self.view = View()
        self.ready = threading.Event()  # set once providers and devices are known (init done)
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # =============================================================== public
    # Called from any thread: everything is posted to the engine thread.
    def post(self, event: str, *args) -> None:
        self.queue.put((event, args))

    def start_lesson(self, activity_id: str) -> None:
        self.post("start_lesson", activity_id)

    def end_lesson(self) -> None:
        self.post("end_lesson")

    def leave_summary(self) -> None:
        self.post("leave_summary")

    def talk_start(self) -> None:
        self.post("talk_start")

    def talk_end(self) -> None:
        self.post("talk_end")

    def toggle_pause(self) -> None:
        self.post("toggle_pause")

    def repeat(self) -> None:
        self.post("command", commands.REPEAT)

    def slower(self) -> None:
        self.post("command", commands.SLOWER)

    def faster(self) -> None:
        self.post("command", commands.FASTER)

    def typed(self, text: str) -> None:
        self.post("typed", text)

    def say(self, text: str) -> None:
        """Speak a phrase outside a lesson (My phrases > Hear it)."""
        self.post("say", text)

    def stop_speaking(self) -> None:
        self.post("stop_speaking")

    def remove_phrase(self, phrase: str) -> None:
        self.post("remove_phrase", phrase)

    def retry(self) -> None:
        self.post("retry")

    def dismiss_error(self) -> None:
        self.post("dismiss_error")

    def set_setting(self, key: str, value) -> None:
        self.post("set_setting", key, value)

    def reload(self) -> None:
        self.post("reload")

    def delete_history(self) -> None:
        self.post("delete_history")

    def focus(self, has_focus: bool) -> None:
        self.post("focus", has_focus)

    def add_listener(self, callback: Callable[[], None]) -> None:
        self.listeners.append(callback)

    def on_capture(self, kind: str, turn: int, payload) -> None:
        """AudioInput's callback (capture thread)."""
        self.post("capture", kind, turn, payload)

    # live values for the screen (read without the engine thread)
    def mic_level(self) -> float:
        return self.audio_in.level if self.audio_in is not None else dsp.SILENCE_DBFS

    def noise_floor(self) -> float:
        return self.audio_in.floor if self.audio_in is not None else -60.0

    def speak_progress(self) -> tuple:
        """(seconds heard, seconds received) of the current speech."""
        out = self.audio_out
        if out is None or not out.playing:
            return 0.0, 0.0
        return out.progress(), out.queued_seconds

    # ============================================================ lifecycle
    def start(self) -> None:
        """Initialise (providers, devices) and, if threaded, run the loop."""
        self._running = True
        if self.threaded:
            self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
            self._thread.start()
        self.post("init")
        if not self.threaded:
            self.pump()

    def stop(self, timeout: float = 2.5) -> None:
        """Leave now: stop sound, save progress. Returns within ``timeout``."""
        self.post("shutdown")
        if self.threaded and self._thread is not None:
            self._thread.join(timeout)
        else:
            self.pump()

    def pump(self, limit: int = 1000) -> int:
        """Process queued events now (tests and the non-threaded mode)."""
        handled = 0
        while handled < limit:
            try:
                event, args = self.queue.get_nowait()
            except queue.Empty:
                self._timers_due()
                if self.queue.empty():
                    break
                continue
            self._handle(event, args)
            handled += 1
        return handled

    def _loop(self) -> None:
        while self._running:
            timeout = 0.25
            if self.timers:
                timeout = max(0.0, min(timeout, min(t[0] for t in self.timers) - self.clock()))
            try:
                event, args = self.queue.get(timeout=timeout)
            except queue.Empty:
                event = None
            try:
                if event is not None:
                    self._handle(event, args)
                self._timers_due()
                self._watchdog()
            except Exception:       # isolation boundary: a bug must not kill the lesson loop
                log.exception("engine event %s failed", event)
                self._fail("internal", RuntimeError("internal error"))

    def _handle(self, event: str, args: tuple) -> None:
        handler = getattr(self, f"_on_{event}", None)
        if handler is None:
            log.warning("unknown event %s", event)
            return
        handler(*args)
        self._publish()

    def _timers_due(self) -> None:
        now = self.clock()
        due = [t for t in self.timers if t[0] <= now]
        if not due:
            if self.notice and now >= self.notice_until:
                self.notice = ""
                self._publish()
            return
        self.timers = [t for t in self.timers if t[0] > now]
        for _when, name, turn in due:
            if turn == self.turn:
                handler = getattr(self, f"_timer_{name}")
                handler()
        if self.notice and now >= self.notice_until:
            self.notice = ""
        self._publish()

    def _after(self, seconds: float, name: str) -> None:
        self.timers.append([self.clock() + seconds, name, self.turn])

    def _watchdog(self) -> None:
        if self.sm.overdue():
            state = self.sm.state
            log.warning("watchdog: %s for %.0f s", state, self.sm.elapsed)
            if state == SHUTTING_DOWN:
                self._running = False
                return
            stage = {PROCESSING_SPEECH: "transcribe", GENERATING_RESPONSE: "respond",
                     SPEAKING: "speak", INITIALISING: "init"}.get(state, "")
            if stage == "respond" and self.finishing:
                stage = "summary"
            self._fail(stage, ProviderError(TIMEOUT, STAGE_NAMES.get(stage, "mLearning"),
                                            f"{state} took too long"))
            self._publish()

    def _spawn(self, name: str, target: Callable, *args) -> None:
        if self.threaded:
            threading.Thread(target=target, args=args, name=name, daemon=True).start()
        else:
            target(*args)

    # ============================================================== events
    def _on_init(self) -> None:
        self.reload_providers()
        self.sm.go(IDLE, "ready")
        self.ready.set()
        problems = "; ".join(p.title for p in self.providers.problems) or "none"
        lifecycle.info("app ready: teacher=%s problems=%s", self.can_teach, problems)

    @property
    def can_teach(self) -> bool:
        if self.text_only:
            return self.providers.llm is not None
        return self.providers.can_teach

    def reload_providers(self) -> None:
        if self._fixed_providers:
            return
        self.secrets = Secrets(self.secrets.path)
        self.providers = self.build_providers(self.settings, self.secrets)

    def _on_reload(self) -> None:
        self.reload_providers()

    def _on_start_lesson(self, activity_id: str) -> None:
        if self.session is not None or not self.sm.is_(IDLE):
            log.info("start_lesson ignored in %s", self.sm.state)
            return
        if not self.can_teach:
            self.reload_providers()
        if not self.can_teach:
            problem = self.providers.problems[0] if self.providers.problems else None
            self._fail("setup", ProviderError(UNAVAILABLE, "Teacher",
                                              problem.title if problem else "not set up"))
            return
        activity = modes.get(activity_id)
        system = prompts.system_prompt(
            self.settings, activity, self.profile.notes_for_teacher(),
            self.settings["session_minutes"], self.profile.listening_success(activity.id))
        self.session = Session(activity, system, self.settings["keep_transcripts"] != "off",
                               clock=self.clock)
        self.summary = None
        self.error = None
        self.time_up_pending = False
        self.empty_in_a_row = 0
        self.last_transcript = ""
        self.practice_result = ""
        self.last_audio = b""
        self.turn += 1
        lifecycle.info("lesson start: activity=%s session=%s", activity.id, self.session.id)
        if self.audio_in is not None and not self.text_only:
            self.audio_in.set_gate(False)
            if not self.audio_in.start():
                log.warning("microphone not open at lesson start: %s", self.audio_in.error)
        note = self.profile.last_summary.get("next_exercise", "")
        self._respond(Pending("respond", user_content=prompts.opening_message(note)))

    def _on_talk_start(self) -> None:
        if self.session is None or self.finishing:
            return
        state = self.sm.state
        if state in (PROCESSING_SPEECH, GENERATING_RESPONSE):
            self._notify("One moment…")
            return
        if state == ERROR:
            return
        if state == SPEAKING:
            self._interrupt_voice()
        if not self._mic_ready():
            return
        self.turn += 1
        self.audio_in.set_gate(True)
        self.audio_in.hold_start(self.turn)
        self.holding = True
        if not self.sm.is_(LISTENING):
            self.sm.go(LISTENING, "hold to talk")

    def _on_talk_end(self) -> None:
        if not self.holding:
            return
        self.holding = False
        pcm = self.audio_in.hold_end() if self.audio_in is not None else None
        self.audio_in.set_gate(False)
        if not pcm or dsp.seconds_of(pcm, dsp.SPEECH_RATE) < MIN_HOLD_SECONDS:
            self._notify("Hold while you speak")
            self._wait_for_learner(listen=True)
            return
        level = dsp.rms_dbfs(dsp.from_pcm16(pcm))
        if level < max(-62.0, self.noise_floor() + 3.0):
            self._notify("I didn't hear anything")
            self._wait_for_learner(listen=True)
            return
        self._transcribe(pcm)

    def _on_capture(self, kind: str, turn: int, payload) -> None:
        if kind == CAPTURE_ERROR:
            if self.session is not None and self.sm.is_(LISTENING, PRACTICE_WAITING, IDLE):
                self._fail("listen", MicError(payload))
            return
        if turn != self.turn:
            return
        if kind == SPEECH_START:
            self.hearing = True
            if self.sm.is_(PRACTICE_WAITING):
                self.sm.go(LISTENING, "learner speaking")
        elif kind == UTTERANCE:
            self.hearing = False
            self.audio_in.set_gate(False)
            if self.session is not None:
                self.session.learner_seconds += payload.speech_seconds
            self._transcribe(payload.pcm)
        elif kind == NO_SPEECH:
            self.hearing = False
            self.audio_in.disarm()
            self.audio_in.set_gate(False)
            self.audio_in.stop()           # nobody is talking: the microphone goes off
            self._notify("Microphone off")
            self.sm.go(IDLE, "no speech")

    def _on_transcribed(self, turn: int, transcript) -> None:
        if turn != self.turn or not self.sm.is_(PROCESSING_SPEECH):
            return
        if isinstance(transcript, Exception):
            self._fail("transcribe", transcript)
            return
        text = transcript.text.strip()
        if not text or transcript.no_speech:
            self.empty_in_a_row += 1
            self._notify("I didn't catch that")
            if self.empty_in_a_row >= MAX_EMPTY_IN_A_ROW:
                self.empty_in_a_row = 0
                self.sm.go(IDLE, "nothing recognised")
                return
            self._wait_for_learner(listen=True)
            return
        self.empty_in_a_row = 0
        self._learner_said(text, transcript.confidence, typed=False)

    def _on_typed(self, text: str) -> None:
        text = " ".join((text or "").split())
        if not text or self.session is None:
            return
        if not self.sm.is_(IDLE, LISTENING, PRACTICE_WAITING, PAUSED):
            self._notify("One moment…")
            return
        if self.audio_in is not None:
            self.audio_in.disarm()
            self.audio_in.set_gate(False)
        self.turn += 1
        self._learner_said(text, None, typed=True)

    def _learner_said(self, text: str, confidence, typed: bool) -> None:
        self.last_transcript = text
        command = commands.detect(text)
        if command is not None:
            lifecycle.info("voice command: %s", command)
            self._on_command(command, spoken=True)
            return
        session = self.session
        result = None
        if session.practice_target:
            result = practice.compare(session.practice_target, text)
            self.practice_result = result.short()
            if session.activity.id == "repeat":
                session.listening_results.append(result.score >= 0.8)
        else:
            self.practice_result = ""
        note = ""
        if self.time_up_pending and not session.time_up_noted:
            note = prompts.TIME_UP_NOTE
            session.time_up_noted = True
        content = prompts.learner_message(text, confidence, typed, result, note)
        if session.activity.id in ("retell", "real-world"):
            session.listening_results.append(True)    # attempts; the teacher judges content
        self._respond(Pending("respond", user_content=content, learner_text=text, typed=typed))

    def _on_replied(self, turn: int, result) -> None:
        if turn != self.turn or not self.sm.is_(GENERATING_RESPONSE):
            return
        if isinstance(result, Exception):
            self._fail(self.pending.stage if self.pending else "respond", result)
            return
        if self.finishing:
            self._summary_ready(result)
            return
        pending = self.pending
        session = self.session
        turn_obj: TeacherTurn = result
        session.commit(pending.user_content, turn_obj, pending.learner_text, pending.typed)
        if pending.learner_text:
            self.profile.record_turn(session.id, turn_obj)
            self._save_profile()
        planned = self.settings["session_minutes"]
        if planned and session.minutes >= planned:
            self.time_up_pending = True
        self.finish_after_speaking = turn_obj.end_session
        lifecycle.info("teacher turn: session=%s corrections=%d practice=%s mode=%s",
                       session.id, len(turn_obj.corrections), bool(turn_obj.practice_target),
                       turn_obj.teaching_mode)
        self._speak(turn_obj.spoken)

    def _on_spoken(self, turn: int, result) -> None:
        if turn != self.turn or not self.sm.is_(SPEAKING):
            return
        if isinstance(result, Exception):
            self._fail("speak", result)
            return
        if result.error:
            self._fail("speak", SpeakerError(result.error))
            return
        if result.audio:
            self.last_audio = result.audio
            if not self.last_audio_rate:
                self.last_audio_rate = self.providers.tts.rate if self.providers.tts else dsp.TTS_RATE
        if self.summary is not None and self.finishing:
            self._lesson_closed()
            return
        if self.finish_after_speaking:
            self.finish_after_speaking = False
            self._on_end_lesson()
            return
        self._wait_for_learner(listen=True)

    def _on_command(self, command: str, spoken: bool = False) -> None:
        if self.session is None or self.finishing:
            return
        if not spoken and self.sm.is_(PROCESSING_SPEECH, GENERATING_RESPONSE, ERROR):
            self._notify("One moment…")
            return
        if command == commands.REPEAT:
            if self.sm.is_(SPEAKING):
                self._interrupt_voice()
            if self.last_audio:
                self._replay()
            elif self.session.last_turn is not None:
                self._speak(self.session.last_turn.spoken)
            else:
                self._wait_for_learner()
        elif command in (commands.SLOWER, commands.FASTER):
            options = [value for value, _ in SETTINGS["speech_speed"].choices]
            index = options.index(self.settings["speech_speed"])
            index = max(0, index - 1) if command == commands.SLOWER else min(len(options) - 1, index + 1)
            self.settings.set("speech_speed", options[index])
            self._save_settings()
            self._notify(f"Speed: {self.settings.text('speech_speed')}")
            if self.sm.is_(SPEAKING):
                self._interrupt_voice()
            if self.session.last_turn is not None:
                self.last_audio = b""
                self._speak(self.session.last_turn.spoken)
            else:
                self._wait_for_learner()
        elif command == commands.PAUSE:
            self._pause()
        elif command == commands.FINISH:
            self._on_end_lesson()
        elif command == commands.INTERVIEW:
            if self.session.activity.mode == "interview":
                self._wait_for_learner()
            else:
                self._close_session(summary=None)
                self.sm.go(IDLE, "switch to interview")
                self._on_start_lesson("interview")

    def _on_say(self, text: str) -> None:
        if self.session is not None or not self.sm.is_(IDLE):
            return
        if self.providers.tts is None or self.audio_out is None:
            self._notify("The voice is not set up")
            return
        self._speak(text, self.settings.speed_factor)

    def _on_remove_phrase(self, phrase: str) -> None:
        if self.profile.remove_phrase(phrase):
            self._save_profile()
            self._notify("Removed")

    def _on_stop_speaking(self) -> None:
        if self.sm.is_(SPEAKING) and self.session is None and not self.finishing:
            self._interrupt_voice()
            self.turn += 1
            self.sm.go(IDLE, "stopped")

    def _on_toggle_pause(self) -> None:
        if self.session is None:
            return
        if self.finishing or self.sm.is_(ERROR):
            return
        if self.sm.is_(PAUSED):
            self._resume()
        elif self.sm.is_(IDLE) and self.settings["listening_mode"] == "hands_free":
            self._wait_for_learner(listen=True, resume=True)    # listening had timed out
        else:
            self._pause()

    def _on_end_lesson(self) -> None:
        session = self.session
        if session is None or self.finishing:
            return
        self._interrupt_voice()
        self._mic_off()
        self.turn += 1
        if session.turns == 0 or not self.providers.llm:
            self._close_session(summary=None)
            if self.sm.state != IDLE:
                self.sm.go(IDLE, "lesson ended")
            return
        self.finishing = True
        lifecycle.info("lesson finishing: session=%s turns=%d", session.id, session.turns)
        content = prompts.summary_record(session.entries)
        self.pending = Pending("summary", user_content=content)
        if not self.sm.is_(IDLE):
            self.sm.go(IDLE, "lesson ending")      # every state may go to IDLE
        self.sm.go(GENERATING_RESPONSE, "lesson summary")
        self._spawn("teacher", self._work_summary, self.turn, content)

    def _on_leave_summary(self) -> None:
        """The learner left the summary screen: stop the voice, back home."""
        if self.sm.is_(SPEAKING):
            self._interrupt_voice()
        if self.finishing and self.summary is not None:
            self._lesson_closed()
        elif self.session is None and not self.sm.is_(IDLE, SHUTTING_DOWN):
            self.turn += 1
            self.sm.go(IDLE, "left summary")
        self.summary = None

    def _on_retry(self) -> None:
        if not self.sm.is_(ERROR):
            return
        pending = self.pending
        self.error = None
        self.reload_providers()
        self.turn += 1
        if pending is None or pending.stage in ("setup", "internal", "init"):
            self.sm.go(IDLE, "retry")
            return
        if pending.stage == "transcribe":
            self._transcribe(pending.pcm)
        elif pending.stage in ("respond",):
            self._respond(pending)
        elif pending.stage == "summary":
            self.sm.go(GENERATING_RESPONSE, "retry summary")
            self._spawn("teacher", self._work_summary, self.turn, pending.user_content)
        elif pending.stage == "speak":
            self._speak(pending.text, pending.speed)
        elif pending.stage == "listen":
            self.sm.go(IDLE, "retry microphone")
            self._wait_for_learner(listen=True, resume=True)
        else:
            self.sm.go(IDLE, "retry")

    def _on_dismiss_error(self) -> None:
        if not self.sm.is_(ERROR):
            return
        stage = self.pending.stage if self.pending else ""
        self.error = None
        self.pending = None
        self.turn += 1
        if self.finishing:
            self.finishing = False
            self._close_session(summary=None)
        self.sm.go(IDLE, "error dismissed")
        if stage == "setup":
            return

    def _on_set_setting(self, key: str, value) -> None:
        try:
            changed = self.settings.set(key, value)
        except ValueError as exc:
            log.warning("setting refused: %s", exc)
            return
        if not changed:
            return
        self._save_settings()
        if key in ("llm_provider", "voice", "openai_llm_model", "openai_tts_model",
                   "openai_asr_model", "anthropic_model", "request_timeout"):
            self.reload_providers()

    def _on_delete_history(self) -> None:
        if self.session is not None:
            self._notify("Finish the lesson first")
            return
        self.storage.delete_all()
        self.profile = LearnerProfile(created=self.profile.created)
        lifecycle.info("learning history deleted by the learner")
        self._notify("Learning history deleted")

    def _on_focus(self, has_focus: bool) -> None:
        if not has_focus and self.session is not None and not self.sm.is_(PAUSED, ERROR):
            self._pause(reason="screen lost")

    def _on_shutdown(self) -> None:
        self._interrupt_voice()
        self._mic_off()
        if self.session is not None:
            self._close_session(summary=None)
        self.turn += 1
        self.sm.go(SHUTTING_DOWN, "exit")
        lifecycle.info("app stopping")
        self._running = False

    # ============================================================== steps
    def _mic_ready(self) -> bool:
        if self.audio_in is None or not self.audio_in.available:
            self._fail("listen", MicError(getattr(self.audio_in, "error", "") or "no microphone"))
            return False
        if not self.audio_in.running and not self.audio_in.start():
            self._fail("listen", MicError(self.audio_in.error or "microphone did not open"))
            return False
        return True

    def _mic_off(self) -> None:
        self.hearing = False
        self.holding = False
        if self.audio_in is not None:
            self.audio_in.disarm()
            self.audio_in.set_gate(False)
            self.audio_in.stop()

    def _wait_for_learner(self, listen: bool = False, resume: bool = False) -> None:
        """After the teacher spoke (or a turn was dropped): the learner's turn."""
        session = self.session
        if session is None:
            if self.sm.state != IDLE:
                self.sm.go(IDLE, "no lesson")
            return
        target = session.practice_target
        hands_free = self.settings["listening_mode"] == "hands_free" and not self.text_only
        if hands_free and (listen or resume):
            if not self._mic_ready():
                return
            self.turn += 1
            self.audio_in.listen(self.turn, self._vad_config())
            if target:
                self.sm.go(PRACTICE_WAITING, "practice, listening")
            else:
                self.sm.go(LISTENING, "learner's turn")
            if self.settings["cues"] and self.audio_out is not None and self.audio_out.available:
                self._spawn("cue", self._work_cue, self.turn)
            else:
                self._after(ECHO_GUARD, "open_gate")
        else:
            if self.audio_in is not None:
                self.audio_in.disarm()
                self.audio_in.set_gate(False)
            self.sm.go(PRACTICE_WAITING if target else IDLE, "waiting for hold to talk")

    def _timer_open_gate(self) -> None:
        if self.sm.is_(LISTENING, PRACTICE_WAITING) and self.audio_in is not None:
            self.audio_in.set_gate(True)

    def _vad_config(self) -> VadConfig:
        s = self.settings
        start_db, peak_db = SENSITIVITY_DB[s["mic_sensitivity"]]
        return VadConfig(start_db=start_db, peak_db=peak_db, end_db=s["vad_end_db"],
                         min_speech_dbfs=s["vad_min_speech_dbfs"], end_silence=s.end_silence,
                         max_utterance=float(s["max_utterance"]),
                         no_speech_timeout=float(s["no_speech_timeout"]))

    def _transcribe(self, pcm: bytes) -> None:
        self.pending = Pending("transcribe", pcm=pcm)
        if self.audio_in is not None:
            self.audio_in.disarm()
        self.sm.go(PROCESSING_SPEECH, "recognising")
        context = ""
        if self.session is not None and self.session.last_turn is not None:
            context = self.session.last_turn.reply_text[-300:]
        self._spawn("asr", self._work_transcribe, self.turn, pcm, context)

    def _respond(self, pending: Pending) -> None:
        self.pending = pending
        self.sm.go(GENERATING_RESPONSE, "teacher thinking")
        messages = self.session.request_messages(pending.user_content)
        self._spawn("teacher", self._work_respond, self.turn, self.session.system, messages)

    def _speak(self, text: str, speed: Optional[float] = None) -> None:
        if not text:
            self._wait_for_learner(listen=True)
            return
        speed = self.settings.speed_factor if speed is None else speed
        self.pending = Pending("speak", text=text, speed=speed)
        self.speaking_text = text
        if self.audio_in is not None:
            self.audio_in.disarm()
            self.audio_in.set_gate(False)
        deadline = 20.0 + len(text) / (8.0 * speed)
        self.turn += 1
        self.sm.go(SPEAKING, "teacher speaking", deadline=deadline)
        if self.text_only:
            self.post("spoken", self.turn, PlayResult(True))
            return
        self._spawn("voice", self._work_speak, self.turn, text, speed)

    def _replay(self) -> None:
        self.pending = Pending("speak", text=self.speaking_text, speed=self.settings.speed_factor)
        if self.audio_in is not None:
            self.audio_in.disarm()
            self.audio_in.set_gate(False)
        seconds = dsp.seconds_of(self.last_audio, self.last_audio_rate or dsp.TTS_RATE)
        self.turn += 1
        self.sm.go(SPEAKING, "repeat", deadline=seconds + 10.0)
        self._spawn("voice", self._work_replay, self.turn, self.last_audio,
                    self.last_audio_rate or dsp.TTS_RATE)

    def _interrupt_voice(self) -> None:
        if self.audio_out is not None:
            self.audio_out.stop()

    def _pause(self, reason: str = "paused") -> None:
        self._interrupt_voice()
        self._mic_off()
        self.turn += 1
        if self.sm.is_(PROCESSING_SPEECH, GENERATING_RESPONSE):
            self.pending = None
        self.sm.go(PAUSED, reason)

    def _resume(self) -> None:
        self.turn += 1
        self.sm.go(IDLE, "resume")
        self._wait_for_learner(listen=True, resume=True)

    def _summary_ready(self, raw) -> None:
        summary = parse_summary(raw) if not isinstance(raw, Summary) else raw
        self.summary = summary
        self._close_session(summary)
        self.finishing = True          # until the summary has been spoken or left
        self._speak(summary.spoken_summary)

    def _lesson_closed(self) -> None:
        self.finishing = False
        if not self.sm.is_(IDLE):
            self.sm.go(IDLE, "lesson closed")

    def _close_session(self, summary: Optional[Summary]) -> None:
        session = self.session
        if session is None:
            return
        self.session = None
        minutes = session.minutes
        self.profile.record_session(minutes, session.activity.mode, session.activity.id, summary,
                                    session.interview_questions, session.listening_results)
        self._save_profile()
        record = {"id": session.id, "activity": session.activity.id,
                  "mode": session.activity.mode, "minutes": round(minutes, 1),
                  "turns": session.turns,
                  "corrections": sum(len(e["corrections"]) for e in session.entries)}
        if summary is not None:
            record.update(main_improvement=summary.main_improvement, new_phrase=summary.new_phrase,
                          next_exercise=summary.next_exercise, went_well=summary.went_well)
        try:
            self.storage.save_lesson(session.id, record)
            if session.keep_transcript and session.transcript:
                self.storage.save_transcript(session.id, session.transcript)
        except OSError:
            log.exception("could not save the lesson record")
        lifecycle.info("lesson end: session=%s minutes=%.1f turns=%d", session.id, minutes,
                       session.turns)

    def _fail(self, stage: str, error: Exception) -> None:
        if self.sm.is_(SHUTTING_DOWN):
            return
        self._interrupt_voice()
        if self.audio_in is not None:
            self.audio_in.disarm()
            self.audio_in.set_gate(False)
        self.hearing = False
        self.holding = False
        if isinstance(error, ProviderError):
            title, remedy = error.explain()
            details = [error.detail] if error.detail else []
            kind = error.kind
        elif isinstance(error, SpeakerError):
            title, remedy = "Speaker problem", "Check the sound card, then Retry."
            details = [str(error)]
            kind = "speaker"
        elif isinstance(error, MicError):
            message = str(error) or "microphone problem"
            title = "Microphone busy" if "busy" in message else "Microphone problem"
            remedy = ("Close the other app using it, then Retry." if "busy" in message
                      else "Check the sound card (Settings > Diagnostics in mFruit OS), then Retry.")
            details = [message]
            kind = "microphone"
        else:
            title, remedy = "Something went wrong", "Retry. Your lesson is kept."
            details = [f"{error.__class__.__name__}: {error}"]
            kind = "internal"
        if self.pending is None or self.pending.stage != stage:
            if stage in ("listen", "setup", "internal", "init"):
                self.pending = Pending(stage)
        details.append(f"Step: {stage or 'unknown'}")
        if stage == "setup":
            details = [line for p in self.providers.problems for line in (p.title, p.remedy)] + details
        self.error = ErrorInfo(title, remedy, details, stage, kind)
        self.turn += 1
        log.warning("failed at %s: %s", stage, error)
        lifecycle.info("error: stage=%s kind=%s", stage, kind)
        if not self.sm.go(ERROR, f"{stage} failed"):
            self.sm.state = ERROR          # from any state: errors must always be reachable

    def _notify(self, text: str) -> None:
        self.notice = text
        self.notice_until = self.clock() + NOTICE_SECONDS

    def _save_profile(self) -> None:
        try:
            self.storage.save_profile(self.profile)
        except OSError:
            log.exception("could not save the learning profile")

    def _save_settings(self) -> None:
        try:
            self.settings.save()
        except OSError:
            log.exception("could not save settings")

    # ============================================================= workers
    # These run on worker threads: they only call providers and post results.
    def _work_transcribe(self, turn: int, pcm: bytes, context: str) -> None:
        try:
            result = self.providers.asr.transcribe(pcm, dsp.SPEECH_RATE, context)
        except Exception as exc:
            result = exc if isinstance(exc, ProviderError) else ProviderError(
                "bad_response", "speech recognition", f"{exc.__class__.__name__}: {exc}")
        self.post("transcribed", turn, result)

    def _work_respond(self, turn: int, system: str, messages: list) -> None:
        try:
            raw = self.providers.llm.complete_json(system, messages, TEACHER_SCHEMA, "teacher_turn")
            result = parse_turn(raw, self.settings.correction_limit)
            if result.problems:
                log.info("teacher answer adjusted: %s", "; ".join(result.problems))
        except Exception as exc:
            result = exc if isinstance(exc, ProviderError) else ProviderError(
                "bad_response", "teacher", f"{exc.__class__.__name__}: {exc}")
        self.post("replied", turn, result)

    def _work_summary(self, turn: int, record: str) -> None:
        try:
            raw = self.providers.llm.complete_json(
                prompts.SUMMARY_SYSTEM, [{"role": "user", "content": record}], SUMMARY_SCHEMA,
                "lesson_summary")
            result = parse_summary(raw)
        except Exception as exc:
            result = exc if isinstance(exc, ProviderError) else ProviderError(
                "bad_response", "teacher", f"{exc.__class__.__name__}: {exc}")
        self.post("replied", turn, result)

    def _work_speak(self, turn: int, text: str, speed: float) -> None:
        tts = self.providers.tts
        try:
            if tts is None or self.audio_out is None:
                raise ProviderError(UNAVAILABLE, "Voice", "no voice or speaker")
            instructions = ""
            stream = tts.stream(text, self.settings["voice"], speed, instructions,
                                cancelled=lambda: turn != self.turn)
            result = self.audio_out.play(stream, tts.rate, keep=True)
        except Exception as exc:
            result = exc if isinstance(exc, ProviderError) else ProviderError(
                "network", "Voice", f"{exc.__class__.__name__}: {exc}")
        self.post("spoken", turn, result)

    def _work_replay(self, turn: int, audio: bytes, rate: int) -> None:
        result = self.audio_out.play_pcm(audio, rate)
        result = replace(result, audio=audio)
        self.post("spoken", turn, result)

    def _work_cue(self, turn: int) -> None:
        self.audio_out.cue("listen")
        self.post("cue_done", turn)

    def _on_cue_done(self, turn: int) -> None:
        if turn == self.turn:
            self._after(ECHO_GUARD, "open_gate")

    # ================================================================ view
    def _state_changed(self, previous: str, state: str, reason: str) -> None:
        if state not in (LISTENING, PRACTICE_WAITING):
            self.hearing = False

    def _publish(self) -> None:
        session = self.session
        mic_on = bool(self.audio_in is not None and self.audio_in.running)
        self.view = View(
            state=self.sm.state,
            in_lesson=session is not None,
            activity_id=session.activity.id if session else "",
            page=modes.page_name(session.activity) if session else "",
            listening_mode=self.settings["listening_mode"],
            mic_on=mic_on,
            hearing=self.hearing or self.holding,
            holding=self.holding,
            last_transcript=self.last_transcript,
            turn=session.last_turn if session else None,
            speaking_text=self.speaking_text,
            practice_target=session.practice_target if session else "",
            practice_result=self.practice_result,
            error=self.error,
            notice=self.notice,
            summary=self.summary,
            finishing=self.finishing,
            can_teach=self.can_teach,
            can_speak=self.providers.can_speak,
            problems=tuple(self.providers.problems),
            remote=tuple(self.providers.remote_names()),
            lesson_started=session.started if session else 0.0,
            lesson_minutes=self.settings["session_minutes"],
        )
        for callback in list(self.listeners):
            try:
                callback()
            except Exception:
                log.exception("view listener failed")
