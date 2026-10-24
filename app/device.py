"""The Whisplay LCD, the button and keyboards: mLearning's device interface.

mFruit OS launches mLearning (ApplicationManager -> whisplay-daemon ->
mfruit-run -> run.sh). This module then follows the app contract: it
claims Esc, takes focus and the framebuffer, draws its first frame at
once, reads the button and keys only through the SDK's InputController,
stops drawing when focus is revoked, and exits on ``app_exit_requested``
or Back from the first screen.

Screens and their controls come from tables in this file: each lesson
state has one table of (gesture, label, command) that both draws the
footer and decides what a gesture does, so the screen never advertises a
gesture that does nothing. Lists use the standard mFruit list controls
(tap next, 2x previous, hold then release to open, 4x back).

Threads: the button, keyboard and daemon call in on their own threads;
handlers change navigation state under a lock and post requests to the
engine. One render thread draws, only when something changed.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

from mfruit_sdk.daemon import own_escape_key
from mfruit_sdk.input import (BACK, CHAR, ERASE, EXTRA, NEXT, PREVIOUS, SELECT, TALK_END,
                              TALK_START, InputController)
from mfruit_sdk.status import StatusMonitor
from mfruit_sdk.ui import Row

from . import config, paths, whisplay
from . import state_machine as sm
from .learning.profile import CATEGORY_NAMES, today
from .teacher import modes
from .ui import screens
from .whisplay import WhisplayApp

log = logging.getLogger("mlearning.device")

NOTE_SECONDS = 2.2
LIST_HINTS = [("tap", "next"), ("hold", "open"), ("4×", "back"), ("2×", "previous")]
HOME_HINTS = [("tap", "next"), ("hold", "open"), ("4×", "exit"), ("2×", "previous")]
SETTINGS_HINTS = [("tap", "next"), ("hold", "change"), ("4×", "back"), ("3×", "reverse")]
READER_HINTS = [("tap", "scroll"), ("2×", "up"), ("4×", "back"), ("hold", "back")]
SUMMARY_HINTS = [("hold", "done"), ("tap", "scroll"), ("4×", "done")]
WAIT_HINTS = [("4×", "back")]
TYPING_HINTS = [("Enter", "send"), ("Esc", "cancel")]

# Lesson controls by state: (gesture, footer label, command). The first
# entries are shown first; the footer drops what does not fit.
LESSON_HINTS = {
    "listening": [("hold", "talk", "talk"), ("tap", "pause", "pause"), ("3×", "menu", "menu"),
                  ("4×", "end", "end"), ("2×", "repeat", "repeat")],
    "holding": [("release", "send", None)],
    "speaking": [("hold", "interrupt", "talk"), ("tap", "pause", "pause"),
                 ("2×", "repeat", "repeat"), ("4×", "end", "end"), ("3×", "menu", "menu")],
    "waiting": [("hold", "talk", "talk"), ("2×", "repeat", "repeat"), ("3×", "menu", "menu"),
                ("4×", "end", "end"), ("tap", "pause", "pause")],
    "mic_off": [("tap", "listen", "pause"), ("hold", "talk", "talk"), ("3×", "menu", "menu"),
                ("4×", "end", "end"), ("2×", "repeat", "repeat")],
    "paused": [("tap", "resume", "pause"), ("hold", "talk", "talk"), ("3×", "menu", "menu"),
               ("4×", "end", "end")],
    "busy": [("3×", "menu", "menu"), ("4×", "end", "end")],
}
GESTURE_OF = {NEXT: "tap", PREVIOUS: "2×", EXTRA: "3×", BACK: "4×", TALK_START: "hold",
              SELECT: "3×"}          # Enter on a talk screen opens what 3x opens

HOME_ITEMS = (
    ("talk", "Talk with your teacher", "Conversation, gentle corrections", True),
    ("daily", "Daily English", "Café, shop, work and more", True),
    ("interview", "Job interview", "Practise with an interviewer", True),
    ("listening", "Listening practice", "Retell, repeat, real-world", True),
    ("ask", "Ask about English", "Words, phrases and grammar", True),
    ("phrases", "My phrases", "", False),
    ("progress", "Progress", "", False),
    ("settings", "Settings", "", False),
)

JOB_PRESETS = ("", "barista", "retail assistant", "hospitality worker", "customer service officer",
               "office administrator", "warehouse worker", "nurse", "teacher", "software developer")
INTERVIEW_KEYS = ("interview_type", "interview_difficulty", "interview_feedback")
PROBLEM_CHOICES = ("Retry", "Details", "Back")
MENU_ROWS = ("repeat", "slower", "faster", "notes", "listening_mode", "pause", "end", "back")


def _count(number: int, word: str) -> str:
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


class DeviceUI:
    def __init__(self, engine, board: Optional[WhisplayApp] = None, keyboard: bool = True,
                 threaded: bool = True, clock=time.monotonic, status: Optional[StatusMonitor] = None):
        self.engine = engine
        self.settings = engine.settings
        self.board = board or WhisplayApp(paths.APP_ID)
        self.clock = clock
        self.threaded = threaded
        self.lock = threading.RLock()
        self.screen = "home"
        self.index = 0
        self.stack: list = []
        self.armed = False
        self.note = ""
        self.note_until = 0.0
        self.typing: Optional[str] = None
        self.typing_for = ""                # "lesson" | "job"
        self.first = 0                      # reader scroll position
        self.reader_total = 0
        self.reader_shown = 0
        self.reader_title = ""
        self.reader_blocks: list = []
        self.phrase: Optional[dict] = None
        self.lesson_origin: tuple = ("home", 0)
        self.setup_retry = False
        self.error_index = 0                # the error overlay's own selection
        self.under_error = "home"           # the screen beneath the error details
        self.levels: deque = deque(maxlen=17)
        self.phase = 0
        self.frames = 0
        self.last_frame = None
        self._last_bytes = b""
        self.running = False
        self.exit_requested = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.status = status or StatusMonitor(on_change=lambda _s: self.wake())
        self.input = InputController(self._on_action, talk=self._talk_screen,
                                     typing=lambda: self.typing is not None,
                                     active=lambda: self.board.has_focus, on_armed=self._on_armed,
                                     app_id=self.board.app_id, keyboard=keyboard, threaded=threaded,
                                     clock=clock, talk_press_ms=350)
        engine.add_listener(self.wake)

    # ============================================================ lifecycle
    def start(self) -> bool:
        board = self.board
        board.on_press = self.input.press
        board.on_release = self.input.release
        board.on_exit_request = self.request_exit
        board.on_focus_changed = self._focus_changed
        try:
            # Before taking the screen: a registration makes the daemon redraw its desktop.
            own_escape_key(board.app_id, whisplay.SOCKET_PATH)
        except Exception as exc:
            log.info("could not claim Esc: %s", exc)
        try:
            board.start()
        except (OSError, RuntimeError) as exc:
            log.warning("whisplay-daemon unavailable: %s", exc)
            return False
        self.running = True
        self.draw()                         # first frame at once
        self.status.start()
        self.input.start()
        if self.threaded:
            self._thread = threading.Thread(target=self._render_loop, name="render", daemon=True)
            self._thread.start()
        log.info("on the Whisplay LCD")
        return True

    def stop(self) -> None:
        self.running = False
        self._wake.set()
        self.input.stop()
        self.status.stop()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.board.stop()

    def request_exit(self) -> None:
        self.exit_requested.set()

    def wake(self) -> None:
        self._wake.set()

    def _focus_changed(self, has_focus: bool) -> None:
        self.input.reset()                  # keys pressed elsewhere are not ours
        with self.lock:
            self.armed = False
        self.engine.focus(has_focus)
        self.wake()

    def _render_loop(self) -> None:
        while self.running:
            self._wake.wait(timeout=self._interval())
            self._wake.clear()
            if not self.running:
                return
            try:
                self.draw()
            except Exception:           # isolation boundary: drawing must not kill the app
                log.exception("drawing failed")

    def _interval(self) -> float:
        view = self.engine.view
        if self.effective_screen() in ("lesson", "summary") and view.state in (
                sm.LISTENING, sm.PRACTICE_WAITING, sm.SPEAKING, sm.PROCESSING_SPEECH,
                sm.GENERATING_RESPONSE):
            return 0.12
        return 0.5 if self.note else 1.0

    # ============================================================== screens
    def effective_screen(self) -> str:
        """The screen to show: errors and lessons take precedence over navigation."""
        view = self.engine.view
        if view.state == sm.ERROR:
            return "error_details" if self.screen == "error_details" else "error"
        self.error_index = 0
        if self.screen == "error_details":
            self.screen = self.under_error
        if view.summary is not None or view.finishing:
            return "summary"
        if view.in_lesson:
            return self.screen if self.screen in ("lesson_menu", "notes") else "lesson"
        if self.screen in ("lesson", "lesson_menu", "notes", "summary"):
            self.screen, self.index = self.lesson_origin
        if self.screen == "starting" and view.state != sm.INITIALISING:
            self.screen = "home"
        return self.screen

    def _talk_screen(self) -> bool:
        with self.lock:
            return self.effective_screen() == "lesson"

    def lesson_key(self) -> str:
        view = self.engine.view
        if view.holding:
            return "holding"
        state = view.state
        hands_free = view.listening_mode == "hands_free"
        if state == sm.SPEAKING:
            return "speaking"
        if state in (sm.LISTENING, sm.PRACTICE_WAITING) and hands_free:
            return "listening"
        if state in (sm.PROCESSING_SPEECH, sm.GENERATING_RESPONSE):
            return "busy"
        if state == sm.PAUSED:
            return "paused"
        if state == sm.IDLE and hands_free:
            return "mic_off"
        return "waiting"

    def lesson_hints(self) -> list:
        if self.typing is not None:
            return [(g, label, None) for g, label in TYPING_HINTS]
        return LESSON_HINTS[self.lesson_key()]

    def _open(self, screen: str, index: int = 0, push: bool = True) -> None:
        if push and screen != self.screen:
            self.stack.append((self.screen, self.index))
        self.screen = screen
        self.index = index
        self.first = 0
        self.armed = False

    def _back(self) -> None:
        if self.stack:
            self.screen, self.index = self.stack.pop()
        else:
            self.screen, self.index = "home", 0
        self.first = 0
        self.armed = False

    def notify(self, text: str) -> None:
        self.note = text
        self.note_until = self.clock() + NOTE_SECONDS

    # =============================================================== rows
    def home_rows(self) -> list:
        view = self.engine.view
        profile = self.engine.profile
        rows = []
        for key, label, subtitle, needs_ai in HOME_ITEMS:
            tone = ""
            if needs_ai and not view.can_teach:
                subtitle, tone = "Needs setup", "warning"
            elif key == "phrases":
                count = len(profile.phrases)
                subtitle = f"{count} saved" if count else "Phrases you learn appear here"
            elif key == "progress":
                minutes = profile.minutes_on(today())
                streak = profile.streak()
                subtitle = f"Today {minutes:.0f} of {self.settings['daily_goal_minutes']} min"
                if streak > 1:
                    subtitle += f" · {streak}-day streak"
            kind = "nav" if key in ("daily", "interview", "listening", "phrases", "progress", "settings") else "action"
            rows.append(Row(label, subtitle=subtitle or None, kind=kind, tone=tone))
        return rows

    def activity_rows(self, activities) -> list:
        return [Row(a.title, subtitle=a.subtitle, kind="action") for a in activities] + [Row("Back", kind="back")]

    def interview_rows(self) -> list:
        job = self.settings["interview_job"]
        rows = [Row("Start interview", subtitle="One question at a time", kind="action"),
                Row("Job", value=(job.title() if job else "Ask me"), kind="action")]
        for key in INTERVIEW_KEYS:
            rows.append(Row(config.SETTINGS[key].label, value=self.settings.text(key), kind="action"))
        return rows + [Row("Back", kind="back")]

    def settings_rows(self) -> list:
        rows = []
        for key in config.MENU_KEYS:
            setting = config.SETTINGS[key]
            rows.append(Row(setting.label, value=self.settings.text(key), kind="action"))
        rows.append(Row("Privacy and data", kind="nav"))
        rows.append(Row("Teacher setup", subtitle="Keys, providers, devices", kind="nav"))
        return rows + [Row("Back", kind="back")]

    def phrase_rows(self) -> list:
        phrases = list(reversed(self.engine.profile.phrases))
        rows = [Row(p["phrase"], subtitle=p.get("meaning") or None, kind="nav") for p in phrases]
        return rows + [Row("Back", kind="back")]

    def menu_rows(self) -> list:
        view = self.engine.view
        speed = self.settings["speech_speed"]
        paused = view.state == sm.PAUSED
        labels = {
            "repeat": Row("Repeat that", kind="action"),
            "slower": Row("Speak slower", kind="action", enabled=speed != "slow"),
            "faster": Row("Speak faster", kind="action", enabled=speed != "fast"),
            "notes": Row("Show notes", subtitle="Corrections and new phrases", kind="nav"),
            "listening_mode": Row("Listening", value=self.settings.text("listening_mode"), kind="action"),
            "pause": Row("Resume" if paused else "Pause", kind="action"),
            "end": Row("End lesson", subtitle="With a short summary", kind="danger"),
            "back": Row("Back", kind="back"),
        }
        return [labels[key] for key in MENU_ROWS]

    def privacy_rows(self) -> list:
        return [Row("Where my voice goes", kind="nav"), Row("What is saved", kind="nav"),
                Row("Delete learning history", kind="danger"), Row("Back", kind="back")]

    # =============================================================== input
    def _on_armed(self, armed: bool) -> None:
        with self.lock:
            self.armed = armed
        self.wake()

    def _on_action(self, action) -> None:
        with self.lock:
            try:
                self._dispatch(action)
            except Exception:          # isolation boundary: one bad press must not kill input
                log.exception("action %s failed", action.name)
        self.wake()

    def _dispatch(self, action) -> None:
        name = action.name
        screen = self.effective_screen()
        if self.typing is not None:
            self._typing_action(action)
            return
        if name == CHAR:
            if screen == "lesson":
                self.typing, self.typing_for = action.char, "lesson"
            elif screen == "interview" and self.index == 1:
                self.typing, self.typing_for = action.char, "job"
            return
        if name in (ERASE,):
            return
        handler = getattr(self, f"_on_{screen}", None)
        if handler is not None:
            handler(name, action)

    def _typing_action(self, action) -> None:
        name = action.name
        if name == CHAR:
            self.typing = (self.typing + action.char)[:300]
        elif name == ERASE:
            self.typing = self.typing[:-1]
        elif name == SELECT:
            text = self.typing.strip()
            target = self.typing_for
            self.typing = None
            if not text:
                return
            if target == "lesson":
                self.engine.typed(text)
            elif target == "job":
                self.engine.set_setting("interview_job", text[:60])
                self.notify("Job saved")
        elif name == BACK:
            self.typing = None

    # -- lists ----------------------------------------------------------
    def _list_move(self, name: str, count: int) -> bool:
        if name == NEXT:
            self.index = (self.index + 1) % count
            return True
        if name == PREVIOUS:
            self.index = (self.index - 1) % count
            return True
        return False

    def _on_home(self, name: str, action) -> None:
        if self._list_move(name, len(HOME_ITEMS)):
            return
        if name == BACK:
            self.notify("Exiting")
            self.request_exit()
            return
        if name != SELECT:
            return
        key, _label, _sub, needs_ai = HOME_ITEMS[self.index]
        if needs_ai and not self.engine.view.can_teach:
            self._open("setup")
            return
        if key in ("talk", "ask"):
            self._start_lesson(key)
        elif key in ("daily", "listening", "interview", "phrases", "progress", "settings"):
            self._open(key)

    def _on_daily(self, name: str, action) -> None:
        self._activities(name, modes.DAILY)

    def _on_listening(self, name: str, action) -> None:
        self._activities(name, modes.LISTENING)

    def _activities(self, name: str, activities) -> None:
        count = len(activities) + 1
        if self._list_move(name, count):
            return
        if name == BACK or (name == SELECT and self.index == len(activities)):
            self._back()
        elif name == SELECT:
            self._start_lesson(activities[self.index].id)

    def _on_interview(self, name: str, action) -> None:
        count = 2 + len(INTERVIEW_KEYS) + 1
        if self._list_move(name, count):
            return
        if name == BACK or (name == SELECT and self.index == count - 1):
            self._back()
            return
        if name not in (SELECT, EXTRA):
            return
        direction = -1 if name == EXTRA else 1
        if self.index == 0 and name == SELECT:
            self._start_lesson("interview")
        elif self.index == 1:
            job = self.settings["interview_job"]
            position = JOB_PRESETS.index(job) if job in JOB_PRESETS else 0
            self.engine.set_setting("interview_job", JOB_PRESETS[(position + direction) % len(JOB_PRESETS)])
        elif 2 <= self.index < 2 + len(INTERVIEW_KEYS):
            key = INTERVIEW_KEYS[self.index - 2]
            self.engine.set_setting(key, self.settings.peek(key, direction))

    def _on_settings(self, name: str, action) -> None:
        keys = config.MENU_KEYS
        count = len(keys) + 3
        if self._list_move(name, count):
            return
        if name == BACK or (name == SELECT and self.index == count - 1):
            self._back()
            return
        if name not in (SELECT, EXTRA):
            return
        if self.index < len(keys):
            key = keys[self.index]
            value = self.settings.peek(key, -1 if name == EXTRA else 1)
            self.engine.set_setting(key, value)
        elif self.index == len(keys) and name == SELECT:
            self._open("privacy")
        elif self.index == len(keys) + 1 and name == SELECT:
            self._open("setup")

    def _on_privacy(self, name: str, action) -> None:
        if self._list_move(name, 4):
            return
        if name == BACK or (name == SELECT and self.index == 3):
            self._back()
        elif name == SELECT and self.index == 0:
            self._read("Your voice", self.voice_blocks())
        elif name == SELECT and self.index == 1:
            self._read("What is saved", self.saved_blocks())
        elif name == SELECT and self.index == 2:
            if self.engine.view.in_lesson:
                self.notify("Finish the lesson first")
            else:
                self._open("confirm_delete")

    def _on_confirm_delete(self, name: str, action) -> None:
        if name in (NEXT, PREVIOUS):
            self.index = 1 - self.index
        elif name == BACK:
            self._back()
        elif name == SELECT:
            if self.index == 0:
                self.engine.delete_history()
            self._back()

    def _on_setup(self, name: str, action) -> None:
        if self._list_move(name, len(PROBLEM_CHOICES)):
            return
        choice = PROBLEM_CHOICES[self.index] if name == SELECT else ("Back" if name == BACK else "")
        if choice == "Retry":
            self.setup_retry = True
            self.engine.reload()
            self.notify("Checking…")
        elif choice == "Details":
            self._read("Setup details", self.setup_blocks())
        elif choice == "Back":
            self._back()

    def _on_phrases(self, name: str, action) -> None:
        phrases = list(reversed(self.engine.profile.phrases))
        count = len(phrases) + 1
        if self._list_move(name, count):
            return
        if name == BACK or (name == SELECT and self.index == len(phrases)):
            self._back()
        elif name == SELECT:
            self.phrase = phrases[self.index]
            self._open("phrase")

    def _on_phrase(self, name: str, action) -> None:
        if self._list_move(name, 3):
            return
        if name == BACK or (name == SELECT and self.index == 2):
            if self.engine.view.state == sm.SPEAKING:
                self.engine.stop_speaking()
            self._back()
        elif name == SELECT and self.index == 0:
            if self.engine.view.can_speak:
                self.engine.say(self.phrase["phrase"])
            else:
                self.notify("The voice is not set up")
        elif name == SELECT and self.index == 1:
            self.engine.remove_phrase(self.phrase["phrase"])
            self._back()

    def _on_progress(self, name: str, action) -> None:
        self._on_reader(name, action)

    def _read(self, title: str, blocks: list) -> None:
        self.reader_title, self.reader_blocks = title, blocks
        self._open("reader")

    def _on_reader(self, name: str, action) -> None:
        if name == NEXT:
            if self.first + self.reader_shown < self.reader_total:
                self.first += max(1, self.reader_shown - 1)
        elif name == PREVIOUS:
            self.first = max(0, self.first - max(1, self.reader_shown - 1))
        elif name in (BACK, SELECT):
            self._back()

    # -- lesson ---------------------------------------------------------
    def _start_lesson(self, activity_id: str) -> None:
        self.lesson_origin = (self.screen, self.index)
        self.levels.clear()
        self.engine.start_lesson(activity_id)
        self.screen = "lesson"
        self.index = 0
        self.stack = []

    def _on_lesson(self, name: str, action) -> None:
        if name == TALK_END:
            self.engine.talk_end()
            return
        gesture = GESTURE_OF.get(name)
        command = next((cmd for g, _label, cmd in self.lesson_hints() if g == gesture), None)
        if command is None:
            return
        if command == "talk":
            self.engine.talk_start()
        elif command == "pause":
            self.engine.toggle_pause()
        elif command == "repeat":
            self.engine.repeat()
        elif command == "menu":
            self.screen, self.index = "lesson_menu", 0
        elif command == "end":
            self.engine.end_lesson()

    def _on_lesson_menu(self, name: str, action) -> None:
        if self._list_move(name, len(MENU_ROWS)):
            return
        if name == BACK:
            self.screen = "lesson"
            return
        if name != SELECT:
            return
        row = MENU_ROWS[self.index]
        if row == "notes":
            view = self.engine.view
            self.reader_title = "Notes"
            self.reader_blocks = screens.turn_blocks(view.turn, view.last_transcript)
            self.screen, self.first = "notes", 0
            return
        if row == "listening_mode":
            self.engine.set_setting("listening_mode", self.settings.peek("listening_mode"))
            return
        self.screen = "lesson"
        if row == "repeat":
            self.engine.repeat()
        elif row == "slower":
            self.engine.slower()
        elif row == "faster":
            self.engine.faster()
        elif row == "pause":
            self.engine.toggle_pause()
        elif row == "end":
            self.engine.end_lesson()

    def _on_notes(self, name: str, action) -> None:
        if name == NEXT:
            if self.first + self.reader_shown < self.reader_total:
                self.first += max(1, self.reader_shown - 1)
        elif name == PREVIOUS:
            self.first = max(0, self.first - max(1, self.reader_shown - 1))
        elif name in (BACK, SELECT):
            self.screen = "lesson_menu"

    def _on_summary(self, name: str, action) -> None:
        view = self.engine.view
        if view.summary is None:
            if name == BACK:            # still writing: leave without waiting
                self.engine.leave_summary()
            return
        if name == NEXT:
            if self.first + self.reader_shown < self.reader_total:
                self.first += max(1, self.reader_shown - 1)
        elif name == PREVIOUS:
            self.first = max(0, self.first - max(1, self.reader_shown - 1))
        elif name in (SELECT, BACK):
            self.engine.leave_summary()
            self.screen, self.index = self.lesson_origin
            self.first = 0

    def _on_error(self, name: str, action) -> None:
        if name == NEXT:
            self.error_index = (self.error_index + 1) % len(PROBLEM_CHOICES)
            return
        if name == PREVIOUS:
            self.error_index = (self.error_index - 1) % len(PROBLEM_CHOICES)
            return
        choice = PROBLEM_CHOICES[self.error_index] if name == SELECT else ("Back" if name == BACK else "")
        if choice == "Retry":
            self.engine.retry()
            self.error_index = 0
        elif choice == "Details":
            error = self.engine.view.error
            self.reader_title = "Details"
            self.reader_blocks = ([(error.title, "title"), (error.remedy, "body"), ("", "body")] +
                                  [(line, "muted") for line in error.details]) if error else []
            self.under_error = self.screen
            self.screen, self.first = "error_details", 0
        elif choice == "Back":
            self.engine.dismiss_error()
            self.error_index = 0

    def _on_error_details(self, name: str, action) -> None:
        if name == NEXT:
            if self.first + self.reader_shown < self.reader_total:
                self.first += max(1, self.reader_shown - 1)
        elif name == PREVIOUS:
            self.first = max(0, self.first - max(1, self.reader_shown - 1))
        elif name in (BACK, SELECT):
            self.screen = self.under_error       # the error shows again while it lasts

    # ============================================================ content
    def voice_blocks(self) -> list:
        view = self.engine.view
        blocks = [("Listening happens on this device.", "body"),
                  ("Nothing is sent while nobody speaks. When you finish a sentence, that "
                   "sentence is sent for recognition.", "muted"), ("", "body")]
        if view.remote:
            blocks.append(("Sent online to:", "label"))
            blocks += [(name, "body") for name in view.remote]
            blocks.append(("The teacher receives the text of what you said, not the "
                           "recording.", "muted"))
        else:
            blocks.append(("No online service is set up.", "muted"))
        blocks += [("", "body"), ("Recordings are never saved on the device.", "good"),
                   ("Tap during a lesson to turn the microphone off.", "muted")]
        return blocks

    def saved_blocks(self) -> list:
        profile = self.engine.profile
        size = self.engine.storage.size_summary() or "nothing yet"
        keep = self.settings["keep_transcripts"] != "off"
        return [
            ("Saved on this device only:", "label"),
            ("• lesson minutes and summaries", "body"),
            ("• your phrases and recurring mistakes (short examples)", "body"),
            ("• your settings", "body"),
            ("• conversation transcripts for 30 days" if keep else
             "• no conversation transcripts (Settings > Keep transcripts)", "body"),
            ("", "body"),
            (f"{_count(profile.sessions, 'lesson')}, {_count(len(profile.phrases), 'phrase')}, {size}.", "muted"),
            (f"Folder: {self.engine.storage.root}", "muted"),
            ("", "body"),
            ("Delete learning history removes all of it.", "warn"),
        ]

    def setup_blocks(self) -> list:
        view = self.engine.view
        blocks = []
        for problem in view.problems:
            blocks += [(problem.title, "warn"), (problem.remedy, "body"), ("", "body")]
        blocks += [(line, "muted") for line in self.engine.providers.summary()]
        blocks += [("", "body"), ("Keys file", "label"), (self.engine.secrets.path, "muted"),
                   ("Microphone", "label"),
                   (getattr(self.engine.audio_in, "device", None) or "not found", "muted"),
                   ("Speaker", "label"),
                   (getattr(self.engine.audio_out, "device", None) or "not found", "muted")]
        return blocks

    def progress_stats(self) -> dict:
        profile = self.engine.profile
        level = profile.working_level()
        blocks = [
            (f"{_count(profile.sessions, 'lesson')} · {_count(round(profile.minutes_total), 'minute')} in all", "body"),
            ((f"{profile.streak()}-day streak · " if profile.streak() else "") +
             _count(len(profile.phrases), 'phrase'), "muted"),
            ("", "body"),
            ("Level", "label"),
            (f"Around CEFR {level}" if level else "Not enough lessons to judge yet", "body"),
        ]
        mistakes = profile.top_mistakes(3)
        if mistakes:
            blocks += [("", "body"), ("Working on", "label")]
            for category, entry in mistakes:
                blocks.append((f"{CATEGORY_NAMES.get(category, category).capitalize()} ({entry['count']})", "body"))
        last = profile.last_summary
        if last.get("main_improvement"):
            blocks += [("", "body"), ("Last lesson", "label"), (last["main_improvement"], "good")]
        if last.get("next_exercise"):
            blocks += [("Next", "label"), (last["next_exercise"], "body")]
        if profile.interview.get("sessions"):
            blocks += [("", "body"), (f"Interview practice: {profile.interview['sessions']} sessions, "
                                      f"{profile.interview['questions']} questions", "muted")]
        return {"today": profile.minutes_on(today()), "goal": self.settings["daily_goal_minutes"],
                "blocks": blocks}

    # ============================================================== drawing
    def render(self):
        status = self.status.sample()
        now = self.clock()
        if self.note and now >= self.note_until:
            self.note = ""
        view = self.engine.view
        if self.setup_retry and view.state != sm.INITIALISING:
            self.setup_retry = False
            if view.can_teach:
                self.notify("Teacher ready")
                if self.screen == "setup":
                    self._back()
            else:
                self.notify("Still not set up")
        note = self.note or view.notice
        screen = self.effective_screen()
        armed = self.armed
        if view.state == sm.INITIALISING:
            return screens.starting(status)
        if screen == "home":
            return screens.list_screen("mLearning", self.home_rows(), self.index, HOME_HINTS,
                                       status, armed, note=note)
        if screen == "daily":
            return screens.list_screen("Daily English", self.activity_rows(modes.DAILY), self.index,
                                       LIST_HINTS, status, armed, "to start", note)
        if screen == "listening":
            return screens.list_screen("Listening", self.activity_rows(modes.LISTENING), self.index,
                                       LIST_HINTS, status, armed, "to start", note)
        if screen == "interview":
            image = screens.list_screen("Job interview", self.interview_rows(), self.index,
                                        SETTINGS_HINTS, status, armed, "to choose", note)
            if self.typing is not None and self.typing_for == "job":
                from mfruit_sdk.ui import Canvas, text_field
                canvas = Canvas(image=image)
                text_field(canvas, self.typing, 200, "Job title, then Enter")
            return image
        if screen == "settings":
            return screens.list_screen("Settings", self.settings_rows(), self.index, SETTINGS_HINTS,
                                       status, armed, "to change", note)
        if screen == "privacy":
            return screens.list_screen("Privacy and data", self.privacy_rows(), self.index,
                                       LIST_HINTS, status, armed, note=note)
        if screen == "confirm_delete":
            return screens.problem("Privacy and data", "Delete history?",
                                   "Lessons, phrases and progress are removed. Settings and keys stay.",
                                   ["Delete", "Cancel"], self.index, LIST_HINTS, status, armed)
        if screen == "setup":
            if view.can_teach:
                title, body = "Teacher ready", " · ".join(view.remote) or "Set up"
            else:
                problem = view.problems[0] if view.problems else None
                title = "Set up your teacher"
                body = (f"{problem.title}. Put the key in mLearning's .env file (Details shows "
                        "where), then Retry.") if problem and "key" in problem.title.lower() else \
                    (problem.remedy if problem else "See Details.")
            return screens.problem("Setup", title, body, list(PROBLEM_CHOICES), self.index,
                                   LIST_HINTS, status, armed)
        if screen == "phrases":
            empty = not self.engine.profile.phrases
            return screens.list_screen("My phrases", self.phrase_rows(), self.index, LIST_HINTS,
                                       status, armed, note=note,
                                       header="Phrases from lessons appear here." if empty else None)
        if screen == "phrase" and self.phrase is not None:
            rows = [Row("Hear it", kind="action", enabled=view.can_speak),
                    Row("Remove", kind="danger"), Row("Back", kind="back")]
            return screens.phrase_detail(self.phrase, rows, self.index, LIST_HINTS, status, armed, note)
        if screen == "progress":
            image, self.reader_total, self.reader_shown = screens.progress(
                self.progress_stats(), self.first, READER_HINTS, status)
            return image
        if screen in ("reader", "notes", "error_details"):
            image, self.reader_total, self.reader_shown = screens.reader(
                self.reader_title, self.reader_blocks, self.first, READER_HINTS, status, note)
            return image
        if screen == "summary":
            if view.summary is None:
                return screens.list_screen("Lesson summary", [], 0, WAIT_HINTS, status,
                                           note=note or "Writing your summary…")
            image, self.reader_total, self.reader_shown = screens.reader(
                "Lesson summary", screens.summary_blocks(view.summary), self.first, SUMMARY_HINTS,
                status, note)
            return image
        if screen == "lesson_menu":
            return screens.list_screen(view.page or "Lesson", self.menu_rows(), self.index,
                                       LIST_HINTS, status, armed, "to choose", note)
        if screen == "error":
            error = view.error
            page = view.page or "mLearning"
            return screens.problem(page, error.title if error else "Problem",
                                   error.remedy if error else "", list(PROBLEM_CHOICES),
                                   self.error_index, LIST_HINTS, status, armed)
        # the lesson
        self.phase += 1
        if view.state in (sm.LISTENING, sm.PRACTICE_WAITING):
            self.levels.append(self.engine.mic_level())
        heard, received = self.engine.speak_progress()
        estimate = max(received, len(view.speaking_text) / 14.0 / max(0.5, self.settings.speed_factor))
        progress = heard / estimate if estimate else 0.0
        mic_off = not view.mic_on
        return screens.lesson(view, self.lesson_hints(), status, list(self.levels),
                              self.engine.noise_floor(), progress, self.phase, mic_off,
                              self.typing if self.typing_for == "lesson" else None, note)

    def draw(self) -> None:
        if not self.board.has_focus:
            return                          # never draw without the screen
        with self.lock:
            image = self.render()
        data = image.tobytes()
        if data == self._last_bytes:
            return
        self._last_bytes = data
        self.board.show(image)
        self.last_frame = image
        self.frames += 1
