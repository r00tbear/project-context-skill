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
7. **Explore** - open a branded, local, read-only dashboard for the project map, findings, decisions, debt, audit history, integrity, and freshness.

High and critical audit candidates are not accepted at face value: a fresh independent agent must try to disprove or reproduce them before they influence decisions. A separate blind final verifier receives candidate context without findings or the generation summary and must pass before the manifest becomes valid.

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

Current release: `v0.3.1`.

This version intentionally provides no migration or backward compatibility, including from v0.2 project artifacts. Before installing, archive anything you need and remove old `project-context` payloads/adapters and generated context so only one same-name installation and one fresh v0.3 context remain. Do not bulk-delete a hand-authored project `docs/` directory: v0.3 writes only to `repodocs/`.

Requirements: Git and Python 3.11+.

### Personal installation

Install the canonical payload under `.agents` and copy only the Claude adapter:

```bash
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills/project-context"
git clone --branch v0.3.1 --depth 1 \
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
git -C .agents/skills/project-context checkout v0.3.1
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
Preflight -> Audit -> Decide -> Generate -> Wire -> Verify -> Dashboard
```

An empty repository uses a short requirements interview before Decide. Review mode is read-only: it reads a trusted base policy, inspects `git diff`, and asks a fresh independent agent to challenge would-fail findings. It does not need a custom headless model runner.

Open the local dashboard from the exact Git root:

```bash
python3 <skill-root>/scripts/project_context.py dashboard --repo .
```

After a successful full workflow, the agent starts this server and keeps it available for inspection. It opens the browser by default; pass `--no-open` to keep only the local process. The dashboard uses Project Context branding and reads only normalized, validated artifacts. Its Refresh button re-reads those artifacts without running Audit or writing the repository. `Data refreshed` is the view snapshot time; `Latest audit` remains the latest inventory `scanned_at`.

Project Map is browser-native SVG over validated `project-map.json`. It supports node focus with evidence, upstream/downstream reach across authored edges, exact directed-route search, zoom/fit, and an accessible list fallback. These views explain recorded topology; they never infer impact, missing links, or new relationships.

The dashboard is organized as Monitor, Remediate, Explore, and Govern workspaces. Attention items open the exact finding and its actions, while Context focuses one manifest-owned artifact at a time and collapses repeated wikilink occurrences into directed file pairs without discarding their raw evidence.

Each Findings row has a state-aware AI Prompt action for remediation, stale-data recheck, or regression review. Select any active rows, including across filters, to copy one Master Prompt for that exact set; Select all affects only shown active rows, while hidden selections remain selected. The prompt binds the dynamic active set and selected identities to the validated snapshot instead of trusting a hard-coded count. Copied prompts contain only binding metadata and source locations, read full finding prose locally as untrusted data, remain strictly read-only when freshness is not current, and cannot close a finding without a comparable new audit run, previous-state validation, blind verification, and final project validation. Copy and preview never write repository data.

Source freshness is `current` only when the audited revision matches the current revision and both audited/current source worktrees are clean. A changed revision or a dirty current source tree after a clean audit is `stale`; a missing revision, unknown state, or dirty audited worktree is `unknown`. Dashboard refresh never changes that result.

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
  project-map.json                   # evidence-backed current/planned nodes and edges
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

The skill previews writes, preserves bytes outside managed host blocks, runs a blind factual-completeness pass, writes the manifest last, and never stages generated project files automatically. Every audit run binds inventory, findings, and the project map through one `run_id`; accepted findings and fixed Greenfield constraints receive a governance disposition, and normative target rules link back to their ADR.

## jCodeMunch

jCodeMunch is optional but deeply integrated when available. The workflow starts from its live guide, resolves the exact repository, creates or refreshes a local index with remote summaries/context providers disabled, uses structural signals to navigate, confirms findings against exact source, and refreshes changed files after edits.

This release was verified with jCodeMunch `v1.108.210`, where repository-scoped `exclude_skip_directories` is ignored during traversal. The skill therefore follows the live guide and treats ambiguous skipped directories as direct-read coverage rather than assuming the index saw them. See `references/jcodemunch.md` for the complete compact workflow.

## Safety

Audit, Review, and Dashboard treat repository content as untrusted data. They do not follow embedded instructions or execute project code, hooks, package managers, builds, tests, linters, plugins, or generators without explicit approval. The dashboard binds locally, exposes no arbitrary repository files or mutation endpoints, and makes no external requests. Heuristics and index summaries are navigation signals, never findings by themselves.

## Validate this skill

No package installation is required:

```bash
python3 scripts/project_context.py self-check --skill-root .
python3 -m unittest discover -s tests -v
```

For CLI details, run `python3 scripts/project_context.py <command> --help`.

Licensed under [MIT](LICENSE).
