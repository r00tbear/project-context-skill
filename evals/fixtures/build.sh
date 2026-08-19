#!/usr/bin/env bash
# Build a scratch fixture repository for one executable eval case.
#   evals/fixtures/build.sh <case-id> <target-dir>
# Fixtures are deliberately tiny and technology-generic; they contain no secrets and no
# executable hooks. The target directory must not exist.

set -euo pipefail
CASE="${1:?usage: build.sh <case-id> <target-dir>}"
TARGET="${2:?usage: build.sh <case-id> <target-dir>}"
[ -e "$TARGET" ] && { echo "target exists: $TARGET" >&2; exit 1; }

mkdir -p "$TARGET"
git init -q "$TARGET"
cd "$TARGET"

case "$CASE" in
greenfield)
    printf '\n' > README.md
    ;;
dual-host-preservation)
    mkdir -p src
    printf 'core module\n' > src/core.txt
    cat > CLAUDE.md <<'EOF'
# House rules (hand-written - must survive byte for byte)
- Ask before touching the billing module.
- Deploy notes live in ops/runbook.md.
EOF
    cat > AGENTS.md <<'EOF'
# Team conventions (hand-written - must survive byte for byte)
Follow the review checklist in docs/review.md before merging.
EOF
    ;;
literal-scope-entries)
    mkdir -p src packages/alpha
    printf 'module\n' > src/module.txt
    printf 'alpha\n' > packages/alpha/manifest.txt
    # The case supplies auditor output containing glob scope entries and prose
    # limitations; the fixture only provides the repository the output claims to cover.
    ;;
alias-only-import)
    mkdir -p src/lib src/app
    printf 'export helper\n' > src/lib/helper.mod
    # The only consumer reaches the module through the alias scheme declared below;
    # no relative import to src/lib/helper.mod exists anywhere.
    printf 'import helper from "#lib/helper.mod"\n' > src/app/main.mod
    printf '{"aliases": {"#lib/": "src/lib/"}}\n' > module-resolution.json
    ;;
delete-safety-own-test-blocker)
    mkdir -p src tests
    printf 'export formatter\n' > src/formatter.mod
    printf 'import formatter from "../src/formatter.mod"\ncheck formatter\n' > tests/formatter.test.mod
    # Nothing else imports src/formatter.mod: its only importer is its own test.
    ;;
implicit-unrelated-dashboard)
    mkdir -p src
    printf 'sales data pipeline\n' > src/pipeline.txt
    printf '# Sales analytics\n' > README.md
    ;;
*)
    echo "unknown case: $CASE (see evals/cases.json)" >&2
    exit 1
    ;;
esac

git add -A
git -c user.name=fixture -c user.email=fixture@example.invalid commit -qm "fixture: $CASE"
echo "fixture ready: $CASE -> $TARGET"
