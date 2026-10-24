"""The engine end to end with fake providers and fake audio (no threads, fake clock)."""

import os
import unittest

import numpy as np

from app import engine as engine_module
from app import state_machine as sm
from app.audio import dsp
from app.audio.capture import CAPTURE_ERROR, NO_SPEECH, SPEECH_START, UTTERANCE
from app.audio.vad import VadEvent
from app.engine import Engine
from app.providers.base import NETWORK, QUOTA, ProviderError, Transcript
from app.providers.fake import FakeASR, FakeLLM, FakeTTS
from app.teacher import prompts
from tests.helpers import FakeAudioIn, FakeAudioOut, FakeClock, TempData, providers, speech_like


def reply(text="Nice to meet you. What do you do?", **extra):
    answer = {"reply_text": text}
    answer.update(extra)
    return answer


SUMMARY = {"went_well": ["You described your job clearly"], "main_improvement": "Say went, not go",
           "new_phrase": "call it a day", "new_phrase_meaning": "stop working",
           "next_exercise": "Tell me about your weekend", "spoken_summary": "Well done today!"}


class EngineTest(TempData):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.audio_in = FakeAudioIn()
        self.audio_out = FakeAudioOut()
        self.asr = FakeASR()
        self.llm = FakeLLM()
        self.tts = FakeTTS()

    def make(self, with_providers=True):
        p = providers(self.asr, self.llm, self.tts) if with_providers else providers()
        self.engine = Engine(self.settings, self.secrets, self.storage, self.audio_in,
                             self.audio_out, providers=p, clock=self.clock, threaded=False)
        self.engine.start()
        return self.engine

    def pump(self):
        self.engine.pump()

    def state(self):
        return self.engine.sm.state

    def open_gate(self):
        self.clock.advance(engine_module.ECHO_GUARD + 0.01)
        self.pump()

    def utterance(self, seconds=1.0):
        pcm = dsp.to_pcm16(speech_like(seconds))
        self.engine.on_capture(SPEECH_START, self.engine.turn, None)
        self.engine.on_capture(UTTERANCE, self.engine.turn, VadEvent("end", pcm, seconds))
        self.pump()

    def start_lesson(self, activity="talk", greeting=None):
        self.llm.replies.append(greeting or reply("Hi! How is your day going?"))
        self.engine.start_lesson(activity)
        self.pump()

    # ------------------------------------------------------------------
    def test_starts_idle_and_explains_missing_setup(self):
        engine = self.make(with_providers=False)
        self.assertEqual(self.state(), sm.IDLE)
        self.assertFalse(engine.view.can_teach)
        engine.start_lesson("talk")
        self.pump()
        self.assertEqual(self.state(), sm.ERROR)
        self.assertIn("not set up", engine.view.error.title)
        engine.retry()
        self.pump()
        self.assertEqual(self.state(), sm.IDLE)
        self.assertIsNone(engine.session)

    def test_hands_free_lesson_cycle(self):
        engine = self.make()
        self.start_lesson()
        # greeting spoken, now listening with a cue; the gate opens after the echo guard
        self.assertEqual(self.state(), sm.LISTENING)
        self.assertEqual(self.tts.calls[0]["text"], "Hi! How is your day going?")
        self.assertEqual(self.audio_out.cues, ["listen"])
        self.assertEqual(self.audio_in.armed_turn, engine.turn)
        self.assertFalse(self.audio_in.gate)
        self.open_gate()
        self.assertTrue(self.audio_in.gate)

        self.asr.results.append(Transcript("Yesterday I go to shopping with my friend", 0.9))
        self.llm.replies.append(reply("Oh, you went shopping! What did you buy?", corrections=[
            {"priority": "grammar", "category": "tense", "you_said": "I go", "better": "I went",
             "explanation": "past"}]))
        self.utterance()
        self.assertEqual(self.state(), sm.LISTENING)
        self.assertEqual(len(self.llm.calls), 2)
        sent = self.llm.calls[1]["messages"]
        self.assertEqual([m["role"] for m in sent], ["user", "assistant", "user"])
        self.assertIn("Yesterday I go to shopping", sent[-1]["content"])
        self.assertIn("recognition confidence high", sent[-1]["content"])
        self.assertEqual(engine.view.turn.corrections[0].better, "I went")
        self.assertEqual(engine.view.last_transcript, "Yesterday I go to shopping with my friend")
        # progress was recorded and saved
        self.assertEqual(engine.profile.mistakes["tense"]["count"], 1)
        self.assertTrue(os.path.exists(self.storage.profile_path))

    def test_stale_results_are_dropped(self):
        engine = self.make()
        self.start_lesson()
        old_turn = engine.turn
        engine.toggle_pause()
        self.pump()
        self.assertEqual(self.state(), sm.PAUSED)
        self.assertFalse(self.audio_in.running, "pausing turns the microphone off")
        engine.post("replied", old_turn, engine_module.parse_turn(reply("late answer")))
        engine.post("spoken", old_turn, None)
        engine.post("transcribed", old_turn, Transcript("late"))
        self.pump()
        self.assertEqual(self.state(), sm.PAUSED)
        engine.toggle_pause()
        self.pump()
        self.assertEqual(self.state(), sm.LISTENING)
        self.assertTrue(self.audio_in.running)

    def test_repeat_command_replays_without_new_requests(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(Transcript("Could you repeat that, please?", 0.9))
        before_llm, before_tts = len(self.llm.calls), len(self.tts.calls)
        self.utterance()
        self.assertEqual(len(self.llm.calls), before_llm)
        self.assertEqual(len(self.tts.calls), before_tts)
        self.assertEqual(self.audio_out.played[-1][0], "pcm")
        self.assertEqual(self.state(), sm.LISTENING)

    def test_slower_command_changes_speed_and_respeaks(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(Transcript("Speak more slowly please", 0.9))
        self.utterance()
        self.assertEqual(self.settings["speech_speed"], "slow")
        self.assertEqual(self.tts.calls[-1]["speed"], 0.8)
        self.assertEqual(self.tts.calls[-1]["text"], "Hi! How is your day going?")
        self.assertTrue(os.path.exists(self.settings.path))

    def test_practice_target_waits_and_reports_result(self):
        engine = self.make()
        self.start_lesson(greeting=reply("Say: I went shopping.", practice_target="I went shopping."))
        self.assertEqual(self.state(), sm.PRACTICE_WAITING)
        self.assertEqual(engine.view.practice_target, "I went shopping.")
        self.engine.on_capture(SPEECH_START, engine.turn, None)
        self.pump()
        self.assertEqual(self.state(), sm.LISTENING)
        self.asr.results.append(Transcript("I go shopping", 0.8))
        self.llm.replies.append(reply("Almost! Went, not go."))
        pcm = dsp.to_pcm16(speech_like(1.0))
        engine.on_capture(UTTERANCE, engine.turn, VadEvent("end", pcm, 1.0))
        self.pump()
        content = self.llm.calls[-1]["messages"][-1]["content"]
        self.assertIn('Practice target was: "I went shopping."', content)
        self.assertIn("'go' for 'went'", content)
        self.assertIn("words", engine.view.practice_result)

    def test_recognition_failure_then_retry_repeats_the_same_audio(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(ProviderError(NETWORK, "OpenAI", "connection reset"))
        self.utterance()
        self.assertEqual(self.state(), sm.ERROR)
        self.assertEqual(engine.view.error.title, "No connection to OpenAI")
        self.assertIn("Retry", engine.view.error.remedy)
        first_len = self.asr.calls[-1][0]
        self.asr.results.append(Transcript("I work in a cafe", 0.9))
        self.llm.replies.append(reply("Which cafe?"))
        engine.retry()
        self.pump()
        self.assertEqual(self.asr.calls[-1][0], first_len)
        self.assertEqual(self.state(), sm.LISTENING)
        self.assertEqual(engine.session.turns, 1, "the learner's turn is recorded once")

    def test_teacher_failure_can_be_dismissed_and_the_lesson_continues(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(Transcript("hello", 0.9))
        self.llm.replies.append(ProviderError(QUOTA, "OpenAI teacher", "insufficient_quota"))
        self.utterance()
        self.assertEqual(self.state(), sm.ERROR)
        self.assertIn("quota", engine.view.error.title)
        engine.dismiss_error()
        self.pump()
        self.assertEqual(self.state(), sm.IDLE)
        self.assertIsNotNone(engine.session)
        engine.toggle_pause()          # tap: listen again
        self.pump()
        self.assertEqual(self.state(), sm.LISTENING)

    def test_watchdog_ends_a_hung_request(self):
        engine = self.make()
        engine._spawn = lambda *args: None          # the worker never answers
        engine.start_lesson("talk")
        self.pump()
        self.assertEqual(self.state(), sm.GENERATING_RESPONSE)
        self.clock.advance(sm.DEADLINES[sm.GENERATING_RESPONSE] + 1)
        engine._watchdog()
        self.assertEqual(self.state(), sm.ERROR)
        self.assertEqual(engine.view.error.title, "The teacher took too long")

    def test_end_lesson_summarises_records_and_returns_home(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(Transcript("I am barista", 0.9))
        self.llm.replies.append(reply("A barista! Do you like it?"))
        self.utterance()
        self.clock.advance(300)
        self.llm.replies.append(SUMMARY)
        engine.end_lesson()
        self.pump()
        self.assertEqual(self.llm.calls[-1]["schema"], "lesson_summary")
        self.assertIn("I am barista", self.llm.calls[-1]["messages"][0]["content"])
        self.assertEqual(self.tts.calls[-1]["text"], "Well done today!")
        self.assertEqual(self.state(), sm.IDLE)
        self.assertIsNone(engine.session)
        self.assertEqual(engine.view.summary.new_phrase, "call it a day")
        self.assertFalse(self.audio_in.running)
        self.assertEqual(engine.profile.sessions, 1)
        self.assertEqual(len(self.storage.lessons()), 1)
        engine.leave_summary()
        self.pump()
        self.assertIsNone(engine.view.summary)

    def test_empty_lesson_ends_without_summary(self):
        engine = self.make()
        self.start_lesson()
        calls = len(self.llm.calls)
        engine.end_lesson()
        self.pump()
        self.assertEqual(len(self.llm.calls), calls)
        self.assertEqual(self.state(), sm.IDLE)
        self.assertIsNone(engine.session)

    def test_teacher_can_end_the_lesson(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(Transcript("yes I want to finish now thanks", 0.9))
        self.llm.replies.append(reply("Bye for now!", end_session=True))
        self.llm.replies.append(SUMMARY)
        self.utterance()
        self.assertEqual(self.llm.calls[-1]["schema"], "lesson_summary")
        self.assertIsNone(engine.session)

    def test_finish_command(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(Transcript("I like coffee", 0.9))
        self.llm.replies.append(reply("Me too!"))
        self.utterance()
        self.asr.results.append(Transcript("Let's finish the lesson.", 0.9))
        self.llm.replies.append(SUMMARY)
        self.utterance()
        self.assertIsNone(engine.session)
        self.assertEqual(engine.view.summary.main_improvement, "Say went, not go")

    def test_no_speech_turns_the_microphone_off(self):
        engine = self.make()
        self.start_lesson()
        engine.on_capture(NO_SPEECH, engine.turn, None)
        self.pump()
        self.assertEqual(self.state(), sm.IDLE)
        self.assertFalse(self.audio_in.running)
        self.assertEqual(engine.view.notice, "Microphone off")

    def test_nothing_recognised_listens_again(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(Transcript("", None, no_speech=True))
        self.utterance()
        self.assertEqual(self.state(), sm.LISTENING)
        self.assertEqual(engine.view.notice, "I didn't catch that")

    def test_hold_to_talk(self):
        self.settings.set("listening_mode", "hold")
        engine = self.make()
        self.start_lesson()
        self.assertEqual(self.state(), sm.IDLE)
        self.assertEqual(self.audio_out.cues, [])
        engine.talk_start()
        self.pump()
        self.assertEqual(self.state(), sm.LISTENING)
        self.assertTrue(self.audio_in.gate)
        self.audio_in.hold_pcm = dsp.to_pcm16(speech_like(1.2))
        self.asr.results.append(Transcript("I live in Sydney", 0.9))
        self.llm.replies.append(reply("Sydney is lovely."))
        engine.talk_end()
        self.pump()
        self.assertEqual(self.state(), sm.IDLE)
        self.assertEqual(len(self.asr.calls), 1)

    def test_too_short_hold_is_ignored(self):
        self.settings.set("listening_mode", "hold")
        engine = self.make()
        self.start_lesson()
        engine.talk_start()
        self.pump()
        self.audio_in.hold_pcm = dsp.to_pcm16(speech_like(0.1))
        engine.talk_end()
        self.pump()
        self.assertEqual(self.state(), sm.IDLE)
        self.assertEqual(self.asr.calls, [])
        self.assertEqual(engine.view.notice, "Hold while you speak")

    def test_silent_hold_is_not_sent(self):
        self.settings.set("listening_mode", "hold")
        engine = self.make()
        self.start_lesson()
        engine.talk_start()
        self.pump()
        self.audio_in.hold_pcm = dsp.to_pcm16(np.zeros(16000))
        engine.talk_end()
        self.pump()
        self.assertEqual(self.asr.calls, [], "silence never leaves the device")

    def test_holding_during_speech_interrupts_the_teacher(self):
        engine = self.make()
        engine._spawn = lambda name, target, *args: None if name == "voice" else target(*args)
        self.start_lesson()
        self.assertEqual(self.state(), sm.SPEAKING)
        engine.talk_start()
        self.pump()
        self.assertGreaterEqual(self.audio_out.stops, 1)
        self.assertEqual(self.state(), sm.LISTENING)
        self.assertTrue(engine.view.holding)

    def test_microphone_busy_is_explained_and_retried(self):
        self.audio_in.start_ok = False
        engine = self.make()
        self.start_lesson()
        self.assertEqual(self.state(), sm.ERROR)
        self.assertEqual(engine.view.error.title, "Microphone busy")
        self.assertIn("sox", " ".join(engine.view.error.details))
        self.audio_in.start_ok = True
        engine.retry()
        self.pump()
        self.assertEqual(self.state(), sm.LISTENING)

    def test_stream_dying_while_listening_is_an_error(self):
        engine = self.make()
        self.start_lesson()
        engine.on_capture(CAPTURE_ERROR, 0, "arecord stopped (exit code 1)")
        self.pump()
        self.assertEqual(self.state(), sm.ERROR)
        self.assertEqual(engine.view.error.title, "Microphone problem")

    def test_losing_the_screen_pauses(self):
        engine = self.make()
        self.start_lesson()
        engine.focus(False)
        self.pump()
        self.assertEqual(self.state(), sm.PAUSED)
        self.assertFalse(self.audio_in.running)

    def test_shutdown_saves_the_lesson(self):
        engine = self.make()
        self.start_lesson()
        self.asr.results.append(Transcript("hello", 0.9))
        self.llm.replies.append(reply("Hi"))
        self.utterance()
        engine.stop()
        self.assertEqual(self.state(), sm.SHUTTING_DOWN)
        self.assertEqual(engine.profile.sessions, 1)
        self.assertEqual(len(self.storage.lessons()), 1)

    def test_typed_text_is_marked_typed(self):
        engine = self.make()
        self.start_lesson()
        self.llm.replies.append(reply("Good typing!"))
        engine.typed("I has a dog")
        self.pump()
        self.assertIn("(typed)", self.llm.calls[-1]["messages"][-1]["content"])

    def test_time_up_note_is_sent_once(self):
        self.settings.set("session_minutes", 10)
        engine = self.make()
        self.start_lesson()
        self.clock.advance(11 * 60)
        for text in ("one", "two", "three"):
            self.asr.results.append(Transcript(text, 0.9))
            self.llm.replies.append(reply("ok"))
            self.utterance()
        notes = [m["messages"][-1]["content"] for m in self.llm.calls[1:]]
        self.assertEqual(sum(prompts.TIME_UP_NOTE in n for n in notes), 1)

    def test_delete_history_only_outside_a_lesson(self):
        engine = self.make()
        self.start_lesson()
        engine.delete_history()
        self.pump()
        self.assertEqual(engine.view.notice, "Finish the lesson first")
        engine.end_lesson()
        self.pump()
        engine.profile.add_phrase("x")
        engine.delete_history()
        self.pump()
        self.assertEqual(engine.profile.phrases, [])
        self.assertFalse(os.path.exists(self.storage.root))

    def test_screen_commands_wait_while_the_teacher_thinks(self):
        engine = self.make()
        engine._spawn = lambda *args: None
        engine.start_lesson("talk")
        self.pump()
        engine.repeat()
        self.pump()
        self.assertEqual(self.state(), sm.GENERATING_RESPONSE)
        self.assertEqual(engine.view.notice, "One moment…")

    def test_speaker_failure_is_an_error_with_retry(self):
        self.audio_out.error = "audio open error: No such device"
        engine = self.make()
        self.start_lesson()
        self.assertEqual(self.state(), sm.ERROR)
        self.assertEqual(engine.view.error.title, "Speaker problem")
        self.audio_out.error = ""
        engine.retry()
        self.pump()
        self.assertEqual(self.state(), sm.LISTENING)
        self.assertEqual(self.tts.calls[-1]["text"], "Hi! How is your day going?")


if __name__ == "__main__":
    unittest.main()
