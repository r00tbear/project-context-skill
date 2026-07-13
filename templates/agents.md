# <Project name> — context for AI agents

<!-- Core: 1-2 paragraphs — what the project is, the main rule for an agent working in it. Do not duplicate the contents of the files below. -->
<!-- All doc references are wikilinks — the docs form a connected graph. -->

## Rules
- Do the task, don't refactor around it. Anything that could be improved later goes to [[LegacyWarning]].
- You don't pick technologies yourself — see [[techstack]].
- You don't invent the DB structure — see [[db-schema]].
- You don't write UI components from scratch — see [[ui-kit]].
- Changed dependencies / DB schema / UI components — update the corresponding doc in the same change, or record the mismatch in [[LegacyWarning]].
<!-- include the line below only if the jcodemunch MCP is wired into the project -->
- Navigate the code with jcodemunch (search_symbols / get_symbol_source / find_references) instead of reading whole files.
<!-- if ponytail is installed — do not add anti-over-engineering rules here: that's its territory (always-on mode + /ponytail-review) -->

## Definition of Done
A task is not done until:
- [ ] tests are written per [[testing]], critical paths covered
- [ ] edge cases from [[edge-cases]] are handled
- [ ] layer boundaries are intact, new files follow the target structure ([[architecture]])
- [ ] no new dependencies without an ADR in [[decisions]]
- [ ] the rules of [[security]] are followed
- [ ] affected docs are updated (or the mismatch is recorded in [[LegacyWarning]])

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
