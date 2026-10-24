# 2026-10-24 — first install and device checks on opi2w

| | |
|---|---|
| Date | 2026-10-24, 17:25-18:33 (device clock) |
| Revision | `d72cc12` (first install), `4477f34` (installed at the end; same package as the record's commit except docs and one shorter LCD hint) |
| Package | `mlearning-0.1.0.tar.gz`, SHA-256 `8f52aaab…c7ef` (first), `1481c66a…f464f` (second) |
| Device | opi2w, `orangepi@192.168.0.248`: Orange Pi Zero 2W, Whisplay HAT (WM8960), PiSugar 3 |
| OS | Ubuntu 22.04.5, kernel 6.1.31-sun50iw9, Python 3.10.12, NumPy 1.21.5, Pillow 9.0.1, mFruit OS 1.6.2, whisplay-daemon as installed by mFruit OS |
| Keys | none on the device (the owner adds them); AI Chatbot's keys were deliberately not copied |

## Commands and results

| Check | Command | Result |
|---|---|---|
| Survey | read-only `ssh` probes (cards, tools, memory, mFruit state) | Whisplay card 3, shared `capture`/`playback` in `/etc/asound.conf`; sox, arecord, aplay, mpg123; no espeak/piper/vosk |
| Install | `mfruitctl sideload mlearning-0.1.0.tar.gz` | installed; install.sh created `data/.env` (0600); test.sh "selftest ok (26 modules, 27 screens, scripted lesson)" |
| Launch | `mfruitctl launch mlearning` | first frame ~0.1 s after start (log 18:12:34.53 → 18:12:34.65); devices `capture`/`playback`; "teacher=False, OpenAI key missing" |
| Screens | `mfruitctl key down/enter/escape`, framebuffer `/tmp/whisplay-fb-mlearning-*.bin` | home (Needs setup), Setup, Settings, My phrases, Progress drawn as in the previews, with Wi-Fi and battery |
| Diagnose | `run.sh --diagnose --loopback` | microphone 2 s: peaks L -12 / R -90 dBFS; loopback: speaker 1.6 s, room -49 / loudest -17 dBFS, detected a 2.2 s turn (1.6 s loud) |
| Echo tail | `echo_tail.py` (3 trials) | microphone at room level within 10 ms of `aplay` exiting |
| Resources | `perf.py` | listening 11.6 % of one core (python) + 0.9 % (arecord), 29 MB; lesson frame render + RGB565 11 ms; 52 MB after rendering |
| Room noise | `noise_levels.py`, 11 s, levels only | median -45.7, p90 -40.5, p95 -37.3, p99 -27.7 dBFS; two sound events up to -23 dBFS |
| Pipeline | `pipeline_test.sh`: `run.sh --terminal --voice` with `OPENAI_BASE_URL` → local stand-in server; test sound via `aplay` as the learner | see below |
| Exit | `mfruitctl key escape` on Home | exit code 0 within 0.1 s; mFruit OS `APP_CLOSED result=exited`; no recorder or player left |
| Update | sideload of the second package | new version folder active, previous kept, `.env` untouched |

## The pipeline test and the bug it found

Run 1 (`d72cc12`): the terminal mode quit at once: it checked for a teacher
before the engine thread had loaded the providers. Fixed (engine `ready`
event), regression test with a negative control.

Run 2: the full chain worked (greeting → streamed voice → listening →
turn → upload → teacher → correction → voice → summary → record saved), but
the uploaded turn was **12.42 s for a 1.8 s sound**. Cause: the noise floor
tracked the quiet end of a room whose noise moves over ~10 dB, so ordinary
noise counted as speech and the 1.3 s pause was never seen. Fixed (median
floor of the last 2 s of quiet, 20 % voiced tolerance in the pause window,
8 dB continuation margin); regression fixture of the measured levels; the
old detector fails it.

Runs 3-5 (fixed detector): the test-sound turn was 2.40 / 2.40 / 2.44 s.
Extra short turns appeared at random times after the teacher's replies;
level logs (numbers only) and a playback-then-listen test show sounds in
the room 8-15 dB above its median every few seconds, not a hardware click.
Added the Mic sensitivity setting (a turn must also reach 12 dB, or 17 dB on
Low, above the room before it is sent).

## Not verified

- A lesson with OpenAI (speech recognition, gpt-4o, voice): no key on the device.
- A human voice: detection sensitivity at Normal/Low in that room; how often room sounds start turns.
- The physical button (gestures were driven by simulated keys and tested in software).
- Claude as the teacher (the anthropic package is not installed on the device).
- The Raspberry Pi Zero 2 W.

## Files on the device

`~/mlearning-dev/` holds the scripts and their outputs (`diagnose-1.txt`,
`echo-tail-1.txt`, `perf-1.txt`, `pipeline-1..5.txt`, `after-play-1.txt`)
and a development copy (`src/`); safe to delete.
