# CONTINUE — mLearning hand-off

Started 2026-10-24 at the owner's request: build **mLearning**, a voice-first
English teacher, in `/home/meng/mLearning` as an mFruit OS app (id
`mlearning`), and install it on opi2w (`ssh orangepi@192.168.0.248`).
Commits: no co-author or agent lines (owner's rule).

## Owner decisions (2026-10-24)

- Teacher AI: **OpenAI gpt-4o** (Claude stays switchable in Settings; it needs
  the anthropic package, installed only when chosen).
- Keys: the owner adds them to mLearning's own `.env`; the AI Chatbot's keys
  on the device were **not** copied.

## State

| # | Step | Status |
|---|---|---|
| 1 | Hardware survey of opi2w (Ubuntu 22.04, Python 3.10, NumPy 1.21, Pillow 9.0.1, 981 MB, Whisplay WM8960, shared `capture`/`playback` ALSA devices, no local speech engines) | DONE (read-only probes) |
| 2 | Core: state machine, audio (capture, VAD, playback), providers, teacher, learning record, engine | IMPLEMENTED, AUTOMATED |
| 3 | LCD interface (mFruit SDK controls and chrome), terminal mode, diagnostics, previews, install/test hooks, icon | IMPLEMENTED, AUTOMATED |
| 4 | Install on opi2w (sideload), launch, screens, loopback audio check | next |
| 5 | Full voice lesson on the device | NOT VERIFIED until the owner adds an OpenAI key |

`bash tools/check.sh` = CI (141 tests, mFruit OS preflight, SDK parity).
