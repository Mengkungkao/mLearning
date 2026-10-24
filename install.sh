#!/bin/sh
# Install hook. mFruit OS runs it on install, reinstall, update and downgrade,
# as the user, without a terminal; a non-zero exit keeps the previous version.
# Run it by hand to set up the same way. It never uses sudo and changes nothing
# outside mLearning's data folder.
#
#   Python 3.9+, NumPy, Pillow 9.0+   required     (Debian: python3-numpy python3-pil)
#   arecord, aplay                    microphone and speaker (alsa-utils)
#   anthropic (pip, in a venv)        only when Settings > Teacher AI is Claude,
#                                     or MLEARNING_WITH_CLAUDE=1
set -eu
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
DATA="${WHISPLAY_OS_APP_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/mlearning}"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  echo "mlearning: Python 3.9 or newer is needed"; exit 1
fi
missing=""
python3 -c 'import numpy' 2>/dev/null || missing="$missing python3-numpy"
python3 -c 'import PIL' 2>/dev/null || missing="$missing python3-pil"
if [ -n "$missing" ]; then
  echo "mlearning: missing:$missing (install with: sudo apt install$missing)"; exit 1
fi
for tool in arecord aplay; do
  command -v "$tool" >/dev/null 2>&1 || echo "mlearning: warning: $tool not found (sudo apt install alsa-utils); no voice until it is installed"
done

# The keys file: created once from the template, owner-only, never overwritten.
mkdir -p "$DATA"
chmod 700 "$DATA"
if [ ! -e "$DATA/.env" ]; then
  umask 077
  cp .env.example "$DATA/.env"
  chmod 600 "$DATA/.env"
  echo "mlearning: created $DATA/.env; add your OPENAI_API_KEY there"
fi

# Optional: the Claude teacher needs the anthropic package, in a venv in the
# data folder that still sees the system's NumPy and Pillow.
wants_claude="${MLEARNING_WITH_CLAUDE:-0}"
if [ -f "$DATA/settings.json" ] && grep -q '"llm_provider": "anthropic"' "$DATA/settings.json"; then
  wants_claude=1
fi
if [ "$wants_claude" = "1" ]; then
  if [ ! -x "$DATA/venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$DATA/venv" || echo "mlearning: warning: could not create a venv"
  fi
  if [ -x "$DATA/venv/bin/python" ] && ! "$DATA/venv/bin/python" -c 'import anthropic' 2>/dev/null; then
    "$DATA/venv/bin/python" -m pip install --quiet --disable-pip-version-check anthropic \
      || echo "mlearning: warning: could not install the anthropic package; the Claude teacher stays unavailable"
  fi
fi

PYTHON=python3
if [ -x "$DATA/venv/bin/python" ]; then PYTHON="$DATA/venv/bin/python"; fi
"$PYTHON" -c 'import numpy, PIL, sys; print("mlearning: dependencies ok: Python", sys.version.split()[0], "NumPy", numpy.__version__, "Pillow", PIL.__version__)'
