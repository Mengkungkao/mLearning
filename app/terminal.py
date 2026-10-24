"""A lesson in the terminal: the same engine and teacher, typed in and printed out.

For trying the teacher over SSH or on a desktop, and for checking a key
works. ``--voice`` adds the microphone and speaker (the device's audio
path) with the terminal as the screen.
"""

from __future__ import annotations

import sys
import threading

from . import state_machine as sm
from .main import build_engine
from .teacher import modes

HELP = """Type what you want to say and press Enter.
  /repeat  /slower  /faster  /pause  /notes  /end (summary, then quit)  /quit  /help"""

WAITING = (sm.IDLE, sm.LISTENING, sm.PRACTICE_WAITING, sm.PAUSED, sm.ERROR)


class TerminalView:
    def __init__(self, engine, out=sys.stdout):
        self.engine = engine
        self.out = out
        self.shown_turn = None
        self.shown_error = None
        self.shown_summary = None
        self.state = None
        self.ready = threading.Event()
        self.busy_seen = False
        engine.add_listener(self.changed)

    def arm(self) -> None:
        """Wait for the next answer: ready once the engine was busy and is waiting again."""
        self.busy_seen = False
        self.ready.clear()

    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def changed(self) -> None:
        view = self.engine.view
        if view.turn is not None and view.turn is not self.shown_turn:
            self.shown_turn = view.turn
            self.print_turn(view.turn)
        if view.error is not None and view.error is not self.shown_error:
            self.shown_error = view.error
            self.say(f"\n[!] {view.error.title}: {view.error.remedy}")
            for line in view.error.details:
                self.say(f"    {line}")
            self.say("    (/retry to try again, /back to continue)")
        if view.summary is not None and view.summary is not self.shown_summary:
            self.shown_summary = view.summary
            self.print_summary(view.summary)
        if view.state != self.state:
            self.state = view.state
            if self.engine.audio_in is not None and view.state == sm.LISTENING:
                self.say("  (listening…)")
        if view.state in (sm.PROCESSING_SPEECH, sm.GENERATING_RESPONSE, sm.SPEAKING) or view.finishing:
            self.busy_seen = True
        elif view.state in WAITING and (self.busy_seen or view.state == sm.ERROR):
            self.ready.set()

    def print_turn(self, turn) -> None:
        self.say(f"\nteacher> {turn.reply_text}")
        for correction in turn.corrections:
            said = f"'{correction.you_said}' -> " if correction.you_said else ""
            self.say(f"  correction ({correction.priority}): {said}'{correction.better}'")
            if correction.explanation:
                self.say(f"    {correction.explanation}")
        if turn.natural_alternative:
            self.say(f"  more natural: {turn.natural_alternative}")
        for note in turn.vocabulary_notes:
            self.say(f"  phrase: {note.phrase} = {note.meaning}")
        if turn.practice_target:
            self.say(f"  say this: {turn.practice_target}")
        for key, value in (turn.interview_feedback or {}).items():
            self.say(f"  {key.replace('_', ' ')}: {value}")
        if turn.recognition_doubt:
            self.say("  (the teacher thinks it may have misheard you)")
        if self.engine.view.practice_result:
            self.say(f"  practice: {self.engine.view.practice_result}")

    def print_summary(self, summary) -> None:
        self.say("\n=== Lesson summary ===")
        for item in summary.went_well:
            self.say(f"  went well: {item}")
        if summary.main_improvement:
            self.say(f"  main improvement: {summary.main_improvement}")
        if summary.new_phrase:
            self.say(f"  new expression: {summary.new_phrase} ({summary.new_phrase_meaning})")
        if summary.next_exercise:
            self.say(f"  next time: {summary.next_exercise}")


def main(activity_id: str = "talk", voice: bool = False) -> int:
    engine = build_engine(text_only=not voice)
    view = TerminalView(engine)
    engine.start()
    if not engine.can_teach:
        view.say("The teacher is not set up:")
        for problem in engine.providers.problems:
            view.say(f"  {problem.title}: {problem.remedy}")
        engine.stop()
        return 1
    activity = modes.get(activity_id)
    where = ", ".join(engine.providers.remote_names()) or "this device"
    view.say(f"mLearning: {activity.title} (teacher: {where})")
    view.say(HELP)
    view.arm()
    engine.start_lesson(activity.id)
    try:
        while True:
            view.ready.wait(120)
            if engine.view.summary is not None and not engine.view.in_lesson:
                break
            try:
                line = input("\nyou> ").strip()
            except EOFError:
                break
            commands = {"/repeat": engine.repeat, "/slower": engine.slower, "/faster": engine.faster,
                        "/pause": engine.toggle_pause, "/retry": engine.retry,
                        "/back": engine.dismiss_error}
            if line in ("/quit", "/exit"):
                break
            if line == "/help":
                view.say(HELP)
                continue
            if line == "/notes":
                if engine.view.turn is not None:
                    view.print_turn(engine.view.turn)
                continue
            view.arm()
            if line == "/end":
                engine.end_lesson()
                view.ready.wait(120)
                break
            if line in ("/pause", "/back"):
                commands[line]()
                view.ready.set()
            elif line in commands:
                commands[line]()
            elif line:
                engine.typed(line)
            else:
                view.ready.set()
    except KeyboardInterrupt:
        pass
    engine.stop()
    return 0
