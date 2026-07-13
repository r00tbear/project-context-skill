# Review mode — checking a diff against the project docs

Runs on request ("check my diff/branch/PR against the project docs") or headless in CI. Works ONLY when the generated docs exist; otherwise offer to run the full skill first.

Input: a branch/PR diff (git diff <base>...HEAD) or staged changes.

Checks (each against its doc):
1. **Architecture** (architecture.md): new files land in the target structure; import directions do not violate the layers (with jcodemunch — get_layer_violations on the affected modules).
2. **Stack** (techstack.md): no new dependencies without an ADR; no banned libraries/versions used.
3. **UI** (ui-kit.md): no new components duplicating the canonical ones; styles via tokens.
4. **Data** (db-schema.md): schema changes go through migrations; queries live in the right layer.
5. **Security** (security.md): secrets, validation at new boundaries, authz on new endpoints.
6. **Tests** (testing.md + DoD): new functionality is covered at the right pyramid level.
7. **Docs** (freshness rule): if the diff changes dependencies/schema/components — was the corresponding doc or LegacyWarning updated.

Do not check over-engineering/complexity here — that is ponytail's territory. If it is installed, /ponytail-review is a mandatory parallel part of the review: run it yourself or explicitly ask the user to.

Output: a verdict — pass / pass-with-notes / fail + the list of violations (file:line → which doc/rule is violated → how to fix). Fix nothing yourself without an explicit request.

Verdict rubric:
- **fail** — any violation of security.md, a layer-boundary break, a banned or ADR-less new dependency, or a DB schema change without a migration;
- **pass-with-notes** — everything else (convention drift, a missing doc update, a test-level mismatch);
- **pass** — no violations.
