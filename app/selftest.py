"""The install smoke test (``test.sh``): fast, deterministic, offline.

Imports every module, checks the settings table, parses a sample teacher
answer, renders every screen and runs a scripted lesson through the real
engine and device interface with fake providers, fake audio and a fake
board. It never takes the display, opens the microphone or speaker, or
uses the network. Exit code 0 means mLearning can run here.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile

MODULES = ("app.config", "app.envfile", "app.paths", "app.state_machine", "app.engine",
           "app.device", "app.whisplay", "app.audio.dsp", "app.audio.vad", "app.audio.capture",
           "app.audio.playback", "app.audio.devices", "app.providers.openai",
           "app.providers.anthropic_llm", "app.providers.registry", "app.teacher.schema",
           "app.teacher.prompts", "app.teacher.modes", "app.teacher.practice",
           "app.teacher.commands", "app.learning.profile", "app.learning.storage",
           "app.ui.screens", "app.terminal", "app.diagnostics", "app.preview")


def main() -> int:
    import logging
    logging.disable(logging.WARNING)       # the scripted lesson includes a staged error
    for name in MODULES:
        importlib.import_module(name)
    from . import config
    from .teacher.schema import parse_turn
    settings = config.Settings()
    for key, setting in config.SETTINGS.items():
        assert setting.valid(settings[key]), key
    turn = parse_turn({"reply_text": "**Hello!** How are you?", "corrections": []})
    assert turn.spoken == "Hello! How are you?", turn.spoken

    from . import preview
    tmp = tempfile.mkdtemp(prefix="mlearning-selftest-")
    try:
        shots = preview.frames(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert len(shots) >= 20, len(shots)
    for name, image in shots:
        assert image.size == (240, 280), name
    print(f"mlearning: selftest ok ({len(MODULES)} modules, {len(shots)} screens, scripted lesson)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
