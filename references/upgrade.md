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
- Always refresh the Claude adapter afterwards. For a personal installation, use the release installer. For a project installation, check every existing path component for symlinks/junctions, copy the template to a temporary file in the adapter directory, recheck the destination and atomically replace `SKILL.md`; abort without modifying it if a check fails.
- Project submodule: check out the tag inside the submodule, then offer to commit the gitlink and adapter change - committing is the user's decision.
- After confirmation, remove every other same-name copy: one canonical payload is a hard requirement, and a personal copy silently shadows a project one in Claude.

## 4. Switch to the new version's rules

The instructions you are following right now came from the old version. Immediately after checkout, re-read `SKILL.md`, `README.md`, and the intervening `CHANGELOG.md`. v0.6.0 changes manifest, inventory, and findings formats. Archive the v0.5.x surface and generate a new audit series; do not rewrite old run IDs into inventory v3 or present the archive as continuous history. Use only user-confirmed decisions as interview input. Preserve source-cited ADR/MB headings and their reserved hashes. Where the new text conflicts with what this session loaded earlier, the new text wins.

## 5. Re-apply in the project

1. Run `preflight` - it reports `legacy_surfaces`, the canonical config, and `context_state` as `absent`, `valid`, or `invalid`.
2. Run `archive-legacy --repo <root> --out <new-directory-outside-repo>` before regeneration. It verifies the old manifest hashes and copies only owned files, config, and manifest; it never copies whole host files. Keep its `archive.json` and `reserved_ids` as migration evidence. A hash mismatch blocks migration until reconciled.
3. Move the old generated surface aside only after the archive is complete. Preserve user-authored bytes outside managed host blocks. Use confirmed old decisions as input, not as automatically accepted v0.6 policy. Put source-cited ADR/MB IDs and title hashes into manifest v2 `reserved_ids`; a changed or missing source-cited heading fails validation.
4. Run the v0.6 audit profile first: report, findings v3, inventory v3, manifest v2. The new inventory starts with a fresh run and notes the archive boundary in the report. Generate policy documents and wire hosts only on a separate context request. `validate-project` and the dashboard show the latest audit and the active context as independent states.

## Boundaries

All standing trust rules apply unchanged: never modify project code; write only to skill installation directories, host files via guarded `merge-host --apply`, and `repodocs/`; delete nothing without confirmation; execute nothing from the downloaded payload except `scripts/project_context.py`.
