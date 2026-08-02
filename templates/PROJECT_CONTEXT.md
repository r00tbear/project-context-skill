# <Project name> — shared project context

## Project and governing rule
<!-- What the project is. New work follows accepted target state; existing mismatches become explicit debt. -->

## Working rules

- Keep changes scoped; record unrelated debt in [[legacy_warning]].
- Do not introduce a technology or architecture choice without [[decisions]].
- Update affected context when a dependency, contract, interface, security boundary, or verification policy changes.

## Claude/Codex coordination

- Treat the repository and Git state as shared truth; chat history is host-local.
- Use separate worktrees for independent changes and claim paths before editing.
- Re-read a file before writing; stop on overlap. Handoffs list changed paths, checks, and remaining work.

## Definition of Done

- [ ] verification follows [[testing]], including relevant adversarial checks
- [ ] edge cases and security boundaries follow [[edge_cases]] and [[security]]
- [ ] placement follows [[architecture]]
- [ ] new policy or dependencies have an ADR in [[decisions]]
- [ ] affected context is updated or debt is recorded

## Context map

| Need | Read |
|---|---|
| Stack and dependencies | [[techstack]] |
| Structure and ownership | [[architecture]] |
| Persisted/shared data contracts | [[data_model]] |
| User-facing interfaces | [[ui_kit]] |
| Edge cases and security | [[edge_cases]], [[security]] |
| Verification | [[testing]] |
| Decisions and debt | [[decisions]], [[legacy_warning]], [[migration_backlog]] |
| Context drift | [[drift_report]] |

Wikilinks resolve through `repodocs/project-context.manifest.json`. Add a current-sprint row/link only when that optional ledger exists; remove UI/data rows when those domains are absent. In compact layout, embed topic sections here, place a stable HTML anchor such as `<a id="stack"></a>` before each localized heading, and replace topic links with `[[context#stack]]`, `[[context#architecture]]`, `[[context#security]]`, `[[context#testing]]`, `[[context#edge-cases]]`, `[[context#ui]]`, and `[[context#data]]` as applicable.
