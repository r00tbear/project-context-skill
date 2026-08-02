# Decision matrix

Adapt decisions to `user_level` without hiding evidence or making irreversible choices for the user.

| Situation | novice | specialist | expert |
|---|---|---|---|
| Low/medium severity, high confidence | decide and report | decide and report | recommend, confirm if debatable |
| High severity, high confidence | recommend, confirm | recommend, confirm | show full trade-offs |
| Critical severity, any confidence | recommend, confirm | recommend, confirm | show full trade-offs |
| Non-critical severity, low/medium confidence | recommend, confirm | show full trade-offs | show full trade-offs |
| Options are genuinely balanced | ask briefly | ask | ask |

Always ask before:

- deleting code or files;
- irreversible work;
- major dependency/runtime/platform compatibility changes;
- paid services or infrastructure;
- compatibility-affecting persisted/shared data or protocol changes.

Ask by topic in this order when enabled: stack, architecture, UI, data, bloat, security, testing. State evidence, options, effort, trade-offs, and a recommendation. Record accepted policy in the in-memory ADR candidate immediately; persist it only with regenerated dependent docs. Policy changed by a reviewed diff cannot approve implementation in that same diff.
