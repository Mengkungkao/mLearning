import io
import threading
import time
import unittest

import numpy as np

from app.audio import capture, devices, dsp, playback
from app.audio.vad import END, START, TIMEOUT, Endpointer, VadConfig
from tests.helpers import noise, speech_like


def tone_db(seconds, freq, rate, level_db):
    t = np.arange(int(seconds * rate)) / rate
    return np.sin(2 * np.pi * freq * t) * 32768 * 10 ** (level_db / 20)


class DspTest(unittest.TestCase):
    def test_mono_is_the_average_of_both_channels(self):
        stereo = np.array([1000, 3000, -2000, 0], dtype="<i2").tobytes()
        self.assertEqual(list(dsp.to_mono(stereo)), [2000.0, -1000.0])

    def test_decimator_keeps_speech_and_removes_what_would_fold(self):
        rate = dsp.HARDWARE_RATE
        speech = tone_db(1.0, 1000, rate, -20)
        alias = tone_db(1.0, 15000, rate, -20)         # would fold to 1 kHz at 16 kHz
        dec = dsp.Decimator()
        kept = np.concatenate([dec.process(c) for c in np.array_split(speech, 37)])
        dec = dsp.Decimator()
        folded = np.concatenate([dec.process(c) for c in np.array_split(alias, 37)])
        self.assertAlmostEqual(kept.size, 16000, delta=1)
        self.assertGreater(dsp.rms_dbfs(kept[2000:]), -24)
        self.assertLess(dsp.rms_dbfs(folded[2000:]), -70)

    def test_decimator_is_continuous_across_chunks(self):
        signal = tone_db(0.5, 440, dsp.HARDWARE_RATE, -10)
        whole = dsp.Decimator().process(signal)
        dec = dsp.Decimator()
        pieces = np.concatenate([dec.process(c) for c in np.array_split(signal, 13)])
        np.testing.assert_allclose(whole, pieces, atol=1e-2)

    def test_upsampler_doubles_the_rate_without_images(self):
        rate = dsp.TTS_RATE
        signal = tone_db(0.5, 3000, rate, -12)
        up = dsp.Upsampler(2)
        out = np.concatenate([up.process(c) for c in np.array_split(signal, 9)])
        self.assertEqual(out.size, signal.size * 2)
        spectrum = np.abs(np.fft.rfft(out[4000:]))
        freqs = np.fft.rfftfreq(out[4000:].size, 1 / dsp.HARDWARE_RATE)
        main = spectrum[np.argmin(abs(freqs - 3000))]
        image = spectrum[np.argmin(abs(freqs - 21000))]
        self.assertLess(image / main, 0.01)

    def test_wav_and_levels(self):
        pcm = dsp.to_pcm16(tone_db(0.1, 440, 16000, -6))
        wav = dsp.wav_bytes(pcm)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertAlmostEqual(dsp.seconds_of(pcm, 16000), 0.1, places=3)
        self.assertEqual(dsp.rms_dbfs(np.zeros(100)), dsp.SILENCE_DBFS)


class VadTest(unittest.TestCase):
    def run_vad(self, signal, config=None, chunk=640):
        vad = Endpointer(config or VadConfig(no_speech_timeout=None))
        events = []
        for start in range(0, signal.size, chunk):
            events += vad.feed(signal[start:start + chunk])
        return vad, events

    def test_detects_a_turn_with_preroll_and_trimmed_tail(self):
        signal = np.concatenate([noise(1.0), speech_like(1.5), noise(2.0)])
        vad, events = self.run_vad(signal, VadConfig(end_silence=1.0, no_speech_timeout=None))
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, [START, END])
        turn = events[-1]
        seconds = dsp.seconds_of(turn.pcm, 16000)
        # 1.5 s of speech + up to 0.4 s pre-roll + ~0.25 s tail
        self.assertGreater(seconds, 1.5)
        self.assertLess(seconds, 2.3)
        self.assertGreater(turn.speech_seconds, 1.0)

    def test_a_thinking_pause_shorter_than_patience_does_not_end_the_turn(self):
        signal = np.concatenate([noise(0.5), speech_like(1.0), noise(1.0), speech_like(1.0),
                                 noise(2.5)])
        _, events = self.run_vad(signal, VadConfig(end_silence=1.6, no_speech_timeout=None))
        self.assertEqual([e.kind for e in events], [START, END])
        self.assertGreater(dsp.seconds_of(events[-1].pcm, 16000), 3.0)

    def test_short_patience_ends_at_the_pause(self):
        signal = np.concatenate([noise(0.5), speech_like(1.0), noise(1.2), speech_like(1.0)])
        _, events = self.run_vad(signal, VadConfig(end_silence=0.8, no_speech_timeout=None))
        self.assertEqual([e.kind for e in events], [START, END])
        self.assertLess(dsp.seconds_of(events[-1].pcm, 16000), 2.4)

    def test_noise_alone_times_out(self):
        _, events = self.run_vad(noise(6.0), VadConfig(no_speech_timeout=5.0))
        self.assertEqual([e.kind for e in events], [TIMEOUT])

    def test_a_click_is_not_a_turn(self):
        click = speech_like(0.12)
        signal = np.concatenate([noise(0.5), click, noise(2.0)])
        _, events = self.run_vad(signal, VadConfig(end_silence=0.8, no_speech_timeout=None))
        self.assertNotIn(END, [e.kind for e in events])

    def test_floor_adapts_to_a_noisier_room(self):
        loud_room = noise(4.0, level_db=-40)
        vad, events = self.run_vad(loud_room)
        self.assertEqual(events, [])
        self.assertGreater(vad.floor, -50)
        speech = speech_like(1.2, level=0.4)
        _, events = self.run_vad(np.concatenate([loud_room, speech + noise(1.2, level_db=-40),
                                                 noise(2.0, level_db=-40)]))
        self.assertIn(END, [e.kind for e in events])

    def test_a_fan_switched_on_is_not_a_turn(self):
        quiet, fan = noise(1.0, level_db=-65), noise(6.0, level_db=-35, seed=5)
        vad, events = self.run_vad(np.concatenate([quiet, fan]),
                                   VadConfig(end_silence=1.0, no_speech_timeout=None))
        self.assertNotIn(END, [e.kind for e in events])
        self.assertGreater(vad.noise_rejections, 0)
        self.assertGreater(vad.floor, -45)

    def test_known_floor_skips_calibration(self):
        vad = Endpointer(VadConfig(no_speech_timeout=None), floor=-60.0)
        self.assertFalse(vad.calibrating)
        events = vad.feed(speech_like(0.5))
        self.assertEqual([e.kind for e in events], [START])

    def test_long_turn_is_cut_at_the_limit(self):
        signal = np.concatenate([noise(0.3), speech_like(4.0)])
        _, events = self.run_vad(signal, VadConfig(max_utterance=2.0, no_speech_timeout=None))
        self.assertEqual(events[-1].kind, END)
        self.assertTrue(events[-1].truncated)

    def test_force_end_returns_audio_so_far(self):
        vad = Endpointer(VadConfig(no_speech_timeout=None))
        vad.feed(np.concatenate([noise(0.3), speech_like(0.8)]))
        self.assertTrue(vad.in_speech)
        event = vad.force_end()
        self.assertEqual(event.kind, END)
        self.assertIsNone(vad.force_end())


class FakeArecord:
    """Serves stereo 48 kHz PCM like arecord's stdout, then blocks or ends."""

    def __init__(self, mono16k_signal=None, end=False, error=b""):
        if mono16k_signal is None:
            mono16k_signal = noise(0.5)
        # upsample by repetition for the fake (the decimator filters it)
        mono48 = np.repeat(mono16k_signal, 3)
        stereo = np.empty(mono48.size * 2)
        stereo[0::2] = mono48
        stereo[1::2] = mono48
        self.data = io.BytesIO(dsp.to_pcm16(stereo))
        self.end = end
        self.closed = threading.Event()
        self.returncode = None
        self.error_file = io.BytesIO(error)
        outer = self

        class Stream:
            def read(self, n):
                piece = outer.data.read(n)
                if piece:
                    return piece
                if outer.end:
                    outer.returncode = 1
                    return b""
                outer.closed.wait(5)
                return b""

        self.stdout = Stream()

    def terminate(self):
        self.closed.set()

    def kill(self):
        self.closed.set()

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return self.returncode


class CaptureTest(unittest.TestCase):
    def wait_for(self, predicate, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_turn_is_detected_through_the_stream_with_the_gate_open(self):
        signal = np.concatenate([noise(0.6), speech_like(1.0), noise(2.0)])
        events = []
        audio = capture.AudioInput("fake", lambda k, t, p: events.append((k, t, p)),
                                   spawn=lambda device: FakeArecord(signal))
        audio.set_gate(True)
        audio.listen(7, VadConfig(end_silence=0.8, no_speech_timeout=None))
        self.assertTrue(audio.start())
        self.assertTrue(self.wait_for(lambda: any(e[0] == capture.UTTERANCE for e in events)))
        audio.stop()
        kinds = [e[0] for e in events]
        self.assertEqual(kinds[:2], [capture.SPEECH_START, capture.UTTERANCE])
        self.assertTrue(all(e[1] == 7 for e in events))

    def test_closed_gate_hears_nothing(self):
        signal = np.concatenate([noise(0.3), speech_like(1.0), noise(1.5)])
        events = []
        fake = FakeArecord(signal)
        audio = capture.AudioInput("fake", lambda k, t, p: events.append(k), spawn=lambda d: fake)
        audio.listen(1, VadConfig(end_silence=0.5, no_speech_timeout=None))
        audio.start()
        self.assertTrue(self.wait_for(lambda: fake.data.tell() == len(fake.data.getvalue())))
        time.sleep(0.05)
        audio.stop()
        self.assertEqual(events, [])

    def test_hold_returns_everything_held(self):
        fake = FakeArecord(speech_like(1.0))
        audio = capture.AudioInput("fake", lambda *a: None, spawn=lambda d: fake)
        audio.set_gate(True)
        audio.hold_start(3)
        audio.start()
        self.assertTrue(self.wait_for(lambda: fake.data.tell() == len(fake.data.getvalue())))
        time.sleep(0.05)
        pcm = audio.hold_end()
        audio.stop()
        self.assertGreater(dsp.seconds_of(pcm, 16000), 0.9)

    def test_stream_that_ends_by_itself_reports_why(self):
        events = []
        fake = FakeArecord(noise(0.1), end=True,
                           error=b"arecord: main:830: audio open error: Device or resource busy")
        audio = capture.AudioInput("plughw:9", lambda k, t, p: events.append((k, p)),
                                   spawn=lambda d: fake)
        audio.start()
        self.assertTrue(self.wait_for(lambda: events))
        self.assertEqual(events[0][0], capture.CAPTURE_ERROR)
        self.assertIn("busy", events[0][1])
        self.assertFalse(audio.running)

    def test_no_device_does_not_start(self):
        audio = capture.AudioInput(None, lambda *a: None)
        self.assertFalse(audio.start())
        self.assertEqual(audio.error, "no microphone")


class FakeAplay:
    def __init__(self, code=0):
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        self.stderr = io.BytesIO(b"")
        self.returncode = None
        self.code = code
        self.killed = False

    def wait(self, timeout=None):
        self.returncode = self.code
        return self.code

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class PlaybackTest(unittest.TestCase):
    def test_streams_and_upsamples_speech(self):
        fake = FakeAplay()
        out = playback.AudioOutput("fake", spawn=lambda device, rate: fake)
        pcm = dsp.to_pcm16(tone_db(0.5, 440, 24000, -12))
        chunks = [pcm[i:i + 4801] for i in range(0, len(pcm), 4801)]   # odd sizes
        started = []
        result = out.play(iter(chunks), 24000, keep=True, on_audio=lambda: started.append(1))
        self.assertTrue(result.completed)
        self.assertEqual(started, [1])
        self.assertAlmostEqual(result.seconds, 0.5, places=2)
        self.assertEqual(result.audio, pcm)
        self.assertEqual(len(fake.stdin.getvalue()), len(pcm) * 2)

    def test_stop_cuts_the_clip(self):
        fake = FakeAplay()
        out = playback.AudioOutput("fake", spawn=lambda device, rate: fake)

        def chunks():
            yield b"\x00" * 4800
            out.stop()
            yield b"\x00" * 4800

        result = out.play(chunks(), 24000)
        self.assertFalse(result.completed)
        self.assertTrue(fake.killed)

    def test_failing_source_is_reraised_after_cleanup(self):
        fake = FakeAplay()
        out = playback.AudioOutput("fake", spawn=lambda device, rate: fake)

        def chunks():
            yield b"\x00" * 4800
            raise RuntimeError("network gone")

        with self.assertRaises(RuntimeError):
            out.play(chunks(), 24000)
        self.assertFalse(out.playing)

    def test_aplay_error_is_reported(self):
        fake = FakeAplay(code=1)
        fake.stderr = io.BytesIO(b"aplay: main:831: audio open error: No such file or directory")
        out = playback.AudioOutput("fake", spawn=lambda device, rate: fake)
        result = out.play(iter([b"\x00" * 100]), 24000)
        self.assertFalse(result.completed)
        self.assertIn("audio open error", result.error)


class DevicesTest(unittest.TestCase):
    def test_prefers_the_shared_whisplay_devices(self):
        cards = [(2, "ahubhdmi"), (3, "whisplaysound")]
        self.assertEqual(devices.resolve("capture", "auto", cards, {"capture", "playback"})[0], "capture")
        self.assertEqual(devices.resolve("playback", "auto", cards, {"playback"})[0], "playback")

    def test_whisplay_card_directly_without_shared_devices(self):
        cards = [(2, "ahubhdmi"), (3, "whisplaysound")]
        self.assertEqual(devices.resolve("capture", "auto", cards, set())[0], "plughw:3")

    def test_hdmi_is_never_the_microphone(self):
        device, why = devices.resolve("capture", "auto", [(2, "ahubhdmi")], set(),
                                      driver_dir="/nonexistent")
        self.assertIsNone(device)

    def test_missing_whisplay_card_is_named(self):
        device, why = devices.resolve("capture", "auto", [(0, "Headset")], set(), driver_dir="/")
        self.assertIsNone(device)
        self.assertIn("Whisplay", why)

    def test_configured_device_wins(self):
        self.assertEqual(devices.resolve("capture", "plughw:1")[0], "plughw:1")


if __name__ == "__main__":
    unittest.main()
