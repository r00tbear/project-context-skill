# Claude Code and Codex integration

Use one shared `PROJECT_CONTEXT.md` and one canonical skill payload. Do not generate separate Claude and Codex contexts.

## Managed host blocks

Merge exactly one block from `templates/host/CLAUDE.block.md` or `templates/host/AGENTS.block.md` into each enabled root file while preserving every byte outside it. New blocks are prepended so the shared-context instruction stays inside host read limits. Existing blocks keep their location.

The Claude import must be active text. Never use symlinks, lowercase `agents.md`, or extra case-variant/sibling host files next to `CLAUDE.md`/`AGENTS.md`. Duplicate/unbalanced markers, unclear ownership, or overlapping shared policy in the surviving host text block the merge until reconciled.

Preview without writing:

```bash
python3 <skill-root>/scripts/project_context.py merge-host --host claude --input CLAUDE.md
python3 <skill-root>/scripts/project_context.py merge-host --host codex --input AGENTS.md
```

Apply atomically — write to a temp file and move it. **Never redirect onto the input file in the same command** (`--input CLAUDE.md > CLAUDE.md` makes the shell truncate the file before it is read; the validator now refuses the resulting empty input unless `--allow-create` is passed, which is only for a genuinely new file):

```bash
python3 <skill-root>/scripts/project_context.py merge-host --host claude --input CLAUDE.md > CLAUDE.md.new
mv CLAUDE.md.new CLAUDE.md
```

Re-read each host file between preview and apply; abort if it changed — or pin it with `--expected-sha256 sha256:<hex>` of the previewed text (the optimistic lock fails the merge if the file moved underneath).

## One payload for both hosts

Keep the full pinned skill at `.agents/skills/project-context`. Codex discovers it directly. Copy `templates/host/claude-skill-adapter.md` to `.claude/skills/project-context/SKILL.md`; the adapter loads the canonical `.agents` payload using `${CLAUDE_SKILL_DIR}`.

Before installation, find personal and repository-local same-name copies under `.agents/skills`, `.claude/skills`, and `.codex/skills`. Claude gives a personal same-name skill precedence over a project skill; Codex retains same-name skills at different paths. Either behavior defeats a single pinned payload, so archive/remove every collision and reinstall fresh.

## Optional Codex fallback

Basic shared context needs only `CLAUDE.md` and `AGENTS.md`; do not install executable lifecycle hooks. With explicit approval, merge `templates/host/codex-config.fragment.toml` into `.codex/config.toml`. It makes Codex consider `PROJECT_CONTEXT.md` only when `AGENTS.md` is absent. Preserve any existing fallback filenames and never replace the whole TOML file.

After writing the manifest last, run `validate-project` manually, open fresh Claude and Codex sessions, and confirm that both report the same root context and manifest hash.
