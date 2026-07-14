# project-context — codebase audit and context-file generation skill

A skill for AI agents (Claude Code, Codex, Cursor) that, in an existing repository:
1. **Audit** — read-only audit by subagents: stack, architecture, UI, DB, redundant code, security, tests. Every finding comes with evidence. A summary health score with a trend across runs.
2. **Decide** — an interview with the user about the conflicts (framework "zoo", duplicated components). Depth depends on the level: novice / specialist / expert. Decisions → `docs/decisions.md` (ADR).
3. **Generate** — produces `agents.md` (with a Definition of Done) + `docs/`: techstack, architecture, db-schema, ui-kit, edge-cases, links, CurrentSprint, security, testing — the target state; LegacyWarning + migration-backlog — the gap from the current one. All docs and agents.md are cross-referenced with wikilinks (`[[techstack]]`, `[[decisions#ADR-007]]`) into a connected graph.
4. **Verify** — checks the docs against the code, drift report.
5. **Review** — checks a diff/PR against all the docs (architecture, stack, ui-kit, security, tests, DoD); also works as a CI quality gate. Complexity/over-engineering is [ponytail](https://github.com/DietrichGebert/ponytail)'s territory (/ponytail-review) when installed; the skill does not duplicate it.

Works on empty projects too (**greenfield mode**): the audit is replaced by a requirements interview, the docs fix the chosen stack/architecture as the plan, the first sprint is the bootstrap plan, and the Review gate guards the plan from the very first PR.

## Layout
```
SKILL.md                 # core: phases, rules, orchestration
auditors/                # prompts of the read-only subagents (+ _common.md)
references/
  findings-schema.md     # format of auditor findings
  decision-matrix.md     # level × severity × confidence → action
templates/               # templates of all generated docs + config
examples/                # gold-standard outputs for a fictional project (few-shot anchors)
```

## Installation

### Claude Code
For all projects (personal):
```bash
git clone https://github.com/r00tbear/project-context-skill.git ~/.claude/skills/project-context
```
For a single project (inside the repository, for the whole team):
```bash
git submodule add https://github.com/r00tbear/project-context-skill.git .claude/skills/project-context
```
Run Claude Code from the repository root. Trigger: "audit the project" / "create context files" or `/project-context`. Update: `git pull` in the skill folder.

### Codex CLI
Codex has no native skills — wire it up through AGENTS.md:
```bash
git submodule add https://github.com/r00tbear/project-context-skill.git tools/project-context
```
Add to the project's `AGENTS.md`:
```
To audit the project and generate context files, read tools/project-context/SKILL.md
and follow it. The skill's supporting files are in the same folder.
```
Codex has no subagents: the auditors run sequentially in the main context (SKILL.md allows this, it is just slower).

### Cursor
```bash
git submodule add https://github.com/r00tbear/project-context-skill.git .cursor/tools/project-context
```
Create a rule `.cursor/rules/project-context.mdc` (type: Agent Requested / manual invocation):
```
---
description: Project audit and context-file generation (agents.md, techstack, architecture...)
---
Read .cursor/tools/project-context/SKILL.md and follow it.
```
Cursor's rule formats change — check the current Cursor documentation.

## What you get in the project
```
agents.md                        # core + links (for Codex, duplicate/symlink to AGENTS.md; for Claude Code — to CLAUDE.md)
docs/
  techstack.md ... CurrentSprint.md
  LegacyWarning.md  migration-backlog.md  decisions.md
  project-context.config.yaml
  audit/findings/*.yaml  audit/inventory.yaml
```

## Optional: jcodemunch and ponytail

Both tools are optional, but when installed the skill must use them (checked in Preflight).

- **[jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp)** — an MCP server for symbol-level code retrieval. Auditors work through symbol search instead of reading whole files (an order of magnitude cheaper in tokens), the bloat auditor leans on find_dead_code/find_references, and the target architecture from decisions is exported to `.jcodemunch.jsonc` — get_layer_violations machine-checks layer boundaries during Verify and afterwards. Without jcodemunch everything works via grep/file reads, just costs more. Install: see the [jcodemunch-mcp README](https://github.com/jgravelle/jcodemunch-mcp#readme).
- **[ponytail](https://github.com/DietrichGebert/ponytail)** — complexity and over-engineering control ("the laziest senior dev in the room"). With ponytail installed, the generated agents.md does not duplicate its rules, and Review mode is complemented by a mandatory /ponytail-review. Install in Claude Code:
  ```
  /plugin marketplace add DietrichGebert/ponytail
  ```

## Language
English by default. Name any language in the Preflight question — or simply reply in it at any point — and the interview and all generated docs continue in that language (`language` in `docs/project-context.config.yaml`).

## Philosophy
- The docs describe the **target** state; the gap from the current one lives in LegacyWarning and migration-backlog.
- `decisions.md` is the source of truth: reconsidered a decision → regenerate the docs.
- Auditors are read-only and never come without evidence.
- Anything irreversible (code deletion, major bumps, DB schema, paid services) is always a user question, at every level.

## License
[MIT](LICENSE)
