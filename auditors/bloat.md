# Bloat auditor

Follow `_common.md`. Inspect hand-maintained implementation and project material for:

- code/config already covered by the standard library, native platform, toolchain, or an installed dependency;
- semantic duplicates across functions, rules, workflows, assets, targets, or configuration;
- dead/superseded material after checking real entry points, registration, generation, and dynamic use;
- pass-through wrappers and one-implementation abstractions with no evidenced boundary;
- repeated AI-style boilerplate or competing neighboring solutions.

Typical kinds: `reinvented-library-feature`, `copy-paste-duplicate`, `dead-code`, `needless-abstraction`.

Prefer delete, reuse, or merge. A full second dependency/subsystem for the same purpose belongs to the stack auditor.

Before recommending any removal:

- corroborate per `_common.md`: two differently-shaped index queries in agreement plus one non-index confirmation, and for any importer claim a mandatory identifier-level check over the exported names. An empty importer list alone never proves deadness - alias, workspace-name, and barrel imports hide from file-level queries, and a path absent from disk is an index artifact, not dead code;
- run delete-safety analysis and classify every blocker: removed by the same proposed change, the symbol's own test, or an independent consumer. Only the independent consumer blocks; record the classification in the finding, never paraphrase a terminal verdict as "safe", and never quote the call's confidence as safety evidence - it describes the verdict, not the deletion;
- for a user-facing surface claimed unreachable, superseded, or removable, state four facts separately, each with its own evidence record: route or handler registration, inbound navigation to it, server-side mount, and consumers of the modules beneath it. Removability of an entry point never implies removability of what it imports; establish that per module.
