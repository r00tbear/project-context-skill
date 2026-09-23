<p align="center">
  <img src="assets/project-context-logo.svg" alt="Project Context" width="360">
</p>

<p align="center">
  <a href="https://github.com/r00tbear/project-context-skill/actions/workflows/validate.yml"><img src="https://github.com/r00tbear/project-context-skill/actions/workflows/validate.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/r00tbear/project-context-skill/tags"><img src="https://img.shields.io/github/v/tag/r00tbear/project-context-skill?label=release&color=bd5734" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-29862d" alt="MIT license"></a>
</p>

**Project Context** teaches your AI coding agents the truth about your repository — once, with evidence — so Claude Code and Codex stop guessing your stack, your architecture, and your rules on every session.

Point it at any Git repository and it will:

1. **Audit** the code with seven read-only specialists (stack, architecture, security, testing, dead weight, plus UI and data when they exist). Every claim comes with a file-and-line receipt, and every serious finding must survive an independent attempt to disprove it.
2. **Save a report** with coverage, findings and open questions, then open a local read-only dashboard.
3. **On a separate request, interview you** about findings and accepted rules.
4. **Generate and connect** one shared `PROJECT_CONTEXT.md` plus supporting `repodocs/` documents for Claude and Codex. Later audits preserve that verified context until you request an update.

It is deliberately technology-neutral: a library, a CLI, a firmware workspace, an infra repo, or a monorepo all work. The skill discovers what your project actually is instead of assuming a web app with a SQL database.

## Install

One command. You need `git` and Python 3.11+ — the installer checks both and tells you what is missing.

**macOS / Linux** (also WSL and Git Bash):

```bash
curl -fsSL https://raw.githubusercontent.com/r00tbear/project-context-skill/main/install.sh | bash
```

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/r00tbear/project-context-skill/main/install.ps1 | iex"
```

Both scripts do exactly the same thing: install the latest release under `~/.agents/skills/project-context`, add the small Claude adapter, **install and register [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp)** (the local code index the skill requires — see Requirements), archive any older copy they replace into `~/.skill-backups/` (nothing is ever deleted), and verify the result with the skill's own `self-check`. Re-running the same command later **updates** everything. Read them first if you like — they are short and boring: [install.sh](install.sh) · [install.ps1](install.ps1).

Already installed? You can also just tell your agent: **“update the project-context skill”** — it knows how ([references/upgrade.md](references/upgrade.md)).

## First run

Open Claude Code (or Codex) in your repository and say:

```text
audit this repository with project-context
```

The skill walks you through it from there. A few useful phrases afterwards:

| You say | What happens |
|---|---|
| “audit this repository” | Saves findings, coverage, report and dashboard; no policy or host writes |
| “generate and connect project context” | Confirms decisions, generates policy, verifies, and connects enabled hosts |
| “refresh the project context” | Re-audits affected areas and previews any proposed policy update |
| “open the project context dashboard” | Local read-only dashboard of the latest audit and connected context |
| “check this diff against the project docs” | Reviews a branch/PR against your recorded rules |
| “update the project-context skill” | Self-update to the latest release |

Empty repository? The first audit records requirements and open questions. It does not invent code defects or connect context.

## What you get in your repository

```text
PROJECT_CONTEXT.md            # created only when you request shared context
CLAUDE.md / AGENTS.md         # managed blocks added only with connected context
repodocs/                     # everything generated lives here, never in your docs/
  decisions.md                # your accepted decisions (ADRs) — the source of truth
  techstack.md, architecture.md, security.md, testing.md, edge-cases.md, ...
  LegacyWarning.md            # honest list of what does not match the target yet
  migration-backlog.md        # ordered plan to get there
  audit/                      # reports, findings with evidence, run history, drift report
```

An audit-only repository has just the config, audit report, findings, inventory and manifest. Connected documents describe the **target** state you chose; new findings remain pending until you decide whether to change policy.

## The dashboard

After a successful run the skill opens a local, read-only dashboard (or ask for it anytime):

- **Monitor** — validation state, auditor coverage, and the items that need attention first;
- **Remediate** — every finding with original and effective severity, plus copyable agent prompts (per finding, a master prompt for a selection, or a vendor-neutral task list for your tracker);
- **Explore** — an interactive project map, bounded plain-text previews of manifest-owned documents, and an inventory of files that instruct agents. Host configuration contents are withheld;
- **Govern** — decisions, technical debt, audit history, and integrity checks.

It binds to localhost, never executes your code, never calls the network, and never dresses up a number: stale is shown as stale, unscanned as unscanned.

## Safety promises

- The skill **never modifies your source code**. It writes only the generated context files listed above, and the installer never writes into your project directories at all.
- Auditors are **read-only** and treat all repository content — including text addressed to AI agents — as untrusted data, never as instructions.
- Nothing irreversible happens without asking you first; previews come before writes.
- Secrets found during the audit are reported by location and type, **never by value**. The dashboard withholds host-configuration file contents for the same reason.
- Every serious finding is independently challenged. When context is generated, fresh blind verifiers check its documents for at most eight passes. Document verification failure is visible separately from audit coverage.

## Requirements

- Git, Python 3.11+ (the skill itself is standard library only)
- Claude Code and/or Codex
- [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp) — the local, offline code index the skill audits through. **The installer installs and registers it for you** (via `uv` or `pipx`); it makes audits an order of magnitude cheaper and lets structural claims be machine-checked instead of guessed. It runs entirely on your machine; the skill uses its private/offline profile.

<details>
<summary><b>Team / project installation (pin the skill inside the repository)</b></summary>

Pin the skill as a submodule at `.agents/skills/project-context` so the team runs the same release. Before adding it, check every existing component of that path and of `.claude/skills/project-context/SKILL.md` for symlinks or junctions, including backup destinations. Archive collisions first. Add the submodule at a release tag, then copy its `templates/host/claude-skill-adapter.md` to a temporary file beside the adapter target. Recheck the paths and atomically replace `SKILL.md`; abort on any failed check. The personal installers show the platform-specific path guards and replacement sequence.

Commit `.gitmodules`, the submodule gitlink, and the adapter. New clones initialize it with:

```bash
git submodule update --init -- .agents/skills/project-context
```

Do not keep a personal copy at the same time — Claude gives personal skills precedence, which silently defeats the pinned version. Do not use a direct `cp` over an existing adapter or follow a linked path.

</details>

<details>
<summary><b>Manual installation (what the one-liner does)</b></summary>

Use the reviewed [POSIX installer](install.sh) or [PowerShell installer](install.ps1) as the manual procedure: read it, pin `PROJECT_CONTEXT_VERSION` to the desired tag, and run it locally. It checks all destination and backup components before mutation and again before each write, then replaces the adapter atomically. An abbreviated `mkdir; git clone; cp` sequence omits those guards. The canonical payload lives under `.agents` (Codex discovers it directly); `.claude` holds only a small adapter pointing at it. With a custom `CLAUDE_CONFIG_DIR`, use a guarded project installation instead so the relative path stays stable.

On Windows the same layout lives under `%USERPROFILE%\.agents` and `%USERPROFILE%\.claude`; use the PowerShell installer for junction checks and atomic adapter replacement.

</details>

<details>
<summary><b>How the shared Claude + Codex model works</b></summary>

```text
.agents/skills/project-context/       # full skill, discovered by Codex
.claude/skills/project-context/       # tiny Claude adapter to the same payload
PROJECT_CONTEXT.md                    # shared generated context
CLAUDE.md                             # managed @PROJECT_CONTEXT.md import
AGENTS.md                             # managed instruction to read PROJECT_CONTEXT.md
.codex/config.toml                    # optional passive Codex fallback
```

One canonical payload, one canonical context. The skill never creates lowercase `agents.md`, never writes into a hand-authored `docs/`, and never replaces `CLAUDE.md` or `AGENTS.md` wholesale — it merges one clearly marked block and preserves every other byte. Details: [references/host-integration.md](references/host-integration.md).

</details>

<details>
<summary><b>Upgrading across major versions</b></summary>

Releases are immutable tags; [CHANGELOG.md](CHANGELOG.md) states whether regeneration is required. v0.6.0 archives v0.5.x artifacts and starts a new audit history. Confirmed decisions help the new interview; source-cited ADR/MB IDs remain reserved. Details: [references/upgrade.md](references/upgrade.md).

</details>

<details>
<summary><b>Validate this repository / contribute</b></summary>

No packages required:

```bash
python3 scripts/project_context.py self-check --skill-root .
python3 -m unittest discover -s tests -v
```

Layout: `SKILL.md` is the entry point; `auditors/` are the subagent prompts; `references/` hold the deep workflow contracts; `templates/` and `examples/` shape the generated output; `scripts/project_context.py` is a stdlib-only validator and dashboard server; `evals/` describes expected behavior with an executable subset. Releases follow [RELEASING.md](RELEASING.md).

</details>

## License

[MIT](LICENSE)
