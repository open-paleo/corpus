#!/usr/bin/env bash
# Open Paleo corpus: first-time post-clone setup.
# Idempotent: re-running only does the steps still needed.
#
#   scripts/bootstrap.sh            venv, .env scaffold, search index
#   scripts/bootstrap.sh --hooks    also enable the tracked git hooks
#
# --hooks sets core.hooksPath to .githooks/: pre-commit refuses secrets,
# pre-push refuses github.com when `git config archive.private true`, and
# post-merge re-runs scripts/index.py after a pull that changed markdown.
# That last one runs code from whatever was merged, so it is opt-in.
#
# PYTHON picks the interpreter (default: the first of python3.13, python3.12,
# python3.11, python3 that is 3.11 or newer).

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

HOOKS=0
for arg in "$@"; do
  case "$arg" in
    --hooks) HOOKS=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\n>>> %s\n' "$*"; }

# 1. git-lfs, for an archive that commits its PDFs (a fresh public clone has
#    no LFS objects: harmless).
if command -v git-lfs >/dev/null 2>&1; then
  say "Registering the git-lfs filter"
  git lfs install --local --skip-repo
  if [ -n "$(git lfs ls-files 2>/dev/null | head -1)" ]; then
    say "Pulling LFS objects (PDFs)"
    git lfs pull
  fi
else
  echo "NOTE: git-lfs not installed; PDFs tracked in LFS will be pointer files." >&2
  echo "      macOS: brew install git-lfs   Debian/Ubuntu: apt install git-lfs" >&2
fi

# 2. Python venv at .runtime/venv/.
pick_python() {
  if [ -n "${PYTHON:-}" ]; then echo "$PYTHON"; return; fi
  for p in python3.13 python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1 &&
       "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      echo "$p"; return
    fi
  done
  echo "ERROR: Python 3.11 or newer not found (set PYTHON=...)" >&2
  exit 1
}
if [ ! -x .runtime/venv/bin/python ]; then
  PY="$(pick_python)"
  say "Creating Python venv at .runtime/venv/ with $PY"
  "$PY" -m venv .runtime/venv
  .runtime/venv/bin/pip install --upgrade pip
  # requirements.txt pins the full environment (every extra, marker included);
  # pyproject.toml declares the dependencies themselves.
  .runtime/venv/bin/pip install -r requirements.txt
else
  say "Venv already present at .runtime/venv/ — skipping (delete to recreate)"
fi
say "Installing the corpus package (editable)"
.runtime/venv/bin/pip install -q -e . --no-deps

# 3. .env scaffold (gitignored; user fills in keys).
if [ ! -f .env ]; then
  say "Creating .env from template (mode 0600) — fill in your API keys"
  cp .env.example .env
  chmod 600 .env
  echo "    edit: $ROOT/.env"
fi

# 4. FTS5 search index (regenerable from markdown/).
say "Building FTS5 search index from markdown/"
.runtime/venv/bin/python scripts/index.py

# 5. Tracked git hooks (opt-in).
if [ "$HOOKS" = 1 ]; then
  say "Wiring up .githooks/ as core.hooksPath"
  git config core.hooksPath .githooks
else
  say "Git hooks not enabled (re-run with --hooks to enable .githooks/)"
fi

cat <<'EOF'

>>> Bootstrap complete.

Next steps:

  ./.runtime/venv/bin/corpus                      # list the commands
  ./.runtime/venv/bin/corpus manifest refresh     # create/refresh manifest.json from the bib (safe to re-run)
  ./.runtime/venv/bin/corpus status               # what is held and what is missing
  ./.runtime/venv/bin/corpus search 'Hylaeosaurus'

  # Optional: the Biodiversity Heritage Library tier (old, public-domain scans;
  # 92 MB download, 0.55 GB index):
  ./.runtime/venv/bin/corpus bhl setup && ./.runtime/venv/bin/corpus bhl index

EOF
