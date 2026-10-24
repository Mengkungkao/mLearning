#!/usr/bin/env bash
# Everything CI runs, in one command (Linux, from anywhere):
#
#   bash tools/check.sh            all checks
#   bash tools/check.sh --quick    skip the test suite
#
# Python 3.9 syntax and no run-time 'X | Y' unions (boards run 3.9-3.13), shell
# syntax, the unit/integration suite (standard library unittest: the boards have
# no pytest), the package's own smoke test, mFruit OS's package preflight
# (MFRUIT_OS, default ~/MFruitOS; skipped without a checkout), the vendored SDK
# copy and the app contract copy, Markdown links, and trailing whitespace.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
export PYTHONDONTWRITEBYTECODE=1
OS="${MFRUIT_OS:-$HOME/MFruitOS}"
failed=0
step() {
  local name="$1"; shift
  if "$@"; then echo "    ok  $name"; else echo "  FAIL  $name"; failed=1; fi
}

py39() {
  python3 - <<'PY'
import ast, pathlib, sys
bad = 0
TYPES = {"int", "str", "float", "bool", "bytes", "dict", "list", "tuple", "set", "type", "object"}
def typeish(n):
    return ((isinstance(n, ast.Constant) and n.value is None) or (isinstance(n, ast.Name) and n.id in TYPES)
            or isinstance(n, ast.Subscript) or (isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)))
for path in sorted(pathlib.Path(".").rglob("*.py")):
    if any(p in {".git", "__pycache__", ".venv", "venv"} for p in path.parts):
        continue
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 9))
    except SyntaxError as exc:
        print(f"{path}:{exc.lineno}: not Python 3.9 syntax: {exc.msg}"); bad += 1; continue
    skip = set()
    for node in ast.walk(tree):
        parts = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            parts = [x.annotation for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]
                     if x is not None] + [node.returns]
        elif isinstance(node, ast.AnnAssign):
            parts = [node.annotation]
        for part in parts:
            if part is not None:
                skip.update(id(n) for n in ast.walk(part))
    for node in ast.walk(tree):
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and id(node) not in skip
                and typeish(node.left) and typeish(node.right)):
            print(f"{path}:{node.lineno}: 'X | Y' at run time needs Python 3.10"); bad += 1
sys.exit(1 if bad else 0)
PY
}

shells() {
  local ok=0 f
  for f in run.sh install.sh test.sh tools/*.sh; do
    sh -n "$f" 2>/dev/null || bash -n "$f" || { echo "syntax error: $f"; ok=1; }
  done
  return $ok
}

whitespace() {
  ! grep -rnI -E '[[:space:]]+$' --exclude-dir=.git --exclude-dir=mfruit_sdk --exclude-dir=.claude \
      --exclude-dir=__pycache__ --exclude-dir=dist --exclude='*.png' . \
    || { echo "trailing whitespace above"; return 1; }
}

suite() { python3 -m unittest discover -s tests -t . 2>&1 | tail -n 3 | grep -q '^OK'; }
smoke() { sh test.sh >/dev/null; }
preflight() {
  # Bytecode from local runs is not part of a package (build-release.sh excludes it).
  find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
  if [ -f "$OS/scripts/check-app.py" ]; then python3 "$OS/scripts/check-app.py" . >/dev/null; else
    echo "no mFruit OS checkout at $OS: preflight skipped"; fi
}
sdk() {
  if [ -x "$OS/scripts/sdk-sync.sh" ]; then
    "$OS/scripts/sdk-sync.sh" . --check >/dev/null &&
      cmp -s "$OS/docs/apps/APP_CONTRACT.md" .claude/rules/mfruit-os-app.md
  else echo "no mFruit OS checkout at $OS: SDK check skipped"; fi
}

links() {
  python3 - <<'PY'
import pathlib, re, sys
bad = 0
for path in sorted(pathlib.Path(".").rglob("*.md")):
    if any(p in {".git", "mfruit_sdk", ".claude", "node_modules"} for p in path.parts):
        continue
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", path.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("mailto:"):
            continue
        if not (path.parent / target).exists():
            print(f"{path}: broken link {target}"); bad += 1
sys.exit(1 if bad else 0)
PY
}

step "Python 3.9 syntax and run-time unions" py39
step "shell syntax" shells
step "trailing whitespace" whitespace
if [ "${1:-}" != "--quick" ]; then
  step "test suite" suite
fi
step "smoke test (test.sh)" smoke
step "mFruit OS package preflight" preflight
step "SDK and app contract copies" sdk
step "Markdown links" links
find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
if [ $failed = 0 ]; then echo "All checks passed."; else echo "Some checks failed."; fi
exit $failed
