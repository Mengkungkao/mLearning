# Testing

```bash
bash tools/check.sh          # what CI runs
python3 -m unittest discover -s tests -t .
```

`tools/check.sh` runs: Python 3.9 syntax and no run-time `X | Y`; shell
syntax; trailing whitespace; the unit and integration suite; the package
smoke test (`test.sh`); mFruit OS's package preflight (`check-app.py`,
needs an mFruit OS checkout in `MFRUIT_OS`, default `~/MFruitOS`); the
vendored SDK and app contract copies; Markdown links.

| Tests | What |
|---|---|
| `test_state_machine` | allowed transitions, no dead ends, deadlines |
| `test_audio` | channel mix, rate conversion (no aliasing, continuous across chunks), voice detection (patience, clicks, steady noise, loudness, the **measured opi2w room** fixture `tests/data/opi2w_room_levels.json`, levels only), capture and playback through fake `arecord`/`aplay`, device choice |
| `test_teacher` | schema strictness, validation and ranking, speech cleaning, commands, practice comparison, prompts, session history |
| `test_providers` | OpenAI adapters against a local fake server (multipart upload, streamed audio, retries, error kinds, no key in errors); Claude adapter request shape with a fake SDK client; provider choice |
| `test_storage` | settings validation and recovery, keys file, profile and lesson record, deletion stays inside |
| `test_engine` | whole lessons with fake providers and audio: hands-free and hold to talk, commands, practice, retry of the failed step, watchdog, summary, shutdown, stale results, and one run on the real threads |
| `test_device` | the SDK's real InputController (unthreaded, fake clock) on every screen; advertised gestures do what they say, others do nothing; nothing acts without the screen; every screen inside the chrome |
| `test_terminal` | the terminal lesson on the real engine thread |

Regression tests with demonstrated negative controls (they fail on the code
before the fix): the measured-room end of turn (12 s uploads on opi2w), the
terminal starting before providers were loaded, the screen-bounds check.

## On a device

```bash
./run.sh --diagnose               # cards, chosen devices, microphone level per channel, keys, network
./run.sh --diagnose --loopback    # speaker -> microphone -> voice detection, without a person
./run.sh --diagnose --online      # one tiny request to each provider (costs a fraction of a cent)
./run.sh --preview DIR            # every screen as PNG
```

Under mFruit OS: `mfruitctl sideload <package>`, `mfruitctl launch mlearning`,
`mfruitctl key down|enter|escape`; the app's screen is in
`/tmp/whisplay-fb-mlearning-*.bin` (240x280 big-endian RGB565). Records of
device checks: [records/](records/). What is and is not verified:
[HARDWARE.md](HARDWARE.md).
