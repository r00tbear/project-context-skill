# CurrentSprint.md — current iteration

One sprint — one agent — one context. Shelfie, day 0: the repo is empty, ADR-001..003 are decided.

## Sprint tasks
| id | task | status | priority |
|---|---|---|---|
| S0-1 | git init, .gitignore, LICENSE, README stub | todo | P1 |
| S0-2 | scaffold: Vite + React + TS client, Express + TS server ([[decisions#ADR-002]]) | todo | P1 |
| S0-3 | CI: lint + typecheck + tests on every PR | todo | P1 |
| S0-4 | walking skeleton: "add a book" end to end (form → API → Postgres → list) | todo | P2 |
| S0-5 | seed [[db-schema]] with the books table from the walking skeleton | todo | P2 |

## Related files
[[architecture]] — the target folder tree to scaffold into; [[techstack]] — pinned versions.

## Sprint constraints
No auth, no deployment, no second feature this sprint.

## Expected outcome
A PR passing CI where a book added in the UI survives a server restart.

Related: [[agents]] · [[migration-backlog]]
