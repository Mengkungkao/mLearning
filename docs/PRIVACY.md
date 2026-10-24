# Privacy

mLearning listens through a microphone in someone's home, so it is careful
about what it hears, sends and keeps. The device's Settings > Privacy and
data screen says the same in short.

## Listening

- Voice detection runs **on the device**. Nothing is sent while nobody speaks,
  and steady noise or quiet room sounds are dropped before anything is sent.
- The microphone is open only during a lesson. The screen shows "Mic on" or
  "Mic off". A tap pauses the lesson and turns the microphone off; leaving
  the lesson, leaving the screen and 30 s without speech also turn it off.
- While the teacher speaks the microphone's audio is thrown away.

## What leaves the device

| What | To | When |
|---|---|---|
| each finished turn (audio, 16 kHz WAV) | OpenAI speech recognition | after you stop speaking |
| the text of your turn, the lesson so far and your profile notes | the teacher (OpenAI or Anthropic) | each turn |
| the teacher's reply text | OpenAI voice | each turn |

Profile notes sent to the teacher: your working level (if known), your three
most frequent mistake types with one short example each, your five most
recent phrases and the last lesson's suggested exercise. No names, no
recordings. The providers' own policies apply to what they receive.

## What is kept on the device

| | Kept | |
|---|---|---|
| recordings | **never** | turns live in memory until recognised |
| conversation transcripts | only with Keep transcripts = 30 days | deleted after 30 days |
| lesson summaries | yes | `learning/lessons/` |
| phrases, mistake types (3 short examples each), minutes per day, level estimates | yes | `learning/profile.json` |
| keys | yes | `.env`, owner-only |

All files are owner-only (folders 0700, files 0600). Logs record states,
timings and errors, not what was said.

**Delete**: Settings > Privacy and data > Delete learning history removes the
whole `learning/` folder (settings and keys stay). Uninstalling mLearning in
mFruit OS keeps the data folder; `mfruitctl delete mlearning` removes it.
