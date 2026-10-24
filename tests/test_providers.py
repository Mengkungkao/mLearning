"""Provider adapters against a local fake server: the real HTTP code paths run."""

import json
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import config
from app.envfile import Secrets
from app.providers import anthropic_llm, http, openai, registry
from app.providers.base import (AUTH, BAD_RESPONSE, NETWORK, QUOTA, RATE_LIMIT, REFUSED, REQUEST,
                                SERVER, UNAVAILABLE, ProviderError)
from app.teacher.schema import TEACHER_SCHEMA


class FakeOpenAI(BaseHTTPRequestHandler):
    routes = {}          # path -> callable(handler, body) -> (status, headers, bytes)
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        FakeOpenAI.seen.append((self.path, dict(self.headers), body))
        status, headers, payload = FakeOpenAI.routes[self.path](self, body)
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def json_reply(data, status=200, headers=None):
    return lambda handler, body: (status, dict({"Content-Type": "application/json"}, **(headers or {})),
                                  json.dumps(data).encode())


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeOpenAI.routes = {}
        FakeOpenAI.seen = []
        self.client = openai.OpenAIClient("sk-test-key", self.base, timeout=5)


class OpenAIASRTest(ServerTest):
    def test_uploads_wav_and_reads_confidence(self):
        FakeOpenAI.routes["/v1/audio/transcriptions"] = json_reply({
            "text": " Yesterday I go to shopping. ",
            "segments": [{"avg_logprob": -0.2, "no_speech_prob": 0.01}]})
        asr = openai.OpenAIASR(self.client)
        result = asr.transcribe(b"\x00\x01" * 16000, 16000, "What did you do?")
        self.assertEqual(result.text, "Yesterday I go to shopping.")
        self.assertGreater(result.confidence, 0.8)
        self.assertFalse(result.no_speech)
        path, headers, body = FakeOpenAI.seen[0]
        self.assertEqual(headers["Authorization"], "Bearer sk-test-key")
        self.assertIn(b'name="model"\r\n\r\nwhisper-1', body)
        self.assertIn(b'name="language"\r\n\r\nen', body)
        self.assertIn(b"verbose_json", body)
        self.assertIn(b"RIFF", body)
        self.assertIn(b"What did you do?", body)

    def test_whisper_invention_on_silence_is_no_speech(self):
        result = openai.parse_transcription({"text": "Thanks for watching!", "segments": [
            {"avg_logprob": -1.4, "no_speech_prob": 0.9}]})
        self.assertTrue(result.no_speech)

    def test_errors_are_classified(self):
        asr = openai.OpenAIASR(self.client)
        cases = [
            (401, {"error": {"message": "Incorrect API key"}}, AUTH),
            (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}, QUOTA),
            (400, {"error": {"message": "bad file"}}, REQUEST),
        ]
        for status, body, kind in cases:
            FakeOpenAI.routes["/v1/audio/transcriptions"] = json_reply(body, status)
            with self.assertRaises(ProviderError) as caught:
                asr.transcribe(b"\x00" * 3200)
            self.assertEqual(caught.exception.kind, kind, status)
            self.assertNotIn("sk-test-key", str(caught.exception))

    def test_server_error_is_retried_once(self):
        calls = []

        def flaky(handler, body):
            calls.append(1)
            if len(calls) == 1:
                return 503, {}, b"{}"
            return 200, {"Content-Type": "application/json"}, json.dumps({"text": "hi"}).encode()

        FakeOpenAI.routes["/v1/audio/transcriptions"] = flaky
        result = openai.OpenAIASR(self.client).transcribe(b"\x00" * 3200)
        self.assertEqual(result.text, "hi")
        self.assertEqual(len(calls), 2)

    def test_short_rate_limit_is_retried_long_one_is_not(self):
        FakeOpenAI.routes["/v1/audio/transcriptions"] = json_reply(
            {"error": {"message": "slow down"}}, 429, {"retry-after": "120"})
        with self.assertRaises(ProviderError) as caught:
            openai.OpenAIASR(self.client).transcribe(b"\x00" * 3200)
        self.assertEqual(caught.exception.kind, RATE_LIMIT)
        self.assertEqual(len(FakeOpenAI.seen), 1)

    def test_unreachable_server_is_a_network_error(self):
        client = openai.OpenAIClient("k", "http://127.0.0.1:9/v1", timeout=2)
        with self.assertRaises(ProviderError) as caught:
            openai.OpenAIASR(client).transcribe(b"\x00" * 3200)
        self.assertEqual(caught.exception.kind, NETWORK)


class OpenAILLMTest(ServerTest):
    def test_strict_json_schema_request(self):
        FakeOpenAI.routes["/v1/chat/completions"] = json_reply({"choices": [
            {"message": {"content": json.dumps({"reply_text": "Hi!"})}, "finish_reason": "stop"}]})
        llm = openai.OpenAILLM(self.client, "gpt-4o")
        result = llm.complete_json("system", [{"role": "user", "content": "hello"}],
                                   TEACHER_SCHEMA, "teacher_turn")
        self.assertEqual(result, {"reply_text": "Hi!"})
        body = json.loads(FakeOpenAI.seen[0][2])
        self.assertEqual(body["model"], "gpt-4o")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "system"})
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])

    def test_falls_back_to_json_object_for_compatible_servers(self):
        calls = []

        def picky(handler, body):
            calls.append(json.loads(body)["response_format"]["type"])
            if calls[-1] == "json_schema":
                return 400, {"Content-Type": "application/json"}, json.dumps(
                    {"error": {"message": "response_format json_schema not supported"}}).encode()
            return 200, {"Content-Type": "application/json"}, json.dumps({"choices": [
                {"message": {"content": "{\"reply_text\": \"ok\"}"}}]}).encode()

        FakeOpenAI.routes["/v1/chat/completions"] = picky
        result = openai.OpenAILLM(self.client).complete_json("s", [], TEACHER_SCHEMA, "t")
        self.assertEqual(result["reply_text"], "ok")
        self.assertEqual(calls, ["json_schema", "json_object"])

    def test_refusal_and_bad_json(self):
        with self.assertRaises(ProviderError) as caught:
            openai.parse_chat_json({"choices": [{"message": {"refusal": "no"}}]})
        self.assertEqual(caught.exception.kind, REFUSED)
        with self.assertRaises(ProviderError) as caught:
            openai.parse_chat_json({"choices": [{"message": {"content": "hello"}}]})
        self.assertEqual(caught.exception.kind, BAD_RESPONSE)
        with self.assertRaises(ProviderError):
            openai.parse_chat_json({})


class OpenAITTSTest(ServerTest):
    def test_streams_pcm_with_speed_and_voice(self):
        audio = bytes(range(256)) * 400
        FakeOpenAI.routes["/v1/audio/speech"] = lambda h, b: (200, {"Content-Type": "audio/pcm"}, audio)
        tts = openai.OpenAITTS(self.client, "tts-1-hd", "nova")
        chunks = list(tts.stream("Hello there", "shimmer", 0.8, "warm"))
        self.assertEqual(b"".join(chunks), audio)
        self.assertGreater(len(chunks), 1)
        body = json.loads(FakeOpenAI.seen[0][2])
        self.assertEqual(body["response_format"], "pcm")
        self.assertEqual(body["voice"], "shimmer")
        self.assertEqual(body["speed"], 0.8)
        self.assertNotIn("instructions", body, "tts-1-hd rejects instructions")

    def test_error_raised_on_first_chunk(self):
        FakeOpenAI.routes["/v1/audio/speech"] = json_reply({"error": {"message": "boom"}}, 500)
        stream = openai.OpenAITTS(self.client).stream("Hi")
        with self.assertRaises(ProviderError) as caught:
            next(iter(stream))
        self.assertEqual(caught.exception.kind, SERVER)

    def test_cancel_stops_reading(self):
        FakeOpenAI.routes["/v1/audio/speech"] = lambda h, b: (200, {}, b"\x00" * 100000)
        stream = openai.OpenAITTS(self.client).stream("Hi", cancelled=lambda: True)
        self.assertEqual(list(stream), [])


class FakeAnthropicModule(types.ModuleType):
    pass


class ClaudeTest(unittest.TestCase):
    def make_client(self, response=None, error=None):
        calls = []

        class Messages:
            def create(self, **kwargs):
                calls.append(kwargs)
                if error is not None:
                    raise error
                return response

        client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=Messages()))
        return client, calls

    def test_request_shape_and_parse(self):
        block = types.SimpleNamespace(type="text", text=json.dumps({"reply_text": "G'day!"}))
        response = types.SimpleNamespace(content=[block], stop_reason="end_turn")
        client, calls = self.make_client(response)
        llm = anthropic_llm.ClaudeLLM("key", client=client)
        result = llm.complete_json("sys", [{"role": "user", "content": "hi"}], TEACHER_SCHEMA, "t")
        self.assertEqual(result["reply_text"], "G'day!")
        call = calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["output_config"]["effort"], "low")
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(call["betas"], [anthropic_llm.FALLBACK_BETA])
        self.assertGreaterEqual(call["max_tokens"], 16000)
        self.assertNotIn("thinking", call)

    def test_refusal_is_reported(self):
        response = types.SimpleNamespace(content=[], stop_reason="refusal",
                                         stop_details=types.SimpleNamespace(category="cyber"))
        client, _ = self.make_client(response)
        with self.assertRaises(ProviderError) as caught:
            anthropic_llm.ClaudeLLM("key", client=client).complete_json("s", [], TEACHER_SCHEMA, "t")
        self.assertEqual(caught.exception.kind, REFUSED)


class RegistryTest(unittest.TestCase):
    def test_nothing_without_keys_and_problems_name_the_file(self):
        settings = config.Settings()
        providers = registry.build(settings, Secrets("/tmp/none/.env", environ={}))
        self.assertFalse(providers.can_teach)
        self.assertTrue(all("/tmp/none/.env" in p.remedy for p in providers.problems))

    def test_openai_key_sets_up_everything_remote(self):
        providers = registry.build(config.Settings(), Secrets("x", environ={"OPENAI_API_KEY": "sk"}))
        self.assertTrue(providers.can_teach)
        self.assertEqual(len(providers.remote_names()), 3)
        self.assertIn("online", providers.summary()[0])

    def test_claude_without_key_or_package_is_explained_not_swapped(self):
        settings = config.Settings()
        settings.set("llm_provider", "anthropic")
        providers = registry.build(settings, Secrets("x", environ={"OPENAI_API_KEY": "sk"}))
        self.assertIsNone(providers.llm, "never silently falls back to another provider")
        self.assertEqual(providers.problems[0].part, "teacher")


if __name__ == "__main__":
    unittest.main()
