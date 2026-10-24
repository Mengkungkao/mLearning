#!/bin/sh
# Smoke test ("test" in manifest.json): mFruit OS runs it after installing and
# before activating; a non-zero exit keeps the previous version. It imports the
# app, checks settings, renders every screen and runs a scripted lesson with
# fake providers and fake audio: no display, microphone, speaker or network.
set -e
export PYTHONDONTWRITEBYTECODE=1   # leave the installed version folder as shipped
cd "$(dirname "$0")"
PYTHON=python3
VENV="${WHISPLAY_OS_APP_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/mlearning}/venv"
if [ -x "$VENV/bin/python" ]; then
  PYTHON="$VENV/bin/python"
fi
"$PYTHON" -m app.main --selftest
echo "mlearning: test passed"
