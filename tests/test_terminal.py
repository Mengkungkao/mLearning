"""The terminal lesson end to end on the real (threaded) engine, with fake providers."""

import io
import threading
import unittest

from app import terminal
from app.engine import Engine
from app.providers.fake import FakeLLM
from tests.helpers import TempData, providers

SUMMARY = {"went_well": ["You introduced yourself"], "main_improvement": "Say I am, not I is",
           "new_phrase": "nice to meet you", "new_phrase_meaning": "a greeting",
           "next_exercise": "Introduce a friend", "spoken_summary": "Good work."}


class TerminalTest(TempData):
    def run_terminal(self, lines, llm):
        # Providers are built on the engine thread, as in production (not passed in ready-made).
        engine = Engine(self.settings, self.secrets, self.storage, threaded=True, text_only=True,
                        build_providers=lambda settings, secrets: providers(llm=llm))
        feed = iter(lines)

        def read(prompt):
            try:
                return next(feed)
            except StopIteration:
                raise EOFError

        out = io.StringIO()
        done = threading.Event()
        result = {}

        def run():
            result["code"] = terminal.main("talk", engine=engine, read=read, out=out)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(done.wait(15), out.getvalue())
        return result["code"], out.getvalue(), engine

    def test_typed_lesson_with_summary(self):
        llm = FakeLLM([{"reply_text": "Hello! Tell me about yourself."},
                       {"reply_text": "Nice to meet you, Ana!", "corrections": [
                           {"priority": "grammar", "category": "agreement", "you_said": "I is Ana",
                            "better": "I am Ana", "explanation": "I takes am."}]},
                       SUMMARY])
        code, text, engine = self.run_terminal(["I is Ana", "/end"], llm)
        self.assertEqual(code, 0)
        self.assertIn("teacher> Hello! Tell me about yourself.", text)
        self.assertIn("'I is Ana' -> 'I am Ana'", text)
        self.assertIn("main improvement: Say I am, not I is", text)
        self.assertEqual(engine.profile.sessions, 1)

    def test_without_a_teacher_it_explains_and_exits(self):
        engine = Engine(self.settings, self.secrets, self.storage, threaded=True, text_only=True,
                        build_providers=lambda settings, secrets: providers())
        out = io.StringIO()
        code = terminal.main("talk", engine=engine, read=lambda p: "", out=out)
        self.assertEqual(code, 1)
        self.assertIn("not set up", out.getvalue())


if __name__ == "__main__":
    unittest.main()
