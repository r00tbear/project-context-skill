# architecture.md — target architecture

## Architectural approach
Layered monolith with a feature-first frontend ([[decisions#ADR-002]]). The server is Express with a thin route layer over services; the SPA is React organized by feature folders. Chosen as the closest fit to the existing code — ~70% of files already match, migration cost S.

## Layers and responsibility boundaries
| Layer | Owns | Must not |
|---|---|---|
| client/features/* | UI and local state of one feature | call the server directly, hold business rules |
| client/shared/api | all HTTP calls to the server | render anything |
| server/routes | HTTP parsing, validation, authz | contain business logic |
| server/services | business rules, transactions | touch req/res |
| server/db | queries, migrations | be imported by anything except services |

## Folder structure
```
client/
  features/<feature>/   # components + hooks of one feature
  shared/ui/            # canonical components ([[ui-kit]])
  shared/api/           # the only place that talks to the server
server/
  routes/               # one file per resource, thin
  services/             # business logic
  db/                   # queries + migrations/
```

## File creation rules
- Components: `PascalCase.tsx` inside their feature; tests as `<name>.test.ts` alongside.
- A new API resource = a route file + a service file + a migration if the schema changes.

## Rules for new code
New code follows this structure from day one, no exceptions. Old-code mismatches are not fixed in passing — record them in [[LegacyWarning]] / [[migration-backlog]].

## Module interaction and constraints
The client talks to the server only through `client/shared/api`. Services never import routes. `server/db` is imported by services only.

Related: [[agents]] · [[techstack]] · [[decisions]]
