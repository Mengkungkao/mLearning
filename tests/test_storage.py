import json
import os
import stat
import time
import unittest

from app import config, envfile
from app.learning.profile import LearnerProfile
from app.learning.storage import LearningStorage
from app.teacher.schema import Summary, parse_turn
from tests.helpers import TempData


class ConfigTest(TempData):
    def test_defaults_without_a_file(self):
        settings = config.load(os.path.join(self.tmp, "none.json"))
        self.assertEqual(settings["listening_mode"], "hands_free")
        self.assertEqual(settings.correction_limit, 2)

    def test_round_trip_is_owner_only(self):
        self.settings.set("speech_speed", "slow")
        self.settings.save()
        mode = stat.S_IMODE(os.stat(self.settings.path).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(config.load(self.settings.path)["speech_speed"], "slow")

    def test_invalid_values_fall_back_unknown_keys_dropped(self):
        with open(self.settings.path, "w") as out:
            json.dump({"settings": {"speech_speed": "warp", "voice": "nova", "bogus": 1,
                                    "vad_end_db": 12, "session_minutes": 7}}, out)
        settings = config.load(self.settings.path)
        self.assertEqual(settings["speech_speed"], "normal")
        self.assertEqual(settings["vad_end_db"], 12.0)
        self.assertEqual(settings["session_minutes"], 15)
        self.assertNotIn("bogus", settings.values)

    def test_broken_file_is_kept_and_defaults_load(self):
        with open(self.settings.path, "w") as out:
            out.write("{not json")
        settings = config.load(self.settings.path)
        self.assertTrue(settings.problem)
        kept = [n for n in os.listdir(self.tmp) if n.startswith("settings.json.broken-")]
        self.assertEqual(len(kept), 1)

    def test_step_cycles(self):
        self.assertEqual(self.settings.step("speech_speed"), "fast")
        self.assertEqual(self.settings.step("speech_speed"), "slow")
        self.assertEqual(self.settings.step("cues"), False)
        with self.assertRaises(ValueError):
            self.settings.set("speech_speed", "warp")

    def test_every_menu_setting_can_step(self):
        for key in config.MENU_KEYS:
            before = self.settings[key]
            self.settings.step(key)
            self.settings.step(key, -1)
            self.assertEqual(self.settings[key], before, key)


class EnvTest(TempData):
    def test_parse_and_environment_wins(self):
        path = os.path.join(self.tmp, ".env")
        with open(path, "w") as out:
            out.write("# comment\nOPENAI_API_KEY='sk-file'\nexport ANTHROPIC_API_KEY=ak # note\nEMPTY=\n")
        os.chmod(path, 0o600)
        secrets = envfile.Secrets(path, environ={"OPENAI_API_KEY": "sk-env"})
        self.assertEqual(secrets.get("OPENAI_API_KEY"), "sk-env")
        self.assertEqual(secrets.get("ANTHROPIC_API_KEY"), "ak")
        self.assertEqual(secrets.describe()["OPENAI_BASE_URL"], "missing")
        self.assertNotIn("sk-", json.dumps(secrets.describe()))

    def test_template_is_created_once_owner_only(self):
        template = os.path.join(self.tmp, "template")
        with open(template, "w") as out:
            out.write("OPENAI_API_KEY=\n")
        target = os.path.join(self.tmp, "data", ".env")
        self.assertTrue(envfile.create_from_template(template, target))
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
        with open(target, "w") as out:
            out.write("OPENAI_API_KEY=mine\n")
        self.assertFalse(envfile.create_from_template(template, target))
        with open(target) as handle:
            self.assertIn("mine", handle.read())


class ProfileTest(TempData):
    def turn(self, level="B1", category="tense"):
        return parse_turn({"reply_text": "Ok", "level_estimate": level, "corrections": [
            {"priority": "grammar", "category": category, "you_said": "I go", "better": "I went",
             "explanation": ""}],
            "vocabulary_notes": [{"phrase": "call it a day", "meaning": "stop", "example": ""}]})

    def test_level_needs_many_estimates_from_two_lessons(self):
        profile = LearnerProfile()
        for _ in range(20):
            profile.record_turn("lesson-1", self.turn("B2"))
        self.assertEqual(profile.working_level(), "", "one lesson never labels the learner")
        for _ in range(5):
            profile.record_turn("lesson-2", self.turn("B1"))
        self.assertEqual(profile.working_level(), "B2")

    def test_mistakes_phrases_and_notes(self):
        profile = LearnerProfile()
        for _ in range(3):
            profile.record_turn("s", self.turn())
        self.assertEqual(profile.mistakes["tense"]["count"], 3)
        self.assertEqual(len(profile.mistakes["tense"]["examples"]), 1)
        self.assertEqual(len(profile.phrases), 1)
        notes = profile.notes_for_teacher()
        self.assertIn("verb tenses", notes)
        self.assertIn("call it a day", notes)

    def test_session_minutes_streak_and_summary(self):
        profile = LearnerProfile()
        profile.record_session(12.5, "daily", "coffee",
                               Summary(["x"], "Say went", "on the go", "busy", "Tell a story", "Well done"))
        self.assertEqual(profile.sessions, 1)
        self.assertEqual(profile.streak(), 1)
        self.assertEqual(profile.last_summary["next_exercise"], "Tell a story")
        self.assertTrue(any(p["phrase"] == "on the go" for p in profile.phrases))

    def test_round_trip_ignores_bad_types(self):
        profile = LearnerProfile.from_dict({"sessions": "many", "minutes_total": 3, "phrases": []})
        self.assertEqual(profile.sessions, 0)
        self.assertEqual(profile.minutes_total, 3.0)


class StorageTest(TempData):
    def test_profile_round_trip_and_permissions(self):
        profile = self.storage.load_profile()
        profile.add_phrase("break the ice", "start a conversation")
        self.storage.save_profile(profile)
        again = self.storage.load_profile()
        self.assertEqual(again.phrases[0]["phrase"], "break the ice")
        self.assertEqual(stat.S_IMODE(os.stat(self.storage.profile_path).st_mode), 0o600)

    def test_broken_profile_is_kept(self):
        os.makedirs(self.storage.root, exist_ok=True)
        with open(self.storage.profile_path, "w") as out:
            out.write("garbage")
        profile = self.storage.load_profile()
        self.assertEqual(profile.sessions, 0)
        self.assertTrue(self.storage.problem)
        self.assertTrue(any(n.startswith("profile.json.broken-") for n in os.listdir(self.storage.root)))

    def test_lessons_transcripts_and_delete_all(self):
        self.storage.save_lesson("20261024-100000", {"id": "a"})
        self.storage.save_lesson("20261024-110000", {"id": "b"})
        self.assertEqual([r["id"] for r in self.storage.lessons()], ["b", "a"])
        self.storage.save_transcript("20261024-110000", [("learner", "hi")])
        old = os.path.join(self.storage.transcripts_dir(), "old.json")
        with open(old, "w") as out:
            out.write("{}")
        os.utime(old, (time.time() - 40 * 86400,) * 2)
        self.assertEqual(self.storage.prune_transcripts(), 1)
        outside = os.path.join(self.tmp, "keep.txt")
        open(outside, "w").close()
        self.storage.delete_all()
        self.assertFalse(os.path.exists(self.storage.root))
        self.assertTrue(os.path.exists(outside))

    def test_refuses_to_delete_outside(self):
        with self.assertRaises(ValueError):
            self.storage._remove_inside(self.tmp)


if __name__ == "__main__":
    unittest.main()
