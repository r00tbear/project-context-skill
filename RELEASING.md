# Releasing

The release convention is one squashed, self-contained commit on `main` per release,
tagged `vX.Y.Z`. Patch releases must not change generated-context contracts (the
validator warns instead of invalidating on patch skew — see CHANGELOG.md's header).

Checklist, in order:

1. **Version.** Bump `VERSION` and `skill_version` in
   `templates/project-context.manifest.json` (self-check cross-checks them). Contract
   changes bump the minor version at least.
2. **Changelog.** Add the `CHANGELOG.md` entry with an explicit
   `Regeneration required: yes/no` line. `references/upgrade.md` sends upgrading agents
   to these entries.
3. **Mechanical proof.** `python3 scripts/project_context.py self-check --skill-root .`
   and `python3 -m unittest discover -s tests` must pass locally; CI runs the suite on
   Linux and macOS and exercises the installer across a real version change. Windows CI
   is intentionally not part of the default pipeline (secondary platform): Windows
   correctness is carried by the junction-aware guards, self-skipping symlink tests, and
   `install.ps1` mirroring `install.sh` (pinned by the mirror test, syntax-checked by
   `pwsh` in CI) - run the suite on a Windows machine manually when touching path or
   installer code.
4. **Dogfood run.** Clone the repo to a scratch directory (`git clone . <scratch>`) and
   run the full workflow against the clone: Preflight -> Audit -> Decide -> Generate ->
   Wire -> Verify -> Dashboard. Never commit the resulting `repodocs/` into this
   repository (it would flip its own preflight classification). Attach the run's
   inventory and drift report to the GitHub Release as evidence; refresh `examples/`
   from sanitized dogfood output when it drifted (self-check validates the examples).
   Known misses of this ritual, so it is not oversold: scale behavior, dual-host
   preservation over rich pre-existing host files, occupied ADR/MB series.
5. **Eval subset.** Run the executable eval subset per `evals/README.md` (one fresh
   agent per case, fixtures from `evals/fixtures/build.sh`, binary scoring); keep the
   scorecard outside the repository.
6. **Tag and push.** Squash to one release commit on `main` whose message leads with the
   regeneration consequence, create the annotated tag, push branch and tag together.
7. **Update the global install** by re-running the installer; it resolves the new tag
   itself.
