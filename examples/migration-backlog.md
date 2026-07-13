# migration-backlog.md — plan for reaching the target state

## MB-004: Replace the hand-written fetch wrapper with axios
- Priority: P2 · Effort: S · Status: todo
- From decision: [[decisions#ADR-001]] · Findings: stack-001 · Debt: [[LegacyWarning]]
- Where: src/utils/http.ts + the 4 files importing it (see finding evidence)
- What exactly: swap httpGet/httpPost calls for the shared axios instance from client/shared/api; delete src/utils/http.ts last
- Risk: the wrapper silently retried 5xx twice — check no caller depends on that; verify with the api-client integration tests
