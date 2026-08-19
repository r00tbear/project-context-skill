# Upgrade mode - updating this skill and re-applying it

Runs when the user says "update the project-context skill", "обнови скилл project-context", or asks to move a project to a newer skill release. The user only asks; you inventory, update, clean up, and re-apply yourself.

## 1. Inventory every installation first

Locate every same-name copy before touching anything:

- personal: `~/.agents/skills/project-context` (canonical payload) and `~/.claude/skills/project-context` (must contain only the small adapter SKILL.md; a full tree with `auditors/` there is a legacy v0.1 clone);
- project, from the exact Git root: `.agents/skills/project-context` (plain directory or submodule), `.claude/skills/project-context`, legacy `.codex/skills/project-context`.

For each copy read its `VERSION` file; no `VERSION` file means pre-0.2. Resolve the target release with `git ls-remote --tags https://github.com/r00tbear/project-context-skill.git` and pick the highest `vX.Y.Z` unless the user pinned one.

## 2. One plan, one confirmation

Present a single plan: which copy updates to which tag, which duplicates/legacy copies go away, and what re-applying will do in the project. Removal is irreversible, so the always-ask rule covers the skill's own files too: get explicit confirmation before deleting anything, and prefer archiving (move aside as `<name>.v0.1-backup`) over deletion.

## 3. Update the payload

- Existing canonical clone: `git -C <payload> fetch --tags origin` then `git -C <payload> checkout <tag>`.
- No canonical payload yet: fresh install following the target version's README.
- Always refresh the Claude adapter afterwards: copy `<payload>/templates/host/claude-skill-adapter.md` over `.claude/skills/project-context/SKILL.md` (personal or project, wherever the adapter lives).
- Project submodule: check out the tag inside the submodule, then offer to commit the gitlink and adapter change - committing is the user's decision.
- After confirmation, remove every other same-name copy: one canonical payload is a hard requirement, and a personal copy silently shadows a project one in Claude.

## 4. Switch to the new version's rules

The instructions you are following right now came from the old version. Immediately after the checkout, re-read `SKILL.md` and `README.md` from the updated payload and follow their migration policy. Read the `CHANGELOG.md` entries between the previous and the target version: each entry states `Regeneration required: yes/no`, which decides whether the project must be re-applied. Releases that tighten contracts are fresh-install-only (v0.4 tightened scope, citation, and jCodeMunch contracts): do not transform older findings, inventory, manifest, or generated project files in place. Archive the old generated surface with approval, reinstall/re-apply from scratch, and use sanitized prior decisions only as user-confirmed interview input. Where the new text conflicts with what this session loaded earlier, the new text wins; if the difference is substantial, tell the user a fresh session is the reliable path.

## 5. Re-apply in the project

1. Run `preflight` - it reports `legacy_surfaces`, the canonical config, and `context_state` as `absent`, `valid`, or `invalid`.
2. Offer to archive legacy artifacts rather than delete them; keep the old decisions content at hand - it answers the new Decide interview quickly. In the same step, append the archive path to `.git/info/exclude` (append-only, preserve existing entries): an archive that shows up in `git status` as untracked is one `git add -A` away from being committed.
3. On an older project, `validate-project` may fail on skill version or the v0.3 schema-v2 contracts. That is the expected fresh-install signal, not an error to suppress or patch around.
4. Run the normal flow from a fresh generated surface - Audit -> Decide, confirming only sanitized carried-over decisions -> Generate -> Wire -> blind Verify, writing the manifest last. Open the dashboard only after `validate-project` succeeds.

## Boundaries

All standing trust rules apply unchanged: never modify project code; write only to skill installation directories, host files via `merge-host`, and `repodocs/`; delete nothing without confirmation; execute nothing from the downloaded payload except `scripts/project_context.py`.
