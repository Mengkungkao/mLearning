# mLearning

A voice-first English teacher for [mFruit OS](https://github.com/Mengkungkao/MFruitOS)
devices (Orange Pi Zero 2W or Raspberry Pi Zero 2 W with a Whisplay HAT).
You talk; the teacher listens, answers naturally, corrects what matters,
and gets you to practise the better version. The small screen shows what is
happening and the correction; the button controls everything.

- **Talk with your teacher**: free conversation with gentle corrections.
- **Daily English**: 16 role-plays (café, shop, directions, a doctor's
  receptionist, your manager, a colleague, misunderstandings…).
- **Job interview**: an interviewer asks one question at a time and gives
  feedback (did you answer the question, STAR structure, language), with a
  stronger version of *your* answer. It never invents experience.
- **Listening practice**: listen and retell, listen and repeat (your words
  compared with the sentence), real-world announcements, useful expressions.
- **Ask about English**: words, idioms, phrasal verbs, grammar questions.
- **My phrases** and **Progress**: what you learned, your recurring mistakes,
  minutes per day against your goal.

Status: version 0.1.0. See [docs/HARDWARE.md](docs/HARDWARE.md) for what has
been checked on a device and what has not.

## What it needs

| | |
|---|---|
| Device | mFruit OS 1.4.0 or newer; Whisplay HAT microphone and speaker |
| Software | Python 3.9+, NumPy, Pillow, `arecord`/`aplay` (all present on mFruit OS devices) |
| Teacher | an **OpenAI API key** (speech recognition, the teacher, the voice). Optional: Claude as the teacher, with an Anthropic key |
| Network | Wi-Fi while you practise; the device works without it, but the teacher does not |

mLearning starts without a key and tells you what is missing; nothing is sent
anywhere until a key is set.

## Install

On the device, from the Fruit Store (when listed), or sideload a package:

```bash
mfruitctl sideload mlearning-0.1.0.tar.gz
```

Then add your key (the install created the file, readable only by you):

```bash
nano ~/.whisplay-os/apps/mlearning/data/.env      # OPENAI_API_KEY=sk-...
```

Open **mLearning** from Home. If you added the key while it was open, choose
**Teacher setup > Retry**.

Running it without mFruit OS (a desktop, over SSH): `./install.sh`, put the key
in `~/.local/share/mlearning/.env`, then `./run.sh --terminal`.

## Using it

| | Button | Keyboard |
|---|---|---|
| Move in lists | tap next · 2× previous | ↓ ↑ |
| Open, change a setting | hold, then release | Enter |
| Back (on Home: leave) | 4× | Esc |

**In a lesson** (the footer always shows what each gesture does now):

| | Button | Keyboard |
|---|---|---|
| Talk | hold while you speak (also interrupts the teacher) | hold Space |
| Pause / resume listening, microphone off | tap | |
| Repeat what the teacher said | 2× | |
| Lesson menu: slower, faster, notes, listening mode, end | 3× | Enter |
| End the lesson (with a summary) | 4× | Esc |
| Type instead of speaking | | type, then Enter |

By default the lesson is **hands-free**: after the teacher speaks, a soft blip
means it is listening, and a pause of 1.3 s ends your turn (Settings > Pause
before reply: short, normal or long). Settings > Listening > Hold to talk
records only while you hold the button.

You can also just say: "repeat that", "speak more slowly", "faster", "pause",
"let's practise interviews", "let's finish the lesson".

## Privacy

Voice detection runs on the device; nothing is sent while nobody speaks. Each
finished sentence goes to OpenAI for recognition, and its text (not the
recording) to the teacher. Recordings are never saved. Saved on the device
only: lesson summaries, your phrases, recurring mistake types with short
examples, minutes. Settings > Privacy and data explains this on the device
and deletes it all. Details: [docs/PRIVACY.md](docs/PRIVACY.md).

## For developers

```bash
bash tools/check.sh                       # everything CI runs (141 tests, mFruit OS preflight)
./run.sh --preview /tmp/screens           # every LCD screen as PNG
./run.sh --terminal                       # a typed lesson (needs a key)
./run.sh --diagnose --loopback            # on a device: audio path check without speaking
bash tools/build-release.sh               # dist/mlearning-<version>.tar.gz
```

- [Architecture](docs/ARCHITECTURE.md): layers, threads, the state machine, providers.
- [Configuration](docs/CONFIGURATION.md): every setting, keys, Claude, devices.
- [Hardware](docs/HARDWARE.md): verified, inferred and untested hardware facts.
- [Testing](docs/TESTING.md) and [Troubleshooting](docs/TROUBLESHOOTING.md).
- [CONTINUE.md](CONTINUE.md): current hand-off. [CHANGELOG.md](CHANGELOG.md).

The app follows the mFruit OS app contract
([.claude/rules/mfruit-os-app.md](.claude/rules/mfruit-os-app.md)) and vendors the
mFruit App SDK in `mfruit_sdk/` (do not edit it here).
