"""mLearning's entry point.

    run.sh                       on the device (mFruit OS starts it): LCD, button, voice
    run.sh --terminal            a typed lesson in the terminal (needs a teacher key)
    run.sh --terminal --voice    the full voice lesson, with the terminal as the screen
    run.sh --diagnose [--loopback] [--online]   hardware and setup report
    run.sh --preview DIR         every LCD screen as PNG files
    run.sh --selftest            the install smoke test (no screen, network or audio)
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

from . import VERSION, config, envfile, paths
from .audio import devices
from .audio.capture import AudioInput
from .audio.playback import AudioOutput
from .engine import Engine
from .learning.storage import LearningStorage

log = logging.getLogger("mlearning")

EXIT_NO_DAEMON = 2


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)


def build_engine(text_only: bool = False, threaded: bool = True, with_audio: bool = True) -> Engine:
    data = paths.ensure_data_dir()
    template = os.path.join(paths.CODE_DIR, ".env.example")
    if os.path.exists(template):
        try:
            if envfile.create_from_template(template, paths.env_file()):
                log.info("created %s from the template (add your keys there)", paths.env_file())
        except OSError as exc:
            log.warning("could not create %s: %s", paths.env_file(), exc)
    settings = config.load(paths.settings_file())
    secrets = envfile.Secrets(paths.env_file())
    storage = LearningStorage(paths.learning_dir())
    audio_in = audio_out = None
    holder = {}
    if with_audio and not text_only:
        capture, why_capture = devices.resolve("capture", settings["capture_device"])
        playback, why_playback = devices.resolve("playback", settings["playback_device"])
        log.info("microphone: %s (%s); speaker: %s (%s)", capture, why_capture, playback, why_playback)
        audio_in = AudioInput(capture, lambda kind, turn, payload: holder["engine"].on_capture(kind, turn, payload))
        if capture is None:
            audio_in.error = why_capture
        audio_out = AudioOutput(playback)
    engine = Engine(settings, secrets, storage, audio_in, audio_out, threaded=threaded,
                    text_only=text_only)
    holder["engine"] = engine
    log.info("mLearning %s, data in %s", VERSION, data)
    return engine


def run_device() -> int:
    from .device import DeviceUI

    engine = build_engine()
    ui = DeviceUI(engine)
    engine.start()
    if not ui.start():
        engine.stop()
        if not paths.under_mfruit_os():
            print("whisplay-daemon is not running: no LCD to draw on.\n"
                  "Try a lesson in the terminal instead:  ./run.sh --terminal", file=sys.stderr)
        return EXIT_NO_DAEMON

    def leave(_signum, _frame):
        ui.request_exit()

    signal.signal(signal.SIGTERM, leave)
    signal.signal(signal.SIGINT, leave)
    ui.exit_requested.wait()
    log.info("leaving")
    # app_exit_requested must be answered within 3 seconds: stop sound, save, release.
    done = threading.Event()

    def stop_engine():
        engine.stop(timeout=1.8)
        done.set()

    threading.Thread(target=stop_engine, daemon=True).start()
    done.wait(2.0)
    if engine.audio_in is not None:
        engine.audio_in.stop()
    ui.stop()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mlearning", description="mLearning, your English teacher")
    parser.add_argument("--terminal", action="store_true", help="a lesson in the terminal")
    parser.add_argument("--voice", action="store_true", help="with --terminal: speak and listen")
    parser.add_argument("--activity", default="talk", help="with --terminal: activity id")
    parser.add_argument("--diagnose", action="store_true", help="hardware and setup report")
    parser.add_argument("--loopback", action="store_true",
                        help="with --diagnose: play a test sound and check the microphone hears it")
    parser.add_argument("--online", action="store_true",
                        help="with --diagnose: one tiny request to each provider (costs a little)")
    parser.add_argument("--preview", metavar="DIR", help="render every screen to PNG files")
    parser.add_argument("--selftest", action="store_true", help="install smoke test")
    parser.add_argument("--version", action="version", version=f"mLearning {VERSION}")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        from . import selftest
        return selftest.main()
    if args.preview:
        from . import preview
        return preview.main(args.preview)
    setup_logging(args.verbose)
    if args.diagnose:
        from . import diagnostics
        return diagnostics.main(loopback=args.loopback, online=args.online)
    if args.terminal:
        from . import terminal
        return terminal.main(args.activity, voice=args.voice)
    return run_device()


if __name__ == "__main__":
    sys.exit(main())
