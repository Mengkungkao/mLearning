# Hardware: what is verified, inferred, simulated or untested

Target: Orange Pi Zero 2W with a Whisplay HAT under mFruit OS. Status words
follow mFruit OS's quality language: **DEVICE VERIFIED** (observed on a named
device), **AUTOMATED** (an automated test ran), **INFERRED** (from code or
documents, not observed), **NOT VERIFIED**.

Device: **opi2w** (`orangepi@192.168.0.248`), Orange Pi Zero 2W, Ubuntu
22.04.5, kernel 6.1.31-sun50iw9, Python 3.10.12, NumPy 1.21.5, Pillow 9.0.1,
981 MB RAM, mFruit OS 1.6.2, Whisplay HAT (WM8960 codec), PiSugar 3.
Evidence: [records/2026-10-24-opi2w-first-install.md](records/2026-10-24-opi2w-first-install.md).

| Part | Fact | Status |
|---|---|---|
| Sound card | `whisplaysound` (card 3) for capture and playback; `ahubhdmi` also lists capture and is skipped | DEVICE VERIFIED |
| ALSA devices | the driver's `/etc/asound.conf` defines shared `capture` (dsnoop) and `playback` (dmix), 48 kHz stereo; mLearning uses them | DEVICE VERIFIED |
| Microphone channel | the microphone is on the **left** channel (right: -90 dBFS); both are averaged | DEVICE VERIFIED (2 s recording) |
| Mono capture | asked for one channel, the card returns the microphone ~35 dB down, so capture is stereo | INFERRED (measured by RadioConnect 2026-10-22, not re-measured) |
| Codec warm-up | ~0.7 s per cold open, so the stream stays open during a lesson | INFERRED (RadioConnect measurement) |
| Speaker → microphone | the speaker reaches -17 dBFS RMS in the microphone, louder than speech: the microphone gate during speech is essential | DEVICE VERIFIED |
| Echo tail | within 10 ms of `aplay` exiting the microphone is back at room level; the 0.3 s guard is ample | DEVICE VERIFIED (3 trials) |
| Room noise | median -46, 95th percentile -37 dBFS (20 ms frames); sounds in the room 8-15 dB above the median every few seconds | DEVICE VERIFIED (that room, that afternoon) |
| Voice detection | a speech-like test sound from the speaker was detected as one turn of the right length (2.4 s kept for 1.8 s) | DEVICE VERIFIED (loopback, not a human voice) |
| Human speech detection | whether Normal sensitivity hears a learner reliably, and how often room sounds start a turn | NOT VERIFIED (needs a person speaking) |
| Full lesson pipeline | microphone → turn → upload → teacher → streamed voice → listening → summary → record saved, on the device's threads | DEVICE VERIFIED against a local stand-in server; NOT VERIFIED with OpenAI (no key on the device yet) |
| LCD | 240x280, first frame ~0.1 s after start, screens drawn through whisplay-daemon's framebuffer | DEVICE VERIFIED (framebuffer captures) |
| Keyboard | keys reach mLearning through mFruit OS's key hub (`mfruitctl key`) | DEVICE VERIFIED (simulated keys) |
| Button | gestures through the SDK InputController | AUTOMATED; NOT VERIFIED with the physical button |
| Exit | 4x / Esc from Home, `app_exit_requested` | AUTOMATED; Esc DEVICE VERIFIED (see record) |
| Resources | listening: ~12 % of one core, 29 MB; with rendering 52 MB; a lesson frame draws in 11 ms | DEVICE VERIFIED |
| Raspberry Pi Zero 2 W | same HAT, different board | NOT VERIFIED |
| Accents | OpenAI voices are not accent-specific; Settings > English changes spelling and vocabulary, not the voice's accent | INFERRED |
| Pronunciation scoring | not provided: practice compares recognised words only | by design |
