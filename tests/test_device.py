"""The LCD interface driven by the SDK's real InputController (unthreaded, fake
clock, keys through key_event), as the mFruit OS app contract asks. Every
gesture a footer advertises does what it says, gestures it does not advertise
do nothing, nothing acts without the screen, and every screen stays inside
the chrome."""

import unittest

import numpy as np

from app import state_machine as sm
from app.audio import dsp
from app.audio.capture import NO_SPEECH, SPEECH_START, UTTERANCE
from app.audio.vad import VadEvent
from app.device import GESTURE_OF, HOME_ITEMS, LESSON_HINTS, MENU_ROWS, DeviceUI
from app.engine import Engine
from app.providers.base import NETWORK, ProviderError, Transcript
from app.providers.fake import FakeASR, FakeLLM, FakeTTS
from app import config
from mfruit_sdk.input import BACK, EXTRA, NEXT, PREVIOUS, SELECT, TALK_START
from mfruit_sdk.keys import DOWN, UP, KeyEvent
from mfruit_sdk.ui.theme import CONTENT_BOTTOM, FOOTER_Y
from tests.helpers import FakeAudioIn, FakeAudioOut, FakeClock, TempData, providers, speech_like


class FakeBoard:
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


class DeviceTest(TempData):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.audio_in = FakeAudioIn()
        self.audio_out = FakeAudioOut()
        self.asr, self.llm, self.tts = FakeASR(), FakeLLM(), FakeTTS()
        self.make()

    def make(self, with_providers=True):
        p = providers(self.asr, self.llm, self.tts) if with_providers else providers()
        self.engine = Engine(self.settings, self.secrets, self.storage, self.audio_in, self.audio_out,
                             providers=p, clock=self.clock, threaded=False)
        self.board = FakeBoard()
        self.ui = DeviceUI(self.engine, board=self.board, keyboard=False, threaded=False, clock=self.clock)
        self.engine.start()
        self.ui.running = True
        self.ui.input.start()
        self.ui.draw()

    def tearDown(self):
        self.ui.input.stop()
        super().tearDown()

    # ------------------------------------------------------------ helpers
    def step(self, seconds):
        self.clock.advance(seconds)
        self.ui.input.gestures.poll()
        self.engine.pump()

    def clicks(self, count=1):
        for _ in range(count):
            self.ui.input.press()
            self.step(0.06)
            self.ui.input.release()
            self.step(0.1)
        self.step(0.6)

    def hold(self):
        self.ui.input.press()
        self.step(0.8)
        self.ui.input.release()
        self.step(0.6)

    def talk(self, pcm=None):
        self.ui.input.press()
        self.step(0.4)                       # talk screens talk after 350 ms
        if pcm is not None:
            self.audio_in.hold_pcm = pcm
        self.ui.input.release()
        self.step(0.6)

    def key(self, name, code, kind="key"):
        self.ui.input.key_event(KeyEvent(kind, name, DOWN, code))
        if kind == "key":
            self.ui.input.key_event(KeyEvent(kind, name, UP, code))
        self.engine.pump()

    def screen(self):
        return self.ui.effective_screen()

    def start_lesson(self):
        self.llm.replies.append({"reply_text": "Hi! How are you today?"})
        self.hold()                          # "Talk with your teacher" is the first row
        self.assertEqual(self.screen(), "lesson")

    def assert_inside_chrome(self, image, name=""):
        pixels = np.asarray(image.convert("RGB")).astype(int)
        background = pixels[CONTENT_BOTTOM + 1, 2]
        band = pixels[CONTENT_BOTTOM + 1:FOOTER_Y - 5, 22:218]   # rows 249-250, above the separator
        self.assertTrue((abs(band - background).sum(axis=2) < 30).all(),
                        f"{name}: drawing between the content and the footer")

    # ------------------------------------------------------------- home
    def test_first_frame_and_home_controls(self):
        self.assertEqual(self.board.frames[-1].size, (240, 280))
        self.assertEqual(self.screen(), "home")
        self.clicks(1)
        self.assertEqual(self.ui.index, 1)
        self.clicks(2)
        self.assertEqual(self.ui.index, 0)

    def test_hold_arms_then_opens_on_release(self):
        self.ui.input.press()
        self.step(0.8)
        self.assertTrue(self.ui.armed)
        self.assertEqual(self.screen(), "home")
        self.ui.input.release()
        self.step(0.1)
        self.assertEqual(self.screen(), "lesson")

    def test_back_from_home_leaves(self):
        self.clicks(4)
        self.assertTrue(self.ui.exit_requested.is_set())

    def test_escape_from_home_leaves(self):
        self.key("escape", 1)
        self.assertTrue(self.ui.exit_requested.is_set())

    def test_every_home_row_opens_and_back_returns(self):
        for index, (key, *_rest) in enumerate(HOME_ITEMS):
            if key in ("talk", "ask"):
                continue
            with self.subTest(row=key):
                self.ui.screen, self.ui.index = "home", index
                self.hold()
                self.assertEqual(self.screen(), key)
                self.clicks(4)
                self.assertEqual(self.screen(), "home")
                self.assertEqual(self.ui.index, index)

    def test_without_setup_ai_rows_open_the_setup_screen(self):
        self.make(with_providers=False)
        self.hold()
        self.assertEqual(self.screen(), "setup")
        self.assertIsNone(self.engine.session)

    def test_lists_have_working_back_rows(self):
        for screen, count in (("daily", len(self.ui.activity_rows(__import__("app.teacher.modes", fromlist=["DAILY"]).DAILY))),
                              ("settings", len(self.ui.settings_rows())),
                              ("interview", len(self.ui.interview_rows()))):
            with self.subTest(screen=screen):
                self.ui.screen, self.ui.index, self.ui.stack = "home", 0, []
                self.ui._open(screen)
                self.ui.index = count - 1
                self.hold()
                self.assertEqual(self.screen(), "home")

    # --------------------------------------------------------- settings
    def test_settings_change_with_hold_and_reverse_with_triple(self):
        self.ui._open("settings")
        speed = config.MENU_KEYS.index("speech_speed")
        self.ui.index = speed
        self.hold()
        self.assertEqual(self.settings["speech_speed"], "fast")
        self.clicks(3)
        self.assertEqual(self.settings["speech_speed"], "normal")

    def test_delete_history_asks_first(self):
        self.engine.profile.add_phrase("x")
        self.ui._open("privacy")
        self.ui.index = 2
        self.hold()
        self.assertEqual(self.screen(), "confirm_delete")
        self.assertEqual(self.ui.index, 0)
        self.clicks(1)                       # Cancel
        self.hold()
        self.assertEqual(len(self.engine.profile.phrases), 1)
        self.ui.index = 2
        self.hold()
        self.hold()                          # Delete
        self.assertEqual(self.engine.profile.phrases, [])

    def test_interview_job_by_preset_and_by_typing(self):
        self.ui._open("interview")
        self.ui.index = 1
        self.hold()
        self.assertEqual(self.settings["interview_job"], "barista")
        for char, code in (("c", 46), ("h", 35), ("e", 18), ("f", 33)):
            self.key(char, code, kind="char")
        self.assertEqual(self.ui.typing, "chef")
        self.key("enter", 28)
        self.assertEqual(self.settings["interview_job"], "chef")
        self.assertIsNone(self.ui.typing)

    # ----------------------------------------------------------- lesson
    def test_lesson_hold_talks_and_release_sends(self):
        self.settings.set("listening_mode", "hold")
        self.start_lesson()
        self.assertEqual(self.ui.lesson_key(), "waiting")
        self.asr.results.append(Transcript("I am fine thanks", 0.9))
        self.llm.replies.append({"reply_text": "Glad to hear it!"})
        self.talk(dsp.to_pcm16(speech_like(1.0)))
        self.assertEqual(len(self.asr.calls), 1)
        self.assertEqual(len(self.llm.calls), 2)

    def test_lesson_tap_pauses_and_resumes(self):
        self.start_lesson()
        self.assertEqual(self.ui.lesson_key(), "listening")
        self.clicks(1)
        self.assertEqual(self.engine.sm.state, sm.PAUSED)
        self.assertEqual(self.ui.lesson_key(), "paused")
        self.clicks(1)
        self.assertEqual(self.engine.sm.state, sm.LISTENING)

    def test_lesson_double_repeats(self):
        self.start_lesson()
        played = len(self.audio_out.played)
        self.clicks(2)
        self.assertEqual(len(self.audio_out.played), played + 1)

    def test_lesson_triple_opens_the_menu_and_every_row_works(self):
        self.start_lesson()
        self.clicks(3)
        self.assertEqual(self.screen(), "lesson_menu")
        self.ui.index = MENU_ROWS.index("slower")
        self.hold()
        self.assertEqual(self.settings["speech_speed"], "slow")
        self.assertEqual(self.screen(), "lesson")
        self.clicks(3)
        self.ui.index = MENU_ROWS.index("notes")
        self.hold()
        self.assertEqual(self.screen(), "notes")
        self.clicks(4)
        self.assertEqual(self.screen(), "lesson_menu")
        self.ui.index = MENU_ROWS.index("listening_mode")
        self.hold()
        self.assertEqual(self.settings["listening_mode"], "hold")
        self.ui.index = MENU_ROWS.index("back")
        self.hold()
        self.assertEqual(self.screen(), "lesson")

    def test_lesson_quad_ends_with_summary_then_home(self):
        self.start_lesson()
        self.asr.results.append(Transcript("I like tea", 0.9))
        self.llm.replies.append({"reply_text": "Me too!"})
        pcm = dsp.to_pcm16(speech_like(1.0))
        self.engine.on_capture(SPEECH_START, self.engine.turn, None)
        self.engine.on_capture(UTTERANCE, self.engine.turn, VadEvent("end", pcm, 1.0))
        self.engine.pump()
        self.llm.replies.append({"went_well": ["You said you like tea"], "main_improvement": "",
                                 "new_phrase": "", "new_phrase_meaning": "", "next_exercise": "",
                                 "spoken_summary": "Good work."})
        self.clicks(4)
        self.assertEqual(self.screen(), "summary")
        self.hold()
        self.assertEqual(self.screen(), "home")
        self.assertIsNone(self.engine.view.summary)

    def test_busy_lesson_ignores_unadvertised_gestures(self):
        self.start_lesson()
        self.engine._spawn = lambda *args: None
        self.asr.results.append(Transcript("hello", 0.9))
        pcm = dsp.to_pcm16(speech_like(1.0))
        self.engine.on_capture(UTTERANCE, self.engine.turn, VadEvent("end", pcm, 1.0))
        self.engine.pump()
        self.assertEqual(self.ui.lesson_key(), "busy")
        self.clicks(1)                       # tap is not offered while busy
        self.clicks(2)
        self.assertEqual(self.engine.sm.state, sm.PROCESSING_SPEECH)

    def test_every_advertised_lesson_gesture_has_a_command(self):
        for key, hints in LESSON_HINTS.items():
            gestures = [g for g, _label, _cmd in hints]
            self.assertEqual(len(gestures), len(set(gestures)), key)
            for gesture, label, command in hints:
                if gesture != "release":
                    self.assertIn(gesture, GESTURE_OF.values(), (key, gesture))
                    self.assertIsNotNone(command, (key, gesture))

    def test_typing_in_a_lesson_sends_text(self):
        self.start_lesson()
        self.llm.replies.append({"reply_text": "Nice."})
        for char, code in (("h", 35), ("i", 23)):
            self.key(char, code, kind="char")
        self.assertTrue(self.ui.input.typing())
        self.key("enter", 28)
        self.assertIn("(typed)", self.llm.calls[-1]["messages"][-1]["content"])
        self.assertIsNone(self.ui.typing)

    def test_error_overlay_retry_details_back(self):
        self.start_lesson()
        self.asr.results.append(ProviderError(NETWORK, "OpenAI", "reset"))
        pcm = dsp.to_pcm16(speech_like(1.0))
        self.engine.on_capture(UTTERANCE, self.engine.turn, VadEvent("end", pcm, 1.0))
        self.engine.pump()
        self.assertEqual(self.screen(), "error")
        self.clicks(1)
        self.hold()                          # Details
        self.assertEqual(self.screen(), "error_details")
        self.clicks(4)
        self.assertEqual(self.screen(), "error")
        self.ui.error_index = 2
        self.hold()                          # Back: the lesson continues
        self.assertEqual(self.screen(), "lesson")
        self.assertEqual(self.engine.sm.state, sm.IDLE)

    # ------------------------------------------------------- ownership
    def test_nothing_acts_without_the_screen(self):
        self.board.has_focus = False
        frames = len(self.board.frames)
        self.clicks(1)
        self.hold()
        self.key("escape", 1)
        self.assertEqual(self.ui.index, 0)
        self.assertEqual(self.screen(), "home")
        self.assertFalse(self.ui.exit_requested.is_set())
        self.ui.draw()
        self.assertEqual(len(self.board.frames), frames, "never draws without the screen")

    def test_key_pressed_elsewhere_is_ignored(self):
        self.board.has_focus = False
        self.ui.input.key_event(KeyEvent("key", "enter", DOWN, 28))
        self.board.has_focus = True
        self.ui._focus_changed(True)
        self.ui.input.key_event(KeyEvent("key", "enter", UP, 28))
        self.engine.pump()
        self.assertEqual(self.screen(), "home")

    def test_losing_focus_pauses_the_lesson(self):
        self.start_lesson()
        self.ui._focus_changed(False)
        self.engine.pump()
        self.assertEqual(self.engine.sm.state, sm.PAUSED)

    # ------------------------------------------------------- rendering
    def test_every_screen_stays_inside_the_chrome(self):
        from app import preview
        for name, image in preview.frames(self.tmp):
            with self.subTest(screen=name):
                self.assertEqual(image.size, (240, 280))
                self.assert_inside_chrome(image, name)


if __name__ == "__main__":
    unittest.main()
