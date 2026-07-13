# decisions.md — accepted decisions (ADR)

## ADR-001: Single HTTP client — axios
- Date: 2026-07-13 · Level: specialist · Accepted by: user
- Context: two HTTP clients coexist — axios (34 usages / 12 files) and a hand-written fetch wrapper (9 usages / 4 files); finding stack-001
- Options: A — keep axios, migrate 4 files (S); B — keep the wrapper, migrate 12 files, lose interceptors (M)
- Decision: A (axios)
- Consequences: the fetch wrapper goes to [[techstack#Banned]]; migration tracked as [[migration-backlog#MB-004]]
