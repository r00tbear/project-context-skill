# Behavioral and trigger evals

`cases.json` contains black-box behavioral and trigger specifications. `self-check`
validates only static shape; the agent session is the harness — there is deliberately no
Python eval runner, keeping the repository stdlib-only. The mechanical halves of many
cases are already enforced by `tests/` and CI; the executable subset below covers what
only agent judgment can prove.

## Executable subset

Six cases have fixture builders (`fixtures/build.sh <case-id> <target-dir>`):
`greenfield`, `dual-host-preservation`, `literal-scope-entries`, `alias-only-import`,
`delete-safety-own-test-blocker`, `implicit-unrelated-dashboard`.

Procedure, per case:

1. Build the fixture into a scratch directory outside this repository.
2. Start a **fresh agent** with the candidate skill installed. For behavioral cases,
   explicitly invoke `$project-context` and provide only the fixture path and the case's
   `request`. For `implicit-*` cases, send only the request — invocation itself is what
   is being measured. Never reveal `expected` or `forbidden` before scoring.
3. Score exact contract outcomes against the case's `expected` and `forbidden` lists,
   binary per item; prose similarity does not count. A case passes when every `expected`
   item was met and no `forbidden` item occurred.
4. Record the verdicts in a scorecard shaped like `scorecard.template.json`. The
   scorecard is written **outside the repository** (it is run evidence, not payload);
   attach it to the GitHub Release alongside the dogfood artifacts (see RELEASING.md).
5. Remove all agent artifacts and the fixture between cases so no context leaks.

Run each case at least twice when changing orchestration or trigger metadata. Never put
a real secret or an executable untrusted hook in a fixture. Cases without fixtures
remain specifications: exercise them opportunistically during dogfood runs and record
what was observed in the same scorecard.
