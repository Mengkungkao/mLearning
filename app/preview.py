"""``run.sh --preview DIR``: every LCD screen as a PNG, from a scripted lesson.

The real engine and device interface run with fake providers, fake audio
and a fake board, so the pictures are exactly what the device would draw.
Also used by the smoke test and the rendering tests.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

from . import config
from .audio.playback import PlayResult
from .device import DeviceUI
from .engine import Engine
from .envfile import Secrets
from .learning.storage import LearningStorage
from .providers.base import NETWORK, ProviderError, Transcript
from .providers.fake import FakeASR, FakeLLM, FakeTTS
from .providers.registry import Problem, Providers


class PreviewBoard:
    app_id = "mlearning"

    def __init__(self):
        self.has_focus = True
        self.frames = []
        self.on_press = self.on_release = self.on_exit_request = lambda *a: None
        self.on_focus_changed = lambda has_focus: None

    def start(self):
        pass

    def stop(self):
        pass

    def show(self, image):
        self.frames.append(image)


class QuietIn:
    """Microphone stand-in with a speech-like level."""
    available = True
    device = "preview"

    def __init__(self):
        self.running = False
        self.level = -38.0
        self.floor = -62.0
        self.error = ""
        self.gate = False

    def start(self):
        self.running = True
        return True

    def stop(self):
        self.running = False

    def set_gate(self, open_):
        self.gate = open_

    def listen(self, turn, vad):
        pass

    def disarm(self):
        pass

    def hold_start(self, turn):
        pass

    def hold_end(self):
        return b""


class QuietOut:
    available = True
    device = "preview"
    playing = False
    queued_seconds = 0.0

    def progress(self):
        return 0.0

    def play(self, chunks, rate, keep=False, on_audio=None):
        audio = b"".join(chunks)
        return PlayResult(True, len(audio) / 2 / rate, "", audio if keep else b"")

    def play_pcm(self, pcm, rate):
        return PlayResult(True)

    def cue(self, kind):
        return PlayResult(True)

    def stop(self):
        pass


GREETING = {"reply_text": "Hi! How is your day going so far?"}
CORRECTED = {
    "reply_text": "Oh, you went shopping yesterday! Because it was yesterday, we say went. What did you buy?",
    "corrections": [{"priority": "grammar", "category": "tense", "you_said": "Yesterday I go to shopping",
                     "better": "Yesterday I went shopping",
                     "explanation": "Yesterday is past, so go becomes went. We say go shopping, not go to shopping."}],
    "vocabulary_notes": [{"phrase": "go shopping", "meaning": "go to shops to buy things",
                          "example": "We often go shopping on Saturdays."}],
    "level_estimate": "A2",
}
PRACTICE = {"reply_text": "Let's practise that. Say: Yesterday I went shopping with my friend.",
            "practice_target": "Yesterday I went shopping with my friend."}
SUMMARY = {"went_well": ["You told me about your day clearly", "You asked a good question back"],
           "main_improvement": "Say 'I went shopping' instead of 'I go to shopping' for the past.",
           "new_phrase": "go shopping", "new_phrase_meaning": "go to shops to buy things",
           "next_exercise": "Tell me three things you did last weekend.",
           "spoken_summary": "Well done today!"}


def build(tmp: str, with_keys: bool = True):
    settings = config.Settings(path=os.path.join(tmp, "settings.json"))
    secrets = Secrets(os.path.join(tmp, ".env"), environ={})
    storage = LearningStorage(os.path.join(tmp, "learning"))
    llm = FakeLLM()
    asr = FakeASR()
    if with_keys:
        providers = Providers(asr, llm, FakeTTS())
    else:
        providers = Providers(problems=[Problem("teacher", "OpenAI key missing",
                                                f"Add OPENAI_API_KEY to {secrets.path}, then Retry.")])
    engine = Engine(settings, secrets, storage, QuietIn(), QuietOut(), providers=providers,
                    threaded=False)
    board = PreviewBoard()
    ui = DeviceUI(engine, board=board, keyboard=False, threaded=False)
    engine.start()
    ui.running = True
    return engine, ui, llm, asr


def frames(tmp: str) -> list:
    """[(name, image)] for every screen, in a sensible order."""
    shots = []

    def shot(name, ui):
        shots.append((name, ui.render()))

    engine, ui, llm, asr = build(os.path.join(tmp, "nokeys"), with_keys=False)
    shot("01-home-needs-setup", ui)
    ui.screen = "setup"
    shot("02-setup", ui)

    engine, ui, llm, asr = build(os.path.join(tmp, "keys"))
    engine.profile.add_phrase("break the ice", "start a conversation", "A joke can break the ice.")
    engine.profile.add_phrase("go shopping", "go to shops to buy things")
    shot("03-home", ui)
    for name in ("daily", "listening", "interview", "settings", "phrases", "progress", "privacy"):
        ui.screen, ui.index = name, 0
        shot(f"04-{name}", ui)
    ui.screen, ui.index = "phrases", 0
    ui.phrase = engine.profile.phrases[-1]
    ui.screen = "phrase"
    shot("05-phrase", ui)
    ui.screen, ui.index = "home", 0

    # a lesson
    llm.replies.append(GREETING)
    ui._start_lesson("talk")
    engine.pump()
    shot("10-lesson-listening", ui)
    engine.hearing = True
    engine._publish()
    shot("11-lesson-hearing", ui)
    engine.hearing = False
    engine.last_transcript = "Yesterday I go to shopping with my friend"
    engine.sm.go("PROCESSING_SPEECH", "preview")
    engine.sm.go("GENERATING_RESPONSE", "preview")
    engine._publish()
    shot("12-lesson-thinking", ui)
    engine.sm.go("IDLE", "preview")
    asr.results.append(Transcript("Yesterday I go to shopping with my friend", 0.9))
    llm.replies.append(CORRECTED)
    engine._learner_said("Yesterday I go to shopping with my friend", 0.9, False)
    engine.pump()
    shot("13-lesson-correction", ui)
    engine.speaking_text = CORRECTED["reply_text"]
    engine.sm.go("SPEAKING", "preview")
    engine._publish()
    shot("14-lesson-speaking", ui)
    engine.sm.go("LISTENING", "preview")
    engine.sm.go("PROCESSING_SPEECH", "preview")
    llm.replies.append(PRACTICE)
    engine._learner_said("I bought a jacket", 0.9, False)
    engine.pump()
    shot("15-lesson-practice", ui)
    engine.toggle_pause()
    engine.pump()
    shot("16-lesson-paused", ui)
    engine.toggle_pause()
    engine.pump()
    ui.typing, ui.typing_for = "I has a red jacket", "lesson"
    shot("17-lesson-typing", ui)
    ui.typing = None
    ui.screen, ui.index = "lesson_menu", 0
    shot("18-lesson-menu", ui)
    ui.reader_title = "Notes"
    from .ui import screens
    ui.reader_blocks = screens.turn_blocks(engine.session.last_turn, engine.last_transcript)
    ui.screen, ui.first = "notes", 0
    shot("19-notes", ui)
    ui.screen = "lesson"
    engine._fail("respond", ProviderError(NETWORK, "OpenAI teacher", "connection reset"))
    engine._publish()
    shot("20-error", ui)
    engine.dismiss_error()
    engine.pump()
    shot("21-lesson-mic-off", ui)
    llm.replies.append(SUMMARY)
    engine.end_lesson()
    engine.pump()
    shot("22-summary", ui)
    engine.leave_summary()
    engine.pump()
    ui.screen, ui.index = "confirm_delete", 1
    shot("23-confirm-delete", ui)
    ui.screen = "progress"
    shot("24-progress-after", ui)
    engine.settings.set("listening_mode", "hold")
    llm.replies.append(GREETING)
    ui.screen, ui.index = "home", 0
    ui._start_lesson("coffee")
    engine.pump()
    shot("25-lesson-hold-to-talk", ui)
    return shots


def main(directory: str) -> int:
    os.makedirs(directory, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="mlearning-preview-")
    try:
        for name, image in frames(tmp):
            image.save(os.path.join(directory, f"{name}.png"))
            print(os.path.join(directory, f"{name}.png"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "preview"))
