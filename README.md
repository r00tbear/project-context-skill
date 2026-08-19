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
2. **Interview you** about the conflicts it found, and record your decisions.
3. **Generate** one shared context: `PROJECT_CONTEXT.md` at the root plus supporting documents under `repodocs/` — wired into `CLAUDE.md` and `AGENTS.md` so both agents read the same truth (your own content in those files is never touched).
4. **Open a local dashboard** where you can explore the findings, the project map, every file that instructs an agent, and copy ready-made prompts to fix things.

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
| “audit this repository” | Full run: audit → interview → generate → wire → verify → dashboard |
| “refresh the project context” | Re-audits what changed and regenerates the docs |
| “open the project context dashboard” | Local read-only dashboard of the last validated run |
| “check this diff against the project docs” | Reviews a branch/PR against your recorded rules |
| “update the project-context skill” | Self-update to the latest release |

Empty repository? That works too — the audit is replaced by a short requirements interview, and the generated docs become the plan your first PRs are reviewed against.

## What you get in your repository

```text
PROJECT_CONTEXT.md            # the one document both agents read
CLAUDE.md / AGENTS.md         # your files; the skill only adds a small managed block
repodocs/                     # everything generated lives here, never in your docs/
  decisions.md                # your accepted decisions (ADRs) — the source of truth
  techstack.md, architecture.md, security.md, testing.md, edge-cases.md, ...
  LegacyWarning.md            # honest list of what does not match the target yet
  migration-backlog.md        # ordered plan to get there
  audit/                      # findings with evidence, run history, drift report
```

The documents describe the **target** state you chose; the gap between target and today lives in `LegacyWarning.md` and the backlog, so nothing gets silently rewritten.

## The dashboard

After a successful run the skill opens a local, read-only dashboard (or ask for it anytime):

- **Monitor** — validation state, auditor coverage, and the items that need attention first;
- **Remediate** — every finding with original and effective severity, plus copyable agent prompts (per finding, a master prompt for a selection, or a vendor-neutral task list for your tracker);
- **Explore** — an interactive project map and an inventory of **every file that instructs an agent** in your repo: who reads it, duplicates across hosts, and whether its links still resolve;
- **Govern** — decisions, technical debt, audit history, and integrity checks.

It binds to localhost, never executes your code, never calls the network, and never dresses up a number: stale is shown as stale, unscanned as unscanned.

## Safety promises

- The skill **never modifies your source code**. It writes only the generated context files listed above.
- Auditors are **read-only** and treat all repository content — including text addressed to AI agents — as untrusted data, never as instructions.
- Nothing irreversible happens without asking you first; previews come before writes.
- Secrets found during the audit are reported by location and type, **never by value**.
- Every serious finding is independently challenged before you see it, and a final blind verifier checks the generated docs without seeing the findings.

## Requirements

- Git, Python 3.11+ (the skill itself is standard library only)
- Claude Code and/or Codex
- [jCodeMunch](https://github.com/jgravelle/jcodemunch-mcp) — the local, offline code index the skill audits through. **The installer installs and registers it for you** (via `uv` or `pipx`); it makes audits an order of magnitude cheaper and lets structural claims be machine-checked instead of guessed. It runs entirely on your machine; the skill uses its private/offline profile.

<details>
<summary><b>Team / project installation (pin the skill inside the repository)</b></summary>

Pin the skill as a submodule so the whole team runs the same version. From the exact Git root:

```bash
mkdir -p .agents/skills .claude/skills/project-context
git submodule add https://github.com/r00tbear/project-context-skill.git .agents/skills/project-context
git -C .agents/skills/project-context checkout "$(git -C .agents/skills/project-context describe --tags --abbrev=0)"
cp .agents/skills/project-context/templates/host/claude-skill-adapter.md .claude/skills/project-context/SKILL.md
```

Commit `.gitmodules`, the submodule gitlink, and the adapter. New clones initialize it with:

```bash
git submodule update --init -- .agents/skills/project-context
```

Do not keep a personal copy at the same time — Claude gives personal skills precedence, which silently defeats the pinned version. Before writing, check for symlinks with `find -P .agents .claude -type l -print` and stop if any appear.

</details>

<details>
<summary><b>Manual installation (what the one-liner does)</b></summary>

```bash
mkdir -p "$HOME/.agents/skills" "$HOME/.claude/skills/project-context"
git clone --branch <latest-tag> --depth 1 \
  https://github.com/r00tbear/project-context-skill.git \
  "$HOME/.agents/skills/project-context"
cp "$HOME/.agents/skills/project-context/templates/host/claude-skill-adapter.md" \
  "$HOME/.claude/skills/project-context/SKILL.md"
```

The canonical payload lives under `.agents` (Codex discovers it directly); `.claude` holds only a small adapter pointing at it. This layout assumes the default `~/.claude`; with a custom `CLAUDE_CONFIG_DIR`, use the project installation instead so the relative path stays stable.

On Windows the same layout lives under `%USERPROFILE%\.agents` and `%USERPROFILE%\.claude`; run the steps above in Git Bash, or use the PowerShell installer which handles the paths for you.

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

Releases are immutable tags; [CHANGELOG.md](CHANGELOG.md) states per release whether generated context must be re-applied. Re-running the installer (or asking your agent to update) moves you to the latest release. A patch release only warns; a minor/major release makes an old project report its context as invalid — that is the signal to re-run the audit, which regenerates everything and re-asks only what changed. Your accepted decisions are carried into the new interview, not thrown away. Details: [references/upgrade.md](references/upgrade.md).

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
