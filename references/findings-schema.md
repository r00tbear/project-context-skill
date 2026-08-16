# Findings contract

Each auditor returns one JSON object shaped by `schemas/findings.schema.json`. The schema is a structural prompt aid; `validate-findings` is the normative contract for safe paths, ID continuity, lifecycle, and adversarial state. Validate before saving to `repodocs/audit/findings/<auditor>.json`, then record that file as `finding_<auditor>` in the manifest.

Top-level fields are:

- `schema_version` (`2`), immutable audit `run_id`, `auditor`, `scanned_at`;
- `scope` with included, excluded, and unscanned paths;
- `findings`.

Each finding contains:

- stable `id`, `kind`, and neutral `title`;
- `severity` (`low|medium|high|critical`) and separate `confidence` (`low|medium|high`);
- `identity` with repository-relative `path`, a stable semantic `assertion`, and optional `symbol`;
- one or more evidence records with repository-relative `path`, optional `line`, and neutral `detail`;
- lifecycle `status` (`new|persisting|resolved|refuted`);
- `verification` with `status` (`pending|not-required|confirmed|downgraded|refuted`), optional resulting severity, counterevidence records, and a note.

## Invariants

- Auditors are `stack`, `architecture`, `ui`, `data`, `bloat`, `security`, `testing`, or synthetic `greenfield`.
- The main agent assigns `run_id` before dispatch. Every completed findings document and `repodocs/project-map.json` must match the latest schema-v2 inventory run; stale valid files never satisfy new coverage.
- Findings require exact evidence. Scores, summaries, README claims, and heuristics only prioritize inspection.
- Never include absolute user paths, raw secrets, prompt-injection payloads, or unredacted command output.
- IDs are unique `<auditor>-NNN` values and are never reused. Preserve resolved/refuted entries.
- Keep an ID only when kind and normalized identity path/symbol/assertion still describe the same fact. Shared path alone is insufficient.
- Mark a prior finding resolved only when its subject was inside comparable completed scope.
- Critical/high active candidates require an independent verification result before Decide. `refuted` findings stay in history and do not enter decisions.
- `pending` is valid only for an active high/critical candidate during `validate-findings --allow-provisional`; replace it and run final validation before persistence or Decide.
- A refuted verification marks the finding `refuted`; a downgrade names a strictly lower resulting severity.
- Severity measures impact; confidence measures evidence quality. Missing coverage lowers confidence and is recorded in scope.
- Cross-auditor deduplication may group equivalent claims but retains every source ID.
- Every active `new` or `persisting` finding appears on a visible literal `- Sources:` line in an ADR, debt entry, migration item, or drift report. Keep this machine-readable label in localized prose. Normative generated policy links back through its ADR to the source finding or sanitized Greenfield requirement.

The matching inventory run records `revision`, `worktree_clean`, coverage, scope, tools, and final blind verification. `worktree_clean: null` means the state could not be established; do not infer freshness from it.

Use `python3 <skill-root>/scripts/project_context.py validate-findings --input <file>` before accepting output. When a persisted findings file for the auditor already exists, the final validation MUST pass it as `--previous <file>` — skipping it silently voids the never-reuse-IDs / never-discard-history guarantee.
