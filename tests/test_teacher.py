import json
import unittest

from app import config
from app.teacher import commands, modes, practice, prompts
from app.teacher.schema import (FALLBACK_REPLY, SUMMARY_SCHEMA, TEACHER_SCHEMA, parse_summary,
                                parse_turn, speakable)
from app.teacher.session import MAX_MESSAGES, Session


def full_answer(**overrides):
    answer = {
        "reply_text": "I understood you. A more natural sentence is: Yesterday, I went shopping with my friend.",
        "intended_meaning": "",
        "corrections": [
            {"priority": "fluency", "category": "naturalness", "you_said": "go to shopping",
             "better": "go shopping", "explanation": "We say go shopping."},
            {"priority": "grammar", "category": "tense", "you_said": "I go",
             "better": "I went", "explanation": "Yesterday needs the past tense."},
            {"priority": "meaning", "category": "word_choice", "you_said": "my friend buyed",
             "better": "my friend bought", "explanation": "Buy becomes bought."},
        ],
        "natural_alternative": "",
        "vocabulary_notes": [{"phrase": "go shopping", "meaning": "shop for things", "example": "Let's go shopping."}],
        "follow_up_question": "What did you buy?",
        "practice_target": "Yesterday, I went shopping with my friend.",
        "teaching_mode": "focused",
        "recognition_doubt": False,
        "uncertainty": "",
        "level_estimate": "A2",
        "interview_feedback": {"answered_question": "", "structure": "", "language": "",
                               "delivery": "", "improved_answer": ""},
        "end_session": False,
    }
    answer.update(overrides)
    return answer


class SchemaTest(unittest.TestCase):
    def test_schemas_are_strict_compatible(self):
        def check(node):
            if node.get("type") == "object":
                self.assertFalse(node["additionalProperties"])
                self.assertEqual(sorted(node["required"]), sorted(node["properties"]))
                for child in node["properties"].values():
                    check(child)
            if node.get("type") == "array":
                check(node["items"])
        check(TEACHER_SCHEMA)
        check(SUMMARY_SCHEMA)
        json.dumps(TEACHER_SCHEMA)

    def test_corrections_are_ranked_and_limited(self):
        turn = parse_turn(full_answer(), correction_limit=2)
        self.assertEqual([c.priority for c in turn.corrections], ["meaning", "grammar"])
        self.assertTrue(turn.problems)

    def test_missing_and_wrong_fields_get_defaults(self):
        turn = parse_turn({"reply_text": "Hello there!", "corrections": "nope",
                           "teaching_mode": "dance", "level_estimate": "Z9",
                           "recognition_doubt": "yes"})
        self.assertEqual(turn.reply_text, "Hello there!")
        self.assertEqual(turn.corrections, [])
        self.assertEqual(turn.teaching_mode, "conversation")
        self.assertEqual(turn.level_estimate, "")
        self.assertFalse(turn.recognition_doubt)

    def test_empty_reply_falls_back_and_is_flagged(self):
        turn = parse_turn({"reply_text": "  ", "follow_up_question": "How was your day?"})
        self.assertEqual(turn.reply_text, "How was your day?")
        self.assertIn("nothing to say", turn.problems)
        self.assertEqual(parse_turn("not a dict").reply_text, FALLBACK_REPLY)

    def test_no_op_and_duplicate_corrections_are_dropped(self):
        answer = full_answer(corrections=[
            {"priority": "grammar", "category": "tense", "you_said": "I went.", "better": "I went",
             "explanation": ""},
            {"priority": "grammar", "category": "tense", "you_said": "I go", "better": "I went",
             "explanation": ""},
            {"priority": "grammar", "category": "tense", "you_said": "me go", "better": "I went",
             "explanation": ""}])
        turn = parse_turn(answer, correction_limit=3)
        self.assertEqual(len(turn.corrections), 1)

    def test_spoken_text_never_contains_notation(self):
        self.assertEqual(speakable('**Great!** {"reply_text": "Say it"} # Title'), 'Great! "Say it" Title')
        self.assertEqual(speakable("- first\n- second"), "first second")

    def test_long_reply_is_cut_at_a_sentence(self):
        reply = "This is a sentence. " * 80
        turn = parse_turn({"reply_text": reply})
        self.assertLessEqual(len(turn.reply_text), 900)
        self.assertTrue(turn.reply_text.endswith("."))

    def test_summary_defaults(self):
        summary = parse_summary({"went_well": ["You told a story", 3, ""]})
        self.assertEqual(summary.went_well, ["You told a story", "3"])
        self.assertTrue(summary.spoken_summary)


class CommandsTest(unittest.TestCase):
    def test_commands(self):
        cases = {
            "Repeat that.": commands.REPEAT,
            "Could you say that again, please?": commands.REPEAT,
            "Pardon?": commands.REPEAT,
            "Can you speak more slowly please": commands.SLOWER,
            "Slower, please.": commands.SLOWER,
            "Speak faster.": commands.FASTER,
            "Stop listening.": commands.PAUSE,
            "Pause the lesson": commands.PAUSE,
            "Let's finish the lesson.": commands.FINISH,
            "OK, let's stop here.": commands.FINISH,
            "Let's practise interviews.": commands.INTERVIEW,
            "Let's practice an interview": commands.INTERVIEW,
        }
        for text, expected in cases.items():
            self.assertEqual(commands.detect(text), expected, text)

    def test_sentences_are_not_commands(self):
        for text in ("I want to stop smoking next year.", "What does repeat mean?",
                     "My bus stop is far from my house.", "Correct my sentence please",
                     "I don't understand", "Yesterday I go to shopping with my friend", ""):
            self.assertIsNone(commands.detect(text), text)


class PracticeTest(unittest.TestCase):
    def test_exact_repeat(self):
        result = practice.compare("Yesterday, I went shopping with my friend.",
                                  "yesterday i went shopping with my friend")
        self.assertTrue(result.all_words)
        self.assertEqual(result.short(), "Every word recognised")

    def test_missing_and_substituted_words(self):
        result = practice.compare("Yesterday I went shopping with my friend",
                                  "yesterday I go shopping with friend")
        self.assertEqual(result.missing, ["my"])
        self.assertEqual(result.substituted, [("went", "go")])
        self.assertIn("Recognition errors are possible", result.describe())
        self.assertNotIn("pronunciation mistake", result.short())

    def test_contractions_and_numbers_match(self):
        result = practice.compare("I'm twenty-two and I don't have two cats",
                                  "I am twenty two and I do not have 2 cats")
        self.assertTrue(result.all_words, result)

    def test_nothing_heard(self):
        result = practice.compare("Good morning", "")
        self.assertEqual(result.short(), "I didn't catch that")


class PromptsTest(unittest.TestCase):
    def test_system_prompt_follows_settings(self):
        settings = config.Settings()
        settings.set("style", "focused")
        settings.set("corrections", "few")
        settings.set("variety", "british")
        text = prompts.system_prompt(settings, modes.get("coffee"), "Recurring mistakes: tense.")
        self.assertIn("At most 1 correction", text)
        self.assertIn("focused learning", text)
        self.assertIn("British English", text)
        self.assertIn("barista", text)
        self.assertIn("Recurring mistakes: tense.", text)

    def test_interview_without_job_asks_for_it_and_never_invents(self):
        settings = config.Settings()
        text = prompts.system_prompt(settings, modes.get("interview"))
        self.assertIn("which job they are preparing for", text)
        self.assertIn("Never invent", text)
        settings.set("interview_job", "barista")
        self.assertNotIn("which job they are preparing for",
                         prompts.system_prompt(settings, modes.get("interview")))

    def test_learner_message_carries_confidence_and_practice(self):
        result = practice.compare("Good morning", "good morning")
        text = prompts.learner_message("good morning", 0.3, practice=result, note="time is up")
        self.assertIn("recognition confidence low", text)
        self.assertIn('Practice target was: "Good morning"', text)
        self.assertIn("time is up", text)
        self.assertIn("typed", prompts.learner_message("hi", typed=True))

    def test_every_activity_has_a_brief(self):
        for activity in modes.ACTIVITIES.values():
            self.assertTrue(activity.brief and activity.title, activity.id)
            self.assertTrue(modes.page_name(activity))


class SessionTest(unittest.TestCase):
    def test_history_alternates_and_is_bounded(self):
        session = Session(modes.CONVERSATION, "system")
        turn = parse_turn(full_answer())
        session.commit("[opening]", turn)
        for index in range(40):
            session.commit(f"learner {index}", turn, f"text {index}")
        messages = session.request_messages("new")
        self.assertLessEqual(len(messages), MAX_MESSAGES + 1)
        self.assertEqual(messages[0]["content"], "[opening]")
        roles = [m["role"] for m in messages]
        for a, b in zip(roles, roles[1:]):
            self.assertNotEqual(a, b)
        self.assertEqual(roles[-1], "user")
        self.assertEqual(session.turns, 40)

    def test_practice_attempts_count(self):
        session = Session(modes.CONVERSATION, "system")
        turn = parse_turn(full_answer())
        session.commit("a", turn, "x")
        session.commit("b", turn, "y")
        self.assertEqual(session.practice_attempts, 1)
        session.commit("c", parse_turn(full_answer(practice_target="")), "z")
        self.assertEqual(session.practice_target, "")


if __name__ == "__main__":
    unittest.main()
