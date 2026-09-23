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
   and `python3 -m unittest discover -s tests` must pass locally, along with
   `bash -n install.sh` and Ruff. CI runs the suite on Linux and macOS,
   installer checks across a real version change, and the PowerShell installer
   on Windows with both Windows PowerShell 5.1 and PowerShell 7. A path or
   installer change needs the Windows job green before release.
4. **Dogfood run.** Copy the candidate tree to a scratch Git repository and run
   the full workflow against the copy: Preflight -> Audit -> report/dashboard;
   then an explicit context request -> Decide -> Generate -> Wire -> Verify ->
   Dashboard. Never commit the resulting `repodocs/` into this
   repository (it would flip its own preflight classification). Attach the run's
   inventory and drift report to the GitHub Release as evidence; refresh `examples/`
   from sanitized dogfood output when it drifted (self-check validates the examples).
   Known misses of this ritual, so it is not oversold: scale behavior, dual-host
   preservation over rich pre-existing host files, occupied ADR/MB series.
5. **Product evaluation.** Run the preregistered 24-session paired study in
   `evals/paired-study.md` with a blind human scorer; record human corrections,
   task success, and rule violations. Keep individual session artifacts and the
   scorecard outside the repository. If the paired study is unavailable, release
   notes must say the product benefit remains unverified.
6. **Tag and push.** Squash to one release commit on `main` whose message leads with the
   regeneration consequence, create the annotated tag, push branch and tag together.
7. **Update the global install** by re-running the installer; it resolves the new tag
   itself.
