# Architecture

mLearning is an mFruit OS app in its own repository. mFruit OS launches it
(ApplicationManager → whisplay-daemon → `mfruit-run` → `run.sh`); nothing in
mFruit OS was changed for it.

## Layers

```text
Device interface   app/device.py, app/ui/screens.py    LCD screens, button + keys (mFruit SDK)
Terminal interface app/terminal.py                      typed lessons, --voice
        │ requests (post)          ▲ View (immutable snapshot)
        ▼                          │
Engine             app/engine.py + app/state_machine.py   one thread owns the lesson
        │                                   │
Teacher            app/teacher/             prompts, answer schema + validation,
                                            activities, practice comparison, commands
Learning record    app/learning/            profile + storage (local, owner-only)
        │
Interfaces         SpeechRecognitionProvider / LanguageModelProvider /
                   TextToSpeechProvider (app/providers/base.py)
                   AudioInput / AudioOutput (app/audio/)  ·  display: show(image), has_focus
        │
Implementations    providers/openai.py (stdlib HTTP), providers/anthropic_llm.py (SDK, optional),
                   providers/fake.py; audio/capture.py (arecord), audio/playback.py (aplay);
                   app/whisplay.py (whisplay-daemon framebuffer and events)
```

The engine never imports the device interface; the teacher code never
touches audio or the network; providers know nothing about lessons. A
different display (HDMI, a desktop window) needs only an object with
`show(image)` and `has_focus`; a different speech engine only a new
provider class registered in `providers/registry.py`.

## One owner per responsibility

| Responsibility | Owner |
|---|---|
| Lesson lifecycle, turn order, retries | `Engine` (engine thread only) |
| Allowed state changes and deadlines | `StateMachine` |
| Microphone stream, gate, turn detection | `AudioInput` + `vad.Endpointer` |
| Speaker | `AudioOutput` |
| What the teacher is told | `teacher/prompts.py` |
| Whether an answer is usable | `teacher/schema.parse_turn` |
| Settings values and their validation | `config.py` (written only by the engine) |
| Learning record on disk | `learning/storage.py` (written only by the engine) |
| whisplay-daemon traffic | `app/whisplay.py` (plus the SDK's `own_escape_key`) |
| Screens and navigation | `DeviceUI` |

## Threads

- **engine**: handles every event in order (requests from the screen,
  worker results, timers) and is the only thread that changes the lesson,
  settings or learning record. After each event it publishes a new `View`.
- **workers**: one short-lived thread per request (speech recognition, the
  teacher, the voice, a cue). They call a provider and post the result with
  the turn id they were started for.
- **capture**: reads `arecord`, converts to 16 kHz mono, runs the turn
  detector, posts speech start / turn / timeout / stream errors.
- **render**: draws the LCD when woken (a new `View`, input) or on a short
  timer while something moves (meter, spinner, speaking progress).
- **SDK input threads**: button gestures and keys; handlers change
  navigation under a lock and post requests to the engine.

## Turn ids: no overlapping voices, no stale answers

Every result carries the turn it belongs to. The engine starts a new turn
whenever the learner interrupts (hold while the teacher speaks), pauses,
retries, leaves or ends the lesson, and drops results for other turns. A slow
answer to a question the learner already abandoned is never spoken; a voice
from a previous turn is stopped (`AudioOutput.stop`) before a new one starts.

## The state machine

```text
INITIALISING → IDLE ⇄ LISTENING → PROCESSING_SPEECH → GENERATING_RESPONSE → SPEAKING
                 ▲        ▲  │                                                 │
                 │        │  └→ (voice command) → SPEAKING                      │
                 │        └──────────── PRACTICE_WAITING ◄──── (practice target)┤
                 │                                                             │
                 └──────────────── hold-to-talk / no speech ◄──────────────────┘
any state → PAUSED (tap, focus lost) · ERROR (Retry / Details / Back) · SHUTTING_DOWN
```

The full table is `TRANSITIONS` in `app/state_machine.py`; a refused
transition changes nothing and is logged. Deadlines: recognising 60 s,
teacher 75 s, speaking 20 s + text length; listening is bounded by the
no-speech timeout (30 s) and the longest turn (60 s). The watchdog turns an
overdue state into ERROR, so a hung request or a silent device can never
leave the lesson stuck. Retry repeats exactly the failed step (the same
recording, the same learner turn), so a turn is never recorded twice.

## A turn, hands-free

1. The teacher's voice finishes (`aplay` has drained). The engine arms the
   turn detector, plays the "listening" cue, and opens the microphone gate
   0.3 s later (room echo and buffer tail; acoustic, not a race fix).
2. The detector hears speech start, then 1.3 s of quiet (the learner's
   *pause before reply* setting), and hands over the turn with 0.4 s of
   audio from before the start.
3. Speech recognition (remote) → transcript with confidence. Empty or
   "nobody spoke" results are dropped and listening resumes. Short commands
   ("repeat that") are handled on the device.
4. The teacher receives the transcript marked as speech (with recognition
   confidence, and the practice comparison if a sentence was being
   practised) and answers in strict JSON (`TEACHER_SCHEMA`).
5. The answer is validated (`parse_turn`): corrections ranked meaning →
   grammar → fluency and capped (1/2/3), duplicates and no-op corrections
   dropped, text trimmed and cleaned for speech.
6. The spoken reply streams from the voice provider to `aplay` while it
   downloads; the screen shows the text, then the correction card.

## Echo and interruption

The microphone stream stays open during a lesson (the WM8960 takes ~0.7 s to
power up), but a gate discards everything while the teacher speaks, so the
teacher never hears itself. There is no acoustic echo cancellation on this
hardware path, so interrupting is by button (hold), not by voice.

## Providers

`registry.build` chooses providers from the settings and the keys present;
it never switches provider by itself. OpenAI adapters use only the standard
library (`urllib`), stream the voice (`response_format: pcm`, 24 kHz) and map
every failure to a `ProviderError` kind with a user-facing explanation. The
Claude adapter uses the official Anthropic SDK (`client.beta.messages.create`
with structured output, low effort, and server-side refusal fallback) and is
imported only when chosen.

## Data

Code: the version folder mFruit OS replaces on update. Data
(`WHISPLAY_OS_APP_DATA`, snapshotted by mFruit OS before updates):
`.env` (keys), `settings.json`, `learning/` (profile, lesson summaries,
optional transcripts). All owner-only, written atomically; unreadable files
are kept aside, never overwritten.
