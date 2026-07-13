# Shelfie — context for AI agents

Shelfie is a small SaaS for tracking home book collections: React SPA + Express API + PostgreSQL.
Main rule: the docs below describe the TARGET state. Where the code you touch contradicts them — follow the docs for new code and record the mismatch in [[LegacyWarning]]; never mass-refactor in passing.

## Rules
- Do the task, don't refactor around it. Improvement ideas go to [[LegacyWarning]].
- You don't pick technologies yourself — see [[techstack]].
- You don't invent the DB structure — see [[db-schema]].
- You don't write UI components from scratch — see [[ui-kit]].
- Changed dependencies / DB schema / UI components — update the corresponding doc in the same change, or record the mismatch in [[LegacyWarning]].
- Navigate the code with jcodemunch (search_symbols / get_symbol_source / find_references) instead of reading whole files.

## Definition of Done
A task is not done until:
- [ ] tests written per [[testing]]: unit for lib code, integration for every API route touched
- [ ] edge cases from [[edge-cases]] handled (at minimum: empty shelf, offline, expired session)
- [ ] layer boundaries intact ([[architecture]]): ui → api-client → routes → services → db
- [ ] no new dependencies without an ADR in [[decisions]]
- [ ] [[security]] rules followed (validation in routes, no PII in logs)
- [ ] affected docs updated (or the mismatch recorded in [[LegacyWarning]])

## Which file to read for which task
| Task | Read |
|---|---|
| Any current-sprint task | [[CurrentSprint]] |
| New code / picking a library | [[techstack]], [[links]] |
| Where a file goes, how the layers work | [[architecture]] |
| Working with data | [[db-schema]] |
| UI | [[ui-kit]] |
| Error handling, unusual scenarios | [[edge-cases]] |
| Security: secrets, validation, permissions | [[security]] |
| Writing tests | [[testing]] |
| Before touching legacy zones | [[LegacyWarning]] |
