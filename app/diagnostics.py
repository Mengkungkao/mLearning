"""``run.sh --diagnose``: what this device has, and whether mLearning can use it.

Reports the board, Python and libraries, sound cards and the devices
mLearning would choose, the microphone's level per channel, whether
whisplay-daemon answers, which keys are set (never their values) and
whether the providers' servers are reachable.

``--loopback`` plays a speech-like test sound through the speaker while the
microphone listens, and checks the turn detector hears it: the capture,
voice detection and playback path, tested without anyone speaking.
``--online`` makes one tiny request to each provider (a few words of
speech and a short answer; it costs a fraction of a cent) and reports the
time each took.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import threading
import time

import numpy as np

from . import VERSION, config, envfile, paths, whisplay
from .audio import devices, dsp
from .audio.capture import UTTERANCE, AudioInput
from .audio.playback import AudioOutput
from .audio.vad import VadConfig


def line(status: str, label: str, detail: str = "") -> None:
    print(f"  {status:<5} {label:<22} {detail}".rstrip(), flush=True)


def _read(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8", "replace").strip("\x00\n ")
    except OSError:
        return ""


def system_section() -> None:
    print("System")
    model = _read("/proc/device-tree/model") or platform.machine()
    line("--", "board", model)
    line("--", "OS", _os_name())
    line("--", "kernel", platform.release())
    line("--", "Python", sys.version.split()[0])
    try:
        import PIL
        line("--", "Pillow / NumPy", f"{PIL.__version__} / {np.__version__}")
    except ImportError:
        line("FAIL", "Pillow", "not installed (python3-pil)")
    memory = _read("/proc/meminfo").splitlines()
    info = {row.split(":")[0]: row.split(":")[1].strip() for row in memory if ":" in row}
    if info:
        line("--", "memory", f"{info.get('MemTotal', '?')} total, {info.get('MemAvailable', '?')} free")
    line("--", "mLearning", f"{VERSION}, data in {paths.data_dir()}")


def _os_name() -> str:
    for row in _read("/etc/os-release").splitlines():
        if row.startswith("PRETTY_NAME="):
            return row.split("=", 1)[1].strip('"')
    return platform.platform()


def audio_section(settings) -> tuple:
    print("Audio")
    for tool in ("arecord", "aplay", "amixer"):
        line("ok" if shutil.which(tool) else "FAIL", tool, shutil.which(tool) or "not installed (alsa-utils)")
    line("--", "capture cards", ", ".join(f"{i}:{n}" for i, n in devices.cards("capture")) or "none")
    line("--", "playback cards", ", ".join(f"{i}:{n}" for i, n in devices.cards("playback")) or "none")
    capture, why_c = devices.resolve("capture", settings["capture_device"])
    playback, why_p = devices.resolve("playback", settings["playback_device"])
    line("ok" if capture else "FAIL", "microphone", f"{capture} ({why_c})" if capture else why_c)
    line("ok" if playback else "FAIL", "speaker", f"{playback} ({why_p})" if playback else why_p)
    for control in ("mic", "speaker"):
        try:
            out = subprocess.run(["amixer", "-c", "whisplaysound", "sget", control],
                                 capture_output=True, text=True, timeout=5).stdout
            level = next((part for part in out.split() if part.startswith("[") and part.endswith("%]")), "")
            if level:
                line("--", f"Whisplay {control} level", level.strip("[]"))
        except (OSError, subprocess.SubprocessError):
            pass
    if capture:
        _channel_check(capture)
    return capture, playback


def _channel_check(device: str) -> None:
    command = ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", "48000", "-c", "2", "-t", "raw",
               "-d", "2"]
    try:
        done = subprocess.run(command, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        line("FAIL", "microphone record", str(exc)[:60])
        return
    if done.returncode != 0 or not done.stdout:
        said = done.stderr.decode("utf-8", "replace").strip().splitlines()
        reason = said[-1] if said else f"arecord exit code {done.returncode}"
        if "busy" in reason.lower():
            who = devices.holder(device)
            reason = f"busy, used by {who}" if who else "busy (another program)"
        line("FAIL", "microphone record", reason[:70])
        return
    left, right = dsp.channel_peaks(done.stdout)
    mono = dsp.Decimator().process(dsp.to_mono(done.stdout))
    room = dsp.rms_dbfs(mono)
    status = "ok" if max(left, right) > -80 else "FAIL"
    line(status, "microphone 2 s", f"peaks L {left:.0f} / R {right:.0f} dBFS, room {room:.0f} dBFS RMS")
    if max(left, right) <= -80:
        line("", "", "no signal at all: check the sound card (mFruit OS Settings > Test microphone)")


def loopback_section(capture: str, playback: str, settings) -> bool:
    print("Loopback (speaker -> microphone -> voice detection)")
    if not capture or not playback:
        line("--", "skipped", "no microphone or speaker")
        return False
    events = []
    heard = threading.Event()

    def on_event(kind, turn, payload):
        events.append((kind, payload))
        if kind == UTTERANCE:
            heard.set()

    audio_in = AudioInput(capture, on_event)
    audio_out = AudioOutput(playback)
    config_vad = VadConfig(start_db=settings["vad_start_db"], end_db=settings["vad_end_db"],
                           min_speech_dbfs=settings["vad_min_speech_dbfs"], end_silence=0.7,
                           no_speech_timeout=15.0)
    audio_in.set_gate(True)
    audio_in.listen(1, config_vad)
    if not audio_in.start():
        line("FAIL", "microphone", audio_in.error)
        return False
    # wait until the stream delivers audio and the room has been measured
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        endpointer = audio_in._endpointer
        if endpointer is not None and endpointer.listening_seconds >= 0.6:
            break
        time.sleep(0.02)
    floor = audio_in.floor
    signal = _test_speech(1.6)
    peak_during = []

    def watch():                    # sample the meter while the sound plays (about 5 s at most)
        while not heard.is_set() and len(peak_during) < 250:
            peak_during.append(audio_in.level)
            time.sleep(0.02)

    watcher = threading.Thread(target=watch, daemon=True)
    started = time.monotonic()
    watcher.start()
    result = audio_out.play_pcm(signal, dsp.HARDWARE_RATE)
    heard.wait(4.0)
    audio_in.stop()
    line("ok" if result.completed else "FAIL", "speaker played", f"{result.seconds:.1f} s"
         + (f" ({result.error})" if result.error else ""))
    loudest = max(peak_during) if peak_during else dsp.SILENCE_DBFS
    line("--", "room / loudest", f"{floor:.0f} / {loudest:.0f} dBFS RMS")
    utterances = [payload for kind, payload in events if kind == UTTERANCE]
    if utterances:
        took = time.monotonic() - started
        line("ok", "voice detection", f"heard a {dsp.seconds_of(utterances[0].pcm, 16000):.1f} s turn "
             f"({utterances[0].speech_seconds:.1f} s loud), ended {took:.1f} s after start")
        return True
    line("FAIL", "voice detection", "the test sound was not detected as a turn")
    return False


def _test_speech(seconds: float) -> bytes:
    rate = dsp.HARDWARE_RATE
    t = np.arange(int(seconds * rate)) / rate
    voice = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate((160, 320, 480, 640, 960, 1280)))
    syllables = 0.08 + 0.92 * np.abs(np.sin(2 * np.pi * 2.5 * t))
    edge = np.minimum(1.0, np.minimum(t / 0.02, (seconds - t) / 0.02))
    return dsp.to_pcm16(voice * syllables * edge * 0.3 * 32767 / 2.0)


def setup_section(settings) -> envfile.Secrets:
    print("Setup")
    secrets = envfile.Secrets(paths.env_file())
    exists = os.path.exists(secrets.path)
    line("ok" if exists else "warn", "keys file", secrets.path if exists else f"{secrets.path} (missing)")
    for name, state in secrets.describe().items():
        line("ok" if state == "set" else "--", name, state)
    line("--", "teacher AI", settings.text("llm_provider"))
    socket_ok = os.path.exists(whisplay.SOCKET_PATH)
    line("ok" if socket_ok else "--", "whisplay-daemon", whisplay.SOCKET_PATH if socket_ok else "not running")
    from .providers import http
    for host in ("api.openai.com", "api.anthropic.com"):
        problem = http.reachable(host)
        line("ok" if problem is None else "FAIL", host, "reachable" if problem is None else problem)
    return secrets


def online_section(settings, secrets) -> None:
    print("Online check (one tiny request each)")
    from .providers import registry
    from .teacher.schema import SUMMARY_SCHEMA
    providers = registry.build(settings, secrets)
    for problem in providers.problems:
        line("FAIL", problem.part, f"{problem.title}: {problem.remedy}")
    audio = b""
    if providers.tts is not None:
        started = time.monotonic()
        try:
            audio = b"".join(providers.tts.stream("Hello, this is your English teacher.",
                                                  settings["voice"], 1.0))
            line("ok", "voice", f"{dsp.seconds_of(audio, providers.tts.rate):.1f} s of speech in "
                 f"{time.monotonic() - started:.1f} s")
        except Exception as exc:
            line("FAIL", "voice", str(exc)[:80])
    if providers.asr is not None and audio:
        started = time.monotonic()
        try:
            result = providers.asr.transcribe(audio, providers.tts.rate)
            line("ok", "speech recognition", f"“{result.text}” in {time.monotonic() - started:.1f} s")
        except Exception as exc:
            line("FAIL", "speech recognition", str(exc)[:80])
    if providers.llm is not None:
        started = time.monotonic()
        try:
            answer = providers.llm.complete_json(
                "Reply with a one-item summary.", [{"role": "user", "content": "Say hello."}],
                SUMMARY_SCHEMA, "check", 300)
            line("ok", "teacher", f"answered in {time.monotonic() - started:.1f} s "
                 f"({len(str(answer))} characters)")
        except Exception as exc:
            line("FAIL", "teacher", str(exc)[:80])


def main(loopback: bool = False, online: bool = False) -> int:
    settings = config.load(paths.settings_file())
    print(f"mLearning diagnostics ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    system_section()
    capture, playback = audio_section(settings)
    secrets = setup_section(settings)
    ok = True
    if loopback:
        ok = loopback_section(capture, playback, settings)
    if online:
        online_section(settings, secrets)
    return 0 if ok else 1
