#!/bin/sh
# Entrypoint. mFruit OS starts it through mfruit-run with the working directory
# set to the active version and WHISPLAY_APP_ID / WHISPLAY_OS_APP_DATA set.
# Run it by hand for the other modes: ./run.sh --terminal, --diagnose, --help.
cd "$(dirname "$0")" || exit 1
export PYTHONUNBUFFERED=1
PYTHON=python3
# install.sh makes a venv only for optional extras (the Claude teacher).
VENV="${WHISPLAY_OS_APP_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/mlearning}/venv"
if [ -x "$VENV/bin/python" ]; then
  PYTHON="$VENV/bin/python"
fi
exec "$PYTHON" -m app.main "$@"
