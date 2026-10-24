# Troubleshooting

Start with `./run.sh --diagnose` in the app folder
(`~/.whisplay-os/apps/mlearning/current`), and the log
`~/.whisplay-os/logs/mlearning.log`.

| You see | Why | What to do |
|---|---|---|
| "Needs setup" on Home, Setup screen | no `OPENAI_API_KEY` | add it to `~/.whisplay-os/apps/mlearning/data/.env`, then Setup > Retry |
| "Key not accepted" | the provider refused the key | check the key; no spaces or quotes problems in `.env` |
| "Quota used up" | the account has no credit | billing on the provider's website |
| "No connection" | Wi-Fi or DNS | check Wi-Fi in mFruit OS; Retry keeps your turn |
| "Took too long" | slow network or provider | Retry; the step that failed is repeated, not the whole lesson |
| "Microphone busy" | another program holds the card (Details names it) | close that app; a leftover recorder can be killed (`pkill arecord`, `pkill sox`) |
| "Microphone problem", or "Whisplay sound card missing" in --diagnose | the codec did not start at boot | mFruit OS Settings > Audio and Diagnostics > Test microphone; reboot |
| It stops listening by itself | 30 s without speech turns the microphone off (privacy) | tap to listen again |
| It answers when nobody spoke, or "I didn't catch that" often | room sounds | Settings > Mic sensitivity > Low, or Listening > Hold to talk |
| It does not hear you | quiet voice, far away, or Low sensitivity | speak closer; Mic sensitivity > High; mFruit OS mic level |
| It cuts you off while you think | Pause before reply is short | Settings > Pause before reply > Long |
| The teacher talks too fast | | say "speak more slowly", or 3x > Speak slower |
| Claude is "not installed" | the anthropic package is missing | [Configuration](CONFIGURATION.md#claude-as-the-teacher) |

Logs contain states, timings and errors but not what was said. The voice
detector logs, as numbers, when speech started and how long each turn was:
useful for tuning Mic sensitivity.
