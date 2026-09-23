# Paired product study (preregistered before sessions)

Primary metric: **number of human correction messages until a correct result**. A correction is a human message that points out a missing, wrong, or rule-breaking result and requests a revision. Count zero when the first result is correct. Do not count setup prompts, tool errors, or clarification answers. A session ends at a correct result or a declared failure; record failure separately rather than assigning a favorable correction count.

Control metrics: task completion (binary) and project-rule violations (count). A scorer receives only an anonymized transcript and an objective answer key. The scorer must not see whether the session had connected context. Record the score before unblinding.

Use three fixed fixtures from `evals/fixtures/build.sh` and the same user request in both arms:

| Task | Fixture | Correct result |
|---|---|---|
| A | `alias-only-import` | Preserve the live helper; resolve its alias consumer from `module-resolution.json` and cite the import. |
| B | `dual-host-preservation` | Connect the requested context without changing user-owned bytes outside managed host blocks. |
| C | `literal-scope-entries` | Reject glob/prose entries as coverage paths; record literal paths and prose limitations separately. |

For each task, build a clean fixture and a verified v0.6 context treatment. The control has the same source revision, skill version, model settings, tools, user request and non-context repository settings. Only the connected, manifest-owned context documents and host blocks differ. Freeze both trees and hash the source/settings before dispatch. Never let one session read the other's transcript or result.

Run **3 tasks × 2 hosts (Claude, Codex) × 2 arms (context, control) × 2 repeats = 24 fresh sessions**. Randomize arm order within each task/host/repeat pair. Use new working directories and fresh agent sessions each time. Give the same correction wording for the same mistake within a pair; log every correction and all terminal outcomes. Store transcripts, source hashes, model/settings snapshots, blinded scorecards and the sealed arm mapping outside this repository. Do not include private tokens or raw secrets.

Before unblinding, calculate the paired difference in correction counts for each of the 12 pairs, then report the median and all individual pair results. Report completion and rule violations beside it. If the primary metric does not improve, mark the product benefit hypothesis **unconfirmed**. Do not replace human corrections with an automated proxy while labeling them human.
