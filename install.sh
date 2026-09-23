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
#   5. installs and registers jCodeMunch (the local code index the skill requires);
#   6. archives any legacy full copy it replaces into ~/.skill-backups/ (never deletes);
#   7. runs the skill's own self-check and prints what to do next.
#
# It never touches your projects. Environment overrides:
#   PROJECT_CONTEXT_REPO           - clone source (default: the GitHub repository)
#   PROJECT_CONTEXT_VERSION        - tag to install (default: latest vX.Y.Z tag)
#   PROJECT_CONTEXT_HOME           - home directory to install under (default: $HOME)
#   PROJECT_CONTEXT_NO_JCODEMUNCH  - set to 1 to skip the jCodeMunch step (CI/hermetic)

set -euo pipefail

REPO_URL="${PROJECT_CONTEXT_REPO:-https://github.com/r00tbear/project-context-skill.git}"
HOME_DIR="${PROJECT_CONTEXT_HOME:-$HOME}"
case "$HOME_DIR" in /*) ;; *) printf '[project-context] error: PROJECT_CONTEXT_HOME must be absolute\n' >&2; exit 1 ;; esac
[ ! -L "$HOME_DIR" ] || { printf '[project-context] error: refusing a symlink home: %s\n' "$HOME_DIR" >&2; exit 1; }
HOME_DIR="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$HOME_DIR")" || { printf '[project-context] error: cannot resolve PROJECT_CONTEXT_HOME\n' >&2; exit 1; }
PAYLOAD="$HOME_DIR/.agents/skills/project-context"
ADAPTER_DIR="$HOME_DIR/.claude/skills/project-context"
BACKUPS="$HOME_DIR/.skill-backups"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
LEGACY_CODEX="$HOME_DIR/.codex/skills/project-context"
CLAUDE_BACKUP="$BACKUPS/claude-project-context-$STAMP"
CODEX_BACKUP="$BACKUPS/codex-project-context-$STAMP"

# Color only when writing to a terminal and NO_COLOR is unset (https://no-color.org).
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then C_INFO='\033[1;36m'; C_ERROR='\033[1;31m'; C_RESET='\033[0m'; else C_INFO=''; C_ERROR=''; C_RESET=''; fi
say()  { printf "${C_INFO}[project-context]${C_RESET} %s\n" "$*"; }
fail() { printf "${C_ERROR}[project-context] error:${C_RESET} %s\n" "$*" >&2; exit 1; }
check_install_path() {
    local target="$1" current=/ relative part
    local -a parts
    case "$HOME_DIR" in /*) ;; *) fail "PROJECT_CONTEXT_HOME must be absolute" ;; esac
    case "$HOME_DIR" in *'/../'*|*'/./'*|*'/..'|*'/.'|*'//'*) fail "PROJECT_CONTEXT_HOME must be normalized" ;; esac
    case "$target" in "$HOME_DIR"|"$HOME_DIR"/*) ;; *) fail "path is outside the selected home: $target" ;; esac
    relative="$target"
    relative="${relative#/}"
    IFS=/ read -r -a parts <<< "$relative"
    for part in "${parts[@]}"; do
        [ -n "$part" ] || continue
        current="${current%/}/$part"
        [ ! -L "$current" ] || fail "refusing a symlink in install path: $current"
    done
}

command -v git >/dev/null 2>&1 || fail "git is required. Install git first: https://git-scm.com/downloads"
command -v python3 >/dev/null 2>&1 || fail "python3 is required. Install Python 3.11+ first: https://www.python.org/downloads/"
python3 - <<'PY' || fail "Python 3.11+ is required (found an older version)."
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

version="${PROJECT_CONTEXT_VERSION:-}"
if [ -z "$version" ]; then
    # Two distinct failures, reported separately (and survivable under pipefail):
    # an unreachable repository, and a reachable one with no release tag.
    tags="$(git ls-remote --tags --refs "$REPO_URL" 'v*')" || fail "could not reach $REPO_URL"
    version="$(printf '%s\n' "$tags" \
        | awk -F/ '{print $NF}' \
        | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
        | sort -V | tail -1 || true)"
fi
[ -n "$version" ] || fail "could not resolve a release tag from $REPO_URL"
say "installing release $version"

# Check every destination before archiving or updating anything. Repeat at each write.
for path in "$PAYLOAD" "$PAYLOAD/.git" "$ADAPTER_DIR" "$ADAPTER_DIR/SKILL.md" \
    "$BACKUPS" "$CLAUDE_BACKUP" "$CODEX_BACKUP" "$LEGACY_CODEX"; do
    check_install_path "$path"
done
[ ! -e "$CLAUDE_BACKUP" ] && [ ! -e "$CODEX_BACKUP" ] || fail "backup destination already exists; re-run the installer"

# Archive a legacy full copy living where the small adapter belongs (v0.1/v0.2 layouts):
# anything there with more than the adapter's single SKILL.md is a full clone.
if [ -d "$ADAPTER_DIR" ] && { [ -d "$ADAPTER_DIR/auditors" ] || [ -d "$ADAPTER_DIR/.git" ]; }; then
    check_install_path "$ADAPTER_DIR"
    check_install_path "$BACKUPS"
    mkdir -p "$BACKUPS"
    mv "$ADAPTER_DIR" "$CLAUDE_BACKUP"
    say "archived the old copy from ~/.claude/skills/project-context to ~/.skill-backups/claude-project-context-$STAMP"
fi
# Same-name copy in the legacy Codex skills directory shadows the canonical payload.
if [ -e "$LEGACY_CODEX" ]; then
    check_install_path "$LEGACY_CODEX"
    check_install_path "$BACKUPS"
    mkdir -p "$BACKUPS"
    mv "$LEGACY_CODEX" "$CODEX_BACKUP"
    say "archived the old copy from ~/.codex/skills/project-context to ~/.skill-backups/codex-project-context-$STAMP"
fi

check_install_path "$PAYLOAD/.git"
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
    check_install_path "$PAYLOAD"
    mkdir -p "$(dirname "$PAYLOAD")"
    git -c advice.detachedHead=false clone --quiet --branch "$version" --depth 1 "$REPO_URL" "$PAYLOAD" 2>/dev/null \
        || git -c advice.detachedHead=false clone --quiet --branch "$version" "$REPO_URL" "$PAYLOAD"
fi

check_install_path "$ADAPTER_DIR"
check_install_path "$ADAPTER_DIR/SKILL.md"
check_install_path "$PAYLOAD/templates/host/claude-skill-adapter.md"
mkdir -p "$ADAPTER_DIR"
[ ! -d "$ADAPTER_DIR/SKILL.md" ] || fail "adapter target is a directory"
adapter_temp="$(mktemp "$ADAPTER_DIR/.SKILL.md.XXXXXX")" || fail "cannot create adapter temporary file"
if ! cp "$PAYLOAD/templates/host/claude-skill-adapter.md" "$adapter_temp"; then
    rm -f -- "$adapter_temp"
    fail "cannot copy the Claude adapter"
fi
check_install_path "$ADAPTER_DIR/SKILL.md"
if ! mv -f -- "$adapter_temp" "$ADAPTER_DIR/SKILL.md"; then
    rm -f -- "$adapter_temp"
    fail "cannot replace the Claude adapter"
fi

# jCodeMunch: the local, offline code index the skill audits through. Required.
if [ "${PROJECT_CONTEXT_NO_JCODEMUNCH:-}" = "1" ]; then
    say "skipping jCodeMunch (PROJECT_CONTEXT_NO_JCODEMUNCH=1); the skill requires it at run time"
else
    export PATH="$HOME_DIR/.local/bin:$PATH"
    if ! command -v jcodemunch-mcp >/dev/null 2>&1; then
        if command -v uv >/dev/null 2>&1; then
            say "installing jCodeMunch with uv"
            uv tool install --quiet jcodemunch-mcp || fail "uv tool install jcodemunch-mcp failed"
        elif command -v pipx >/dev/null 2>&1; then
            say "installing jCodeMunch with pipx"
            pipx install --quiet jcodemunch-mcp || fail "pipx install jcodemunch-mcp failed"
        else
            fail "jCodeMunch is required and neither uv nor pipx is available.
    Install uv first (https://docs.astral.sh/uv/): curl -LsSf https://astral.sh/uv/install.sh | sh
    then re-run this installer."
        fi
    fi
    command -v jcodemunch-mcp >/dev/null 2>&1 || fail "jcodemunch-mcp installed but not on PATH; open a new shell and re-run this installer."
    say "jCodeMunch $(jcodemunch-mcp --version 2>/dev/null || echo present)"
    # Register the MCP server with the coding agents found on this machine.
    # Registration failures are not fatal here: the skill's preflight re-checks and
    # prints the same command, so the user is never stuck silently.
    # Run init from a scratch directory: it drops agent-instruction files (AGENTS.md,
    # .windsurfrules, .cursor/rules/) into its CWD, and that must never be the user's
    # project or the payload clone. The mktemp assignment is guarded: on failure the
    # condition is false and init never runs from the current directory.
    if scratch_dir="$(mktemp -d)" && (cd "$scratch_dir" && jcodemunch-mcp init --client auto --yes </dev/null >/dev/null 2>&1); then
        say "registered the jCodeMunch MCP server with your detected agents"
    else
        say "warning: automatic MCP registration failed; run manually: jcodemunch-mcp init --client auto --yes"
    fi
fi

python3 "$PAYLOAD/scripts/project_context.py" self-check --skill-root "$PAYLOAD" >/dev/null \
    || fail "self-check failed; the installation is incomplete. Re-run the installer or file an issue."

say "installed $version and verified with self-check."
say "next: open Claude Code (or Codex) in any Git repository and say: audit this repository with project-context"
say "update later by re-running this same command."
