# CONTINUE — mLearning hand-off

Started 2026-10-24 at the owner's request: build **mLearning**, a voice-first
English teacher, in `/home/meng/mLearning` as an mFruit OS app (id
`mlearning`), and install it on opi2w (`ssh orangepi@192.168.0.248`).
Commits: dot-point bodies, **no co-author or agent lines** (owner's rule).
No GitHub remote yet (manifest names `github.com/Mengkungkao/mLearning`).

## Owner decisions (2026-10-24)

- Teacher AI: **OpenAI gpt-4o**. Claude stays switchable in Settings; it needs
  the anthropic package, installed only when chosen.
- Keys: the owner adds them to mLearning's own `.env`
  (`~/.whisplay-os/apps/mlearning/data/.env` on the device). The AI
  Chatbot's keys were **not** copied.

## State

| # | Step | Status |
|---|---|---|
| 1 | Hardware survey of opi2w | DONE ([HARDWARE.md](docs/HARDWARE.md)) |
| 2 | Core: state machine, audio, voice detection, providers, teacher, learning record, engine | IMPLEMENTED, AUTOMATED |
| 3 | LCD interface, terminal mode, diagnostics, previews, hooks, docs | IMPLEMENTED, AUTOMATED |
| 4 | Installed on opi2w (0.1.0, version `0.1.0-c23845`), launch, screens, Esc exit, update keeps `.env` | DEVICE VERIFIED ([record](docs/records/2026-10-24-opi2w-first-install.md)) |
| 5 | Audio path on opi2w: loopback, echo tail, resources, full pipeline against a local stand-in server | DEVICE VERIFIED (not with OpenAI, not with a human voice) |
| 6 | A real lesson: OpenAI + a person speaking + the physical button | NOT VERIFIED: waits for the owner's key |

`bash tools/check.sh` = CI (149 tests, mFruit OS preflight, SDK parity, links).

## Next

1. Owner: put `OPENAI_API_KEY=...` in `~/.whisplay-os/apps/mlearning/data/.env`
   on opi2w, open mLearning > Talk with your teacher. Optional first check:
   `cd ~/.whisplay-os/apps/mlearning/current && ./run.sh --diagnose --online`.
2. With a person speaking: check that Normal Mic sensitivity hears the voice
   and how often room sounds start a turn (opi2w's room was noisy: floor
   about -46 dBFS, sounds 8-15 dB above every few seconds); the capture log
   prints levels per turn. Adjust the defaults in `config.SENSITIVITY_DB` if needed.
3. Measure real latency (turn end → teacher's voice) with OpenAI; consider
   `gpt-4o-mini` or streaming the first sentence if it feels slow.
4. The device still shows the longer empty-phrases hint (fixed in source,
   next install).
5. Later (PLANNED, not started): offline speech (Piper voice, Vosk
   recognition) behind the existing provider interfaces; publishing (GitHub
   repo, release workflow, Fruit Store entry).
6. `~/mlearning-dev/` on opi2w holds test scripts and outputs; safe to delete.
