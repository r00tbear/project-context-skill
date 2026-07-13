# Decision matrix (Decide phase)

The action for each conflict group is determined by three axes: user level × severity × confidence.

## Levels
- **novice** — the user trusts the agent. The agent makes the best decisions itself; questions are the exception.
- **specialist** — the agent decides the routine; big forks go to the user with a ready recommendation ("Enter = accept").
- **expert** — the agent decides only the obvious; everything debatable goes to the user with full trade-offs and no nudging.

## Matrix

| Situation | novice | specialist | expert |
|---|---|---|---|
| severity low/medium, confidence high | decide auto | decide auto | recommendation → confirm |
| severity high, confidence high | decide auto, report | recommendation → confirm | full trade-offs |
| any severity, confidence low/medium | recommendation → confirm | full trade-offs | full trade-offs |
| options are a toss-up (~50/50) | ask (briefly) | ask | ask |

## Exceptions — always ask, at every level
- deleting code or files;
- major version changes of a dependency/language/framework;
- anything touching paid services or infrastructure;
- DB schema changes;
- any irreversible action.

At the novice level, exception questions are worded as briefly as possible: one line of essence + a recommendation.

## Question format
- group by topic (stack → architecture → UI → data → bloat), one topic — one message;
- each question: the fact with evidence → options with effort/trade-offs → a recommendation;
- record answers in docs/decisions.md immediately, not at the end of the phase.
