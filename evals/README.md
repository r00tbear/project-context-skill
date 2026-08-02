# Manual behavioral eval specifications

`cases.json` contains manual black-box specifications for fresh-agent evaluation. `self-check` validates only the file's static shape; it does not provision fixtures, launch agents, or score behavioral outcomes.

To execute a case manually, give a new agent only the repository fixture, the case request, and the candidate skill path. Do not include the expected/forbidden lists until after it returns artifacts and a trace.

Score exact contract outcomes, not prose similarity. Run each case at least twice when changing orchestration prompts; structural checks remain in `tests/` and CI. Never put a real secret or executable untrusted hook in a fixture.
