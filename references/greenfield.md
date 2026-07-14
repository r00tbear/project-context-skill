# Greenfield mode — empty or nearly empty project

Applies when Preflight finds no meaningful source code: an empty repo, nothing but configs, or a bare scaffold. A scaffold (create-react-app, a fresh FastAPI template...) is not "code to audit" — it is a partially made stack decision; treat it as one.

**Half-empty rule:** if some real source exists, this mode does not replace the audit — run the auditors on what exists (they will honestly return few findings) and use the interview below only to fill the gaps. Skip every question the code or scaffold already answers.

## Instead of Phase 1 (Audit)

No auditors — there is nothing to scan. Run one batched requirements interview (in the config `language`):

1. What are we building? Product, users, the job it does — one paragraph.
2. Type: web app / API service / CLI / library / mobile / something else?
3. Constraints: team size and skills, deployment target, paid services allowed or not, integrations that must exist.
4. Honest scale: users and data volume actually expected in the first year — we do not design for imagined high load.
5. Anything already fixed? (a scaffold in the repo, a corporate standard, a language the team will not leave)

Record the answers in a synthetic findings file `docs/audit/findings/greenfield.yaml` (auditor: greenfield, findings: [], answers in extras) — later runs must be able to see what the plan was based on.

## Phase 2 (Decide) — from requirements instead of code

Runs as usual, with one substitution: the mandatory `target-architecture-proposal` is derived from the interview, not the code. Propose 2–3 stack + architecture options with trade-offs; the option closest to what is already fixed (scaffold, team skills) goes first. At the novice level default to the boring mainstream option — the fewest moving parts, not the trendiest. Losing options go to techstack's Banned section — that is the vaccination against the future zoo.

Everything decided here becomes an ADR in decisions.md, same as always.

## Phase 3 (Generate) — same file set, different emphasis

- `CurrentSprint.md` is the main doc: the first sprint IS the bootstrap plan (repo init, scaffold, CI, a walking skeleton of the first feature). Example: `examples/greenfield-sprint.md`.
- `LegacyWarning.md` and `migration-backlog.md` — empty skeletons with one line: "nothing yet — this file fills up as code diverges from the target". The file set is fixed; do not skip them.
- `db-schema.md` / `ui-kit.md` — only what was actually decided; everything else is an explicit "TBD: decide in sprint N", never invented.
- `techstack.md` — with Banned filled from the losing ADR options.
- agents.md, wikilinks, DoD — unchanged.

## Phase 4 (Verify) — inverted

Until code exists, check only internal consistency and wikilinks. As code appears, verify becomes the enforcement loop: the docs are the plan, and drift-report shows where new code deviates from it. Review mode works unchanged from the very first PR.
