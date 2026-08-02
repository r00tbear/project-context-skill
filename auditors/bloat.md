# Bloat auditor

Follow `_common.md`. Inspect hand-maintained implementation and project material for:

- code/config already covered by the standard library, native platform, toolchain, or an installed dependency;
- semantic duplicates across functions, rules, workflows, assets, targets, or configuration;
- dead/superseded material after checking real entry points, registration, generation, and dynamic use;
- pass-through wrappers and one-implementation abstractions with no evidenced boundary;
- repeated AI-style boilerplate or competing neighboring solutions.

Typical kinds: `reinvented-library-feature`, `copy-paste-duplicate`, `dead-code`, `needless-abstraction`.

Prefer delete, reuse, or merge. Confirm similarity/dead-code signals with exact references and use delete-safety analysis before recommending removal. A full second dependency/subsystem for the same purpose belongs to the stack auditor.
