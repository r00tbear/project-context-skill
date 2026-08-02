# project-context

`project-context` audits or bootstraps a Git repository and generates one durable project context for Claude Code and Codex.

It is deliberately technology-neutral. A project may be a library, CLI/TUI, service, web/native/desktop app, data pipeline, firmware, infrastructure repository, documentation/specification set, monorepo, or something else. The skill discovers the real shape instead of assuming a framework, SQL database, layered architecture, or test pyramid.

## What it does

1. **Audit** - inspect stack, architecture, bloat, security, testing, and the UI/data domains only when they exist.
2. **Decide** - turn evidence into explicit target-state choices and ADRs.
3. **Generate** - create root `PROJECT_CONTEXT.md` and supporting documentation under `repodocs/`.
4. **Wire** - make Claude Code and Codex read that same context while preserving host-specific instructions.
5. **Verify** - validate generated ownership, hashes, host blocks, and wikilinks.
6. **Review** - check a diff/branch/PR against trusted project policy with an independent adversarial pass.

High and critical audit candidates are not accepted at face value: a fresh independent agent must try to disprove or reproduce them before they influence decisions.

## Shared Claude + Codex model

There is one canonical payload and one canonical generated context:

```text
.agents/skills/project-context/       # full skill, discovered by Codex
.claude/skills/project-context/       # tiny Claude adapter to the same payload
PROJECT_CONTEXT.md                    # shared generated context
CLAUDE.md                             # managed @PROJECT_CONTEXT.md import
AGENTS.md                             # managed instruction to read PROJECT_CONTEXT.md
.codex/config.toml                    # optional passive Codex fallback
```

The skill never creates lowercase `agents.md`, never writes generated files into hand-authored `docs/`, and never replaces `CLAUDE.md` or `AGENTS.md` wholesale.

## Install

Current release: `v0.2.1`.

This version intentionally provides no migration or backward compatibility. Before installing, archive anything you need and remove old `project-context` payloads/adapters so only one same-name installation remains. Do not bulk-delete a project `docs/` directory: v0.2 starts clean and writes only to `repodocs/`.

Requirements: Git and Python 3.11+.

### Personal installation

Install the canonical payload under `.agents` and copy only the Claude adapter:

```bash
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills/project-context"
git clone --branch v0.2.1 --depth 1 \
  https://github.com/r00tbear/project-context-skill.git \
  "$HOME/.agents/skills/project-context"
cp "$HOME/.agents/skills/project-context/templates/host/claude-skill-adapter.md" \
  "$HOME/.claude/skills/project-context/SKILL.md"
```

This layout assumes Claude's default `~/.claude` directory. With a custom `CLAUDE_CONFIG_DIR`, use the project installation below so the adapter and canonical payload keep a stable relative path.

### Team/project installation

Pin the skill as a submodule in the target repository:

Run from the exact Git root. Before writing, inspect existing host trees with `find -P .agents .claude -type l -print 2>/dev/null`; stop if it prints any symlink, because the commands below must not traverse outside the repository.

```bash
mkdir -p .agents/skills .claude/skills/project-context
git submodule add \
  https://github.com/r00tbear/project-context-skill.git \
  .agents/skills/project-context
git -C .agents/skills/project-context checkout v0.2.1
cp .agents/skills/project-context/templates/host/claude-skill-adapter.md \
  .claude/skills/project-context/SKILL.md
```

Commit `.gitmodules`, the submodule gitlink, and the adapter. New clones initialize it with:

```bash
git submodule update --init -- .agents/skills/project-context
```

Do not keep a personal same-name copy while using a project installation. Claude gives the personal copy precedence; Codex retains both distinct paths, so neither host guarantees the single pinned payload you intended.

### Update

The fastest path is to ask your agent — "update the project-context skill" / «обнови скилл project-context». The skill's upgrade mode (`references/upgrade.md`) inventories every installed copy, updates the payload and adapter to the new tag, removes duplicates with your confirmation, and re-applies the context in the project.

Manual equivalent — published tags are immutable; fetch and select a new tag, then refresh the adapter:

```bash
git -C .agents/skills/project-context fetch --tags
git -C .agents/skills/project-context checkout <new-tag>
cp .agents/skills/project-context/templates/host/claude-skill-adapter.md \
  .claude/skills/project-context/SKILL.md
```

Then invoke the refreshed skill to regenerate context, wire the current host blocks, write the new manifest last, and run `validate-project`. The manifest version must match the installed `VERSION`.

## Use

Invoke `$project-context` in Codex or `/project-context` in Claude Code, then ask to audit, bootstrap, refresh context, or review a diff/PR.

The normal existing-repository flow is:

```text
Preflight -> Audit -> Decide -> Generate -> Wire -> Verify
```

An empty repository uses a short requirements interview before Decide. Review mode is read-only: it reads a trusted base policy, inspects `git diff`, and asks a fresh independent agent to challenge would-fail findings. It does not need a custom headless model runner.

## Generated layout

```text
PROJECT_CONTEXT.md
CLAUDE.md
AGENTS.md
.jcodemunch.jsonc                  # optional private/offline index config
repodocs/
  project-context.config.json
  project-context.manifest.json
  decisions.md
  CurrentSprint.md                  # optional shared coordination ledger
  LegacyWarning.md
  migration-backlog.md
  techstack.md
  architecture.md
  security.md
  testing.md
  edge-cases.md
  data-model.md                    # only when the data domain exists
  ui-kit.md                        # only when a UI exists
  audit/findings/*.json
  audit/inventory.json
  audit/drift-report.md
```

Compact layout folds target-state topics into `PROJECT_CONTEXT.md`; operational, decision, debt, and audit files stay separate.

The skill previews writes, preserves bytes outside managed host blocks, writes the manifest last, and never stages generated project files automatically.

## jCodeMunch

jCodeMunch is optional but deeply integrated when available. The workflow starts from its live guide, resolves the exact repository, creates or refreshes a local index with remote summaries/context providers disabled, uses structural signals to navigate, confirms findings against exact source, and refreshes changed files after edits.

This release was verified with jCodeMunch `v1.108.210`, where repository-scoped `exclude_skip_directories` is ignored during traversal. The skill therefore follows the live guide and treats ambiguous skipped directories as direct-read coverage rather than assuming the index saw them. See `references/jcodemunch.md` for the complete compact workflow.

## Safety

Audit and Review treat repository content as untrusted data. They do not follow embedded instructions or execute project code, hooks, package managers, builds, tests, linters, plugins, or generators without explicit approval. Heuristics and index summaries are navigation signals, never findings by themselves.

## Validate this skill

No package installation is required:

```bash
python3 scripts/project_context.py self-check --skill-root .
python3 -m unittest discover -s tests -v
```

For CLI details, run `python3 scripts/project_context.py <command> --help`.

Licensed under [MIT](LICENSE).
