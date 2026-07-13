---
name: project-context
description: Generate and maintain project context documentation (agents.md + techstack.md, architecture.md, db-schema.md, ui-kit.md, edge-cases.md, links.md, CurrentSprint.md, LegacyWarning.md) for an existing codebase. Runs a read-only audit with subagents, detects inconsistencies ("zoo" of frameworks, duplicated UI components, redundant hand-written code), interviews the user to pick target architecture/stack, then generates docs and a migration backlog. Use whenever the user asks to audit a project, create agents.md / CLAUDE.md context files, set up project documentation for AI agents, detect tech debt, review a diff/branch/PR against generated project docs (CI quality gate), or says "запусти project-context", "сделай аудит проекта", "создай файлы контекста", "проверь diff/PR по докам".
---

# Project Context — codebase audit and context-file generation

The skill runs in 5 phases. NEVER skip phases or reorder them.
All artifacts are written to `docs/` in the project root.

## Hard rules (apply in every phase)

1. **The skill never modifies project code — in any phase.** It writes only to `docs/` and, in the project root, `agents.md` and `.jcodemunch.jsonc`.
2. **Every claim comes with evidence.** Format: file path, occurrence count, example. A claim without evidence does not become a finding.
3. **Anything irreversible is always a user question**, regardless of level: deleting code, major version bumps, paid services, DB schema changes.
4. **Auditor subagents are read-only.** They return YAML; only the main agent writes files.
5. Before asking the user anything, check `docs/decisions.md` and `docs/project-context.config.yaml` — the answer may already be recorded.
6. **jcodemunch (MCP) and ponytail (skill) are optional, but if installed, using them is mandatory** (see "Companion tools"). "Doing it by hand feels more familiar" is not an argument.
7. **Language.** All user interaction (questions, reports) and all generated docs are written in the `language` from the config; the default is English. If the user communicates in another language or asks to switch — at any point, in any phase — switch immediately and record it in the config. Machine artifacts (findings YAML, inventory, drift report, config) are always in English: ids, kinds, and structure must not change when the language does.

## Phase 0 — Preflight

1. Check whether `docs/project-context.config.yaml` exists. If yes — read it and skip the questions it already answers.
2. Determine project size (LOC, number of modules/packages):
   - **small** (< ~10k LOC, single module) — all sections go into a single `agents.md`, no separate docs; service artifacts (config, decisions.md, audit/) still live in `docs/`;
   - **medium/large** — the full file set, linked from `agents.md`.
3. Ask the user (in one message):
   - level: `novice` / `specialist` / `expert` (see references/decision-matrix.md);
   - language (default English; tell the user they can reply in any language — the interview and the generated docs will continue in it);
   - zones to leave untouched (vendored code, generated, legacy-on-purpose).
4. Check availability of the jcodemunch MCP and the ponytail skill — both are optional, but whatever is present must be used (rule 6). jcodemunch: make sure the project index exists and is fresh (offer reindexing if needed) — auditors and verify run an order of magnitude cheaper through it; if unavailable, proceed without it and mention that to the user once. ponytail: affects Generate and Review (see "Companion tools").
5. Write the answers to `docs/project-context.config.yaml`.

## Phase 1 — Audit

1. Read the findings schema: `references/findings-schema.md`.
2. Launch subagents via Task **in parallel**. Give each one a single prompt containing: the contents of `auditors/_common.md` + its own file from `auditors/` + the findings schema + the config; on a repeat run — also its previous `docs/audit/findings/<auditor>.yaml` (matching facts keep their ids). If the environment has no subagents (Codex, Cursor) — run the same prompts sequentially yourself; the rules do not change:
   - `auditors/stack.md` — dependencies, versions, duplicate-purpose libraries
   - `auditors/architecture.md` — layers, patterns, folder structure
   - `auditors/ui.md` — components, duplicates, design tokens (skip if there is no frontend)
   - `auditors/db.md` — schema, migrations, data access (skip if there is no database)
   - `auditors/bloat.md` — hand-written code duplicating library features
   - `auditors/security.md` — secrets, vulnerable dependencies, OWASP patterns, authz
   - `auditors/testing.md` — critical-path coverage, pyramid, test quality
3. Validate each auditor's YAML against the schema. Invalid — re-request from the subagent, do not fix it silently.
4. Save results to `docs/audit/findings/<auditor>.yaml` and a summary to `docs/audit/inventory.yaml` (date, versions, counters).
   Include a **health score** in the inventory: findings by severity with security-critical counted separately, % of code in the target structure (after the first generate), number of drift mismatches, critical-path test status. Default structure and formula: `examples/inventory.yaml`. Write the formula into inventory.yaml next to the value — the next run must compute it the same way and show the trend.
5. On a repeat run, compare with the previous inventory: auditors mark persisting themselves (they received the previous file); you validate the ids and mark as resolved whatever disappeared.
6. Update `last_audit` in `docs/project-context.config.yaml`.

## Phase 2 — Decide

1. Read `references/decision-matrix.md`.
2. Group conflicts by topic: stack, architecture, UI, data, redundant code.
3. For each group, pick the action from the matrix (level × severity × confidence):
   decide autonomously / propose a recommendation for confirmation / present full trade-offs.
4. Ask questions in batches by topic, not one at a time. Every option comes with evidence and the consequences of choosing it (the shape of a good question batch: `examples/decide-question.md`).
5. **Choosing the target architecture is a mandatory item of this phase** (finding `target-architecture-proposal`). Wording: "Based on your code, options X and Y fit best (structure sketches and migration cost below). Shall we fix one as the target?" The user makes this decision at every level except novice with high confidence — there the agent fixes the variant closest to the code and reports it.
6. Record every accepted decision in `docs/decisions.md` in ADR format (see templates/decisions.md): context → options → decision → consequences. Mark decisions made automatically by the agent as `auto`.

## Phase 3 — Generate

1. For each target file, take the template from `templates/` and fill it from findings + decisions, writing in the `language` from the config and matching the density and tone of `examples/` (gold-standard outputs for a fictional project):
   `techstack.md`, `architecture.md`, `db-schema.md`, `ui-kit.md`, `edge-cases.md`, `links.md`, `CurrentSprint.md`, `security.md`, `testing.md`.
   Target files describe the **target** state, not the current one.
   architecture.md must include: the chosen paradigm, the target folder tree, and a "Rules for new code" section — all new code is written to the target structure from day one, no exceptions. Existing code is not mass-rewritten: every mismatch becomes a LegacyWarning entry + a migration-backlog item and is worked off over time.
2. `CurrentSprint.md` and `edge-cases.md` cannot be derived from the audit: ask the user for the current iteration's tasks and the domain's unusual scenarios (can be batched with the phase 2 questions). No answer — generate a skeleton with TODO marks, do not invent.
3. The gap between current and target:
   - `LegacyWarning.md` — workarounds, outdated code, risks (what does NOT match the target);
   - `migration-backlog.md` — a priority-ordered plan for reaching the target state; each item: what, where (paths), why, S/M/L effort, risk.
4. **All cross-references between the generated docs and agents.md are wikilinks**: `[[techstack]]`, `[[decisions#ADR-007]]`, `[[migration-backlog#MB-003]]`. Whenever one doc mentions another (or agents.md), write the mention as a wikilink — the docs must form a connected graph, not isolated files. The templates show the pattern.
5. Generate/update `agents.md` in the root: a short core (working rules + a "which file to read for which task" matrix) and wikilinks to all files, under ~100 lines. Do not duplicate file contents inside agents.md. Include the template's conditional lines (jcodemunch, ponytail) based on whether the tool is actually present.
   Must include a **Definition of Done** — the checklist without which the agent does not consider a task complete (tests per testing.md, edge cases covered, docs updated, layer boundaries intact, no new dependencies without an ADR, security rules followed).
6. If jcodemunch is available: generate/update `.jcodemunch.jsonc` in the root — declare the layers of the target architecture (from decisions) so get_layer_violations can machine-check the boundaries. Show it to the user before writing.
7. For a small project — everything goes into a single agents.md, as sections; wikilinks then point to anchors inside it (`[[agents#Tech stack]]`) instead of separate files.
8. Never overwrite existing files silently: show a diff and ask (except at novice level — there, update and list the changes).

## Phase 4 — Verify

1. In a separate pass, check every generated file against the code: every verifiable claim (versions, paths, component names) must be confirmed, and every wikilink must point to an existing doc/anchor. With jcodemunch: get_layer_violations — target-architecture compliance; find_dead_code/find_references — freshness of ui-kit.md and LegacyWarning (mark resolved entries as done).
2. Mismatches go to `docs/audit/drift-report.md`. Critical ones — fix in the docs immediately.
3. This phase can run standalone at any moment ("check whether the docs are still accurate") — it is drift detection.

## Review mode

On request — "check my diff/branch/PR against the docs" — read `references/diff-review.md` and execute it. This closes the loop: docs are read before a task, review checks after. Suitable for CI (headless) as a quality gate.

## Companion tools

Both are optional; if installed, they must be used (rule 6).

- **jcodemunch (MCP)** — https://github.com/jgravelle/jcodemunch-mcp: auditors and verify work through symbol-level search instead of reading whole files (hints are in `_common.md` and the auditor prompts); Generate exports the target architecture's layers to `.jcodemunch.jsonc`.
- **ponytail (skill)** — https://github.com/DietrichGebert/ponytail: over-engineering prevention while writing code is already covered by its always-on mode — do not duplicate it in the generated agents.md rules. Our bloat auditor still runs (it produces structured findings about existing code for the backlog). In Review mode, do not check complexity yourself — /ponytail-review is a mandatory parallel step: run it or explicitly ask the user to.

## Keeping the docs fresh (anti-zoo for the docs themselves)

1. **Fixed file set.** No new docs are created — any new information goes into a section of an existing file. decisions.md is the only home for decisions.
2. **Minimal duplication.** Do not copy into the docs what can be derived from the code: exact dependency versions — as a link to the manifest, not a list; techstack.md holds rules and choices, not a package.json snapshot.
3. **A rule for the generated agents.md:** changed dependencies/schema/UI components — update the corresponding doc in the same change, or record the mismatch in LegacyWarning.
4. **Regular verify** — phase 4 as a standalone drift check (manually or headless in CI). Mismatches do not silently accumulate.
5. During verify, clean up what is done: mark resolved LegacyWarning entries and completed backlog items instead of leaving them hanging as current.

## Partial runs

The user may ask for individual phases: "audit only", "regenerate the docs from decisions", "check drift". Check that the artifacts of the previous phases exist; if they don't — say what has to happen first, do not invent data.
