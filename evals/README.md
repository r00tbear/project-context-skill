# Behavioral and trigger eval specifications

`cases.json` contains black-box behavioral and trigger specifications. `self-check` validates only static shape; it does not provision fixtures, launch agents, or score outcomes.

For behavioral cases, explicitly invoke `$project-context` in a fresh agent and provide only the fixture, request, and candidate skill path. For cases whose ID starts with `implicit-`, install the candidate in a fresh harness and send only the request so invocation itself is measured. Never reveal `expected` or `forbidden` before scoring.

Score exact contract outcomes, not prose similarity. Score invocation first for implicit cases. Run each case at least twice when changing orchestration or trigger metadata; structural checks remain in `tests/` and CI. Remove agent artifacts between runs to avoid leaked context. Never put a real secret or executable untrusted hook in a fixture.
