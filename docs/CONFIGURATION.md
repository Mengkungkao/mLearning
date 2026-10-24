# Configuration

Everything lives in mLearning's data folder, which survives updates:

| | under mFruit OS | on its own |
|---|---|---|
| data folder | `~/.whisplay-os/apps/mlearning/data/` | `~/.local/share/mlearning/` |
| keys | `.env` (owner-only, created by install.sh from `.env.example`) | same |
| settings | `settings.json` (written by the device's Settings screen) | same |
| learning record | `learning/` ([Privacy](PRIVACY.md)) | same |

## Keys (`.env`)

```bash
OPENAI_API_KEY=sk-...                      # speech recognition, the voice, the teacher
# OPENAI_BASE_URL=https://api.openai.com/v1  # an OpenAI-compatible server instead
ANTHROPIC_API_KEY=                         # only when Teacher AI is Claude
```

Environment variables with the same names win over the file. Keys are never
logged or shown; `./run.sh --diagnose` reports only "set" or "missing".
After editing the file while mLearning is open: Settings > Teacher setup >
Retry (or open a lesson again).

## Settings on the device

| Setting | Values (default first) | Meaning |
|---|---|---|
| Listening | Hands-free, Hold to talk | hands-free listens after the teacher speaks; hold records only while held |
| Pause before reply | Normal 1.3 s, Short 0.8 s, Long 2.2 s | how long a silence ends your turn |
| Teacher speed | Normal, Slow, Fast | voice speed 1.0 / 0.8 / 1.2, and simpler language when slow |
| Corrections | Normal (2), Few (1), Many (3) | most corrections in one turn |
| Teaching style | Natural talk, Focused | focused explains and practises the main mistake every time |
| My level | Automatic, Beginner, Intermediate, Advanced | automatic adapts; the record shows a level only after 12 estimates from 2+ lessons |
| English | Australian, British, American | spelling, words and expressions the teacher prefers |
| Teacher voice | Nova, Alloy, Ash, Coral, Echo, Fable, Onyx, Sage, Shimmer | OpenAI voices |
| Lesson length | 15 min, 10, 20, 30, No limit | the teacher suggests finishing after this |
| Mic sensitivity | Normal, Low, High | Low for noisy rooms (talk closer); start/turn thresholds 10/12, 14/17, 7/9 dB above the room |
| Sounds | On, Off | the blip when listening starts |
| Keep transcripts | Off, 30 days | off: only summaries and progress are saved |
| Teacher AI | OpenAI, Claude | see below |
| Daily goal | 10 min, 5, 15, 20, 30 | the Progress screen's bar |

Interview (Job interview screen): job (presets, or type one; empty = the
interviewer asks), type (general, behavioural, technical, mixed),
difficulty, feedback (gentle, standard, thorough).

## Advanced (`settings.json` only)

| Key | Default | |
|---|---|---|
| `openai_asr_model` | `whisper-1` | `verbose_json` confidence is used with whisper models |
| `openai_llm_model` | `gpt-4o` | needs JSON-schema output; compatible servers fall back to JSON mode |
| `openai_tts_model` | `tts-1-hd` | `gpt-4o-mini-tts` also accepts delivery instructions |
| `anthropic_model` | `claude-opus-5-5` | |
| `capture_device`, `playback_device` | `auto` | or an ALSA name such as `plughw:3` |
| `vad_end_db` | 8.0 | how far above the room speech must stay to continue a turn |
| `vad_min_speech_dbfs` | -52 | quieter frames never count as speech |
| `no_speech_timeout` | 30 s | hands-free listening stops (microphone off) after this much silence |
| `max_utterance` | 60 s | the longest turn |
| `request_timeout` | 30 s | per network request |

Invalid values fall back to their default (logged); an unreadable file is
kept as `settings.json.broken-<time>` and defaults are used.

## Claude as the teacher

Anthropic has no speech service, so speech recognition and the voice stay
with OpenAI; only the teacher changes. It needs the `anthropic` Python
package, installed into a venv in the data folder:

1. Add `ANTHROPIC_API_KEY` to `.env`.
2. Settings > Teacher AI > Claude.
3. Reinstall or update mLearning (install.sh sees the setting and installs the
   package), or run `MLEARNING_WITH_CLAUDE=1 ./install.sh` in the app folder.

Until then the Setup screen says what is missing; mLearning never switches
provider by itself. The Claude teacher runs Claude Opus 5.5 at low effort
with structured JSON output and server-side refusal fallback. NOT VERIFIED on
a device (the package is not installed there).
