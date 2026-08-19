#!/usr/bin/env bash
# project-context installer: one command, safe to re-run (re-running updates).
#
#   curl -fsSL https://raw.githubusercontent.com/r00tbear/project-context-skill/main/install.sh | bash
#
# What it does, in order:
#   1. checks git and Python 3.11+;
#   2. resolves the latest release tag (override: PROJECT_CONTEXT_VERSION=v0.4.0);
#   3. installs/updates the canonical payload at ~/.agents/skills/project-context;
#   4. writes the small Claude adapter to ~/.claude/skills/project-context/SKILL.md;
#   5. archives any legacy full copy it replaces into ~/.skill-backups/ (never deletes);
#   6. runs the skill's own self-check and prints what to do next.
#
# It never touches your projects. Environment overrides:
#   PROJECT_CONTEXT_REPO     - clone source (default: the GitHub repository)
#   PROJECT_CONTEXT_VERSION  - tag to install (default: latest vX.Y.Z tag)
#   PROJECT_CONTEXT_HOME     - home directory to install under (default: $HOME)

set -euo pipefail

REPO_URL="${PROJECT_CONTEXT_REPO:-https://github.com/r00tbear/project-context-skill.git}"
HOME_DIR="${PROJECT_CONTEXT_HOME:-$HOME}"
PAYLOAD="$HOME_DIR/.agents/skills/project-context"
ADAPTER_DIR="$HOME_DIR/.claude/skills/project-context"
BACKUPS="$HOME_DIR/.skill-backups"
STAMP="$(date +%Y%m%d-%H%M%S)"

say()  { printf '\033[1;36m[project-context]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[project-context] error:\033[0m %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required. Install git first: https://git-scm.com/downloads"
command -v python3 >/dev/null 2>&1 || fail "python3 is required. Install Python 3.11+ first: https://www.python.org/downloads/"
python3 - <<'PY' || fail "Python 3.11+ is required (found an older version)."
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

version="${PROJECT_CONTEXT_VERSION:-}"
if [ -z "$version" ]; then
    version="$(git ls-remote --tags --refs "$REPO_URL" 'v*' \
        | awk -F/ '{print $NF}' \
        | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
        | sort -V | tail -1)"
fi
[ -n "$version" ] || fail "could not resolve a release tag from $REPO_URL"
say "installing release $version"

# Archive a legacy full copy living where the small adapter belongs (v0.1/v0.2 layouts):
# anything there with more than the adapter's single SKILL.md is a full clone.
if [ -d "$ADAPTER_DIR" ] && { [ -d "$ADAPTER_DIR/auditors" ] || [ -d "$ADAPTER_DIR/.git" ]; }; then
    mkdir -p "$BACKUPS"
    mv "$ADAPTER_DIR" "$BACKUPS/claude-project-context-$STAMP"
    say "archived the old copy from ~/.claude/skills/project-context to ~/.skill-backups/claude-project-context-$STAMP"
fi
# Same-name copy in the legacy Codex skills directory shadows the canonical payload.
if [ -e "$HOME_DIR/.codex/skills/project-context" ]; then
    mkdir -p "$BACKUPS"
    mv "$HOME_DIR/.codex/skills/project-context" "$BACKUPS/codex-project-context-$STAMP"
    say "archived the old copy from ~/.codex/skills/project-context to ~/.skill-backups/codex-project-context-$STAMP"
fi

if [ -d "$PAYLOAD/.git" ]; then
    origin="$(git -C "$PAYLOAD" remote get-url origin 2>/dev/null || true)"
    case "$origin" in
        *project-context-skill*) ;;
        *) fail "$PAYLOAD exists but is not a project-context clone (origin: ${origin:-none}). Move it aside and re-run." ;;
    esac
    say "updating existing installation"
    git -C "$PAYLOAD" fetch --quiet --tags origin
    git -C "$PAYLOAD" -c advice.detachedHead=false checkout --quiet "$version"
elif [ -e "$PAYLOAD" ]; then
    fail "$PAYLOAD exists and is not a git clone. Move it aside and re-run."
else
    mkdir -p "$(dirname "$PAYLOAD")"
    git -c advice.detachedHead=false clone --quiet --branch "$version" --depth 1 "$REPO_URL" "$PAYLOAD" 2>/dev/null \
        || git -c advice.detachedHead=false clone --quiet --branch "$version" "$REPO_URL" "$PAYLOAD"
fi

mkdir -p "$ADAPTER_DIR"
cp "$PAYLOAD/templates/host/claude-skill-adapter.md" "$ADAPTER_DIR/SKILL.md"

python3 "$PAYLOAD/scripts/project_context.py" self-check --skill-root "$PAYLOAD" >/dev/null \
    || fail "self-check failed; the installation is incomplete. Re-run the installer or file an issue."

say "installed $version and verified with self-check."
say "next: open Claude Code (or Codex) in any Git repository and say: audit this repository with project-context"
say "update later by re-running this same command."
