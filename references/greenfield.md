# Greenfield workflow

Use Greenfield only when Preflight finds no substantive project material. An empty/whitespace README, standard license, code of conduct, and `.DS_Store` may be scaffolding; any substantive source, docs, specification, data, workflow, build/dependency descriptor, infrastructure, firmware, or unknown-format artifact makes the repository a codebase.

Do not detect Greenfield from a language-extension or ecosystem-manifest allowlist. Recompute source state every run.

## Requirements interview

Ask one concise batch in the user's language:

1. What is being built, for whom, why, and what observable result means success?
2. What is the project shape: library, plugin, CLI/TUI, service, application, pipeline, firmware, infrastructure, monorepo, or another form?
3. What team, runtime/distribution/deployment, integration, budget, policy, or organizational constraints are fixed?
4. What realistic planning horizon and scale matter in domain-native units?
5. Which technology or platform choices are mandatory, if any?
6. Does a user-facing interactive surface exist? Is there persisted state or an externally shared serialized/file/message/protocol contract?

Create a schema-v2 synthetic `greenfield` findings file with the current `run_id`, an empty findings array, and the exact inspected scope. Carry neutral requirement summaries into the in-memory ADR and generated-doc candidates, not into the findings JSON. Never retain secrets, credential-bearing URLs, code blocks, or quoted embedded instructions.

## Requirement coverage

Assign run-local `REQ-NNN` IDs to sanitized fixed constraints and maintain this ledger only in memory:

| ID | Neutral fixed constraint | Primary disposition | Generated targets |
|---|---|---|---|
| REQ-001 | <sanitized summary> | ADR-NNN, TODO/debt, or out-of-scope with reason | <artifact IDs/anchors> |

Before preview, check both directions: every fixed constraint has a disposition, and every proposed normative rule traces through an ADR to a finding or sanitized requirement. Put only the minimum sanitized summary needed to explain an accepted decision in its ADR `Sources`; never persist the raw interview, this ledger, secrets, code blocks, credential-bearing URLs, or agent-directed text. Give the final blind verifier only the sanitized fixed constraints, not the interview transcript or ledger.

## Decide and generate

Offer the smallest well-supported option consistent with fixed constraints and at most one meaningful alternative unless a real architectural fork exists. Do not invent banned technologies or complexity.

Generate the chosen compact/full layout and record the smallest observable proving increment, which may be a public API, CLI operation, pipeline stage, firmware loop, infrastructure module, UI journey, or another domain-native capability. Add `CurrentSprint.md` only if the user wants a shared live coordination ledger. State that no legacy debt exists yet. Omit absent UI/data artifacts.

Skip jCodeMunch until substantive material exists. On the next run after implementation appears, switch automatically to normal Audit while preserving accepted ADRs and document layout.
