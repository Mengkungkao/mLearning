# Changelog

## [0.1.0] - 2026-10-24

First version: a voice-first English teacher for mFruit OS.

- Lessons: free conversation, 16 everyday role-plays, job interview practice
  (one question at a time, STAR feedback, a stronger version of your own
  answer), listening practice (retell, repeat, real-world, expressions), and
  questions about English.
- Hands-free listening with on-device voice detection, or hold to talk;
  interrupt the teacher by holding the button; spoken commands ("repeat
  that", "speak more slowly", "let's finish the lesson").
- Corrections ranked meaning, grammar, fluency, at most 1 to 3 a turn; a
  correction card on the screen; practice sentences compared word by word
  (recognised words only, never a pronunciation score).
- Lesson summary, phrases, recurring mistakes, daily goal and progress, all
  stored on the device and deletable.
- OpenAI for speech recognition, the teacher (gpt-4o) and the voice;
  Claude can be the teacher. Starts without keys and says what is missing.
- Needs an OpenAI key. Checked on an Orange Pi Zero 2W (opi2w) with a
  stand-in server; a lesson with OpenAI and a human voice is not yet
  verified on a device (see docs/HARDWARE.md).
