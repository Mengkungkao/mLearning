#!/usr/bin/env bash
# Build the release archive mFruit OS installs and updates mLearning from.
#
#   tools/build-release.sh               -> dist/mlearning-<version>.tar.gz and dist/SHA256SUMS
#   tools/build-release.sh --out DIR     -> the same files in DIR
#
# The package holds the code, vendored SDK, hooks, assets and docs; no tests,
# tools, caches or git metadata (mFruit OS docs/apps/PACKAGING.md). It must
# pass mFruit OS's package preflight (MFRUIT_OS, default ~/MFruitOS) and the
# app's own smoke test before the archive is written. Publishing: push a new
# manifest version (.github/workflows/release.yml), or see docs/RELEASING.md.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
out="dist"
if [ "${1:-}" = "--out" ]; then
    out="$2"
fi
version=$(python3 -c 'import json; print(json.load(open("manifest.json"))["version"])')
name="mlearning-$version"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/$name" "$out"
tar --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=dist \
    --exclude=./tests --exclude=./tools --exclude='*.pyc' --exclude=.venv --exclude=venv \
    --exclude=./.github -cf - . | tar -xf - -C "$stage/$name"
checker="${MFRUIT_OS:-$HOME/MFruitOS}/scripts/check-app.py"
if [ -f "$checker" ]; then
    python3 "$checker" "$stage/$name"
else
    echo "warning: $checker not found; package not checked" >&2
fi
(cd "$stage/$name" && PYTHONDONTWRITEBYTECODE=1 bash test.sh)
tar -czf "$out/$name.tar.gz" -C "$stage" "$name"
(cd "$out" && sha256sum "$name.tar.gz" > SHA256SUMS)
echo "built $out/$name.tar.gz"
cat "$out/SHA256SUMS"
