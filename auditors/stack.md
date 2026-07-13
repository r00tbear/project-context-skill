# Stack Auditor

The rules of `_common.md` (passed to you together with this prompt) are mandatory.

Topic: the tech stack and dependencies.

What to investigate:
1. Manifests: package.json (+lock), requirements/pyproject, go.mod, etc. — languages, frameworks, versions.
2. Actual usage: imports in code vs declared dependencies (dead dependencies, usage without declaration).
3. Duplicate-purpose libraries: two+ libraries for the same job (axios + fetch wrappers, moment + dayjs, lodash + ramda, two HTTP clients, two validators, two state managers).
4. Version spread: outdated majors, versions with known EOL, incompatible pairs.
5. Tooling: linters/formatters/CI — any conflicting configs.

Typical findings: `duplicate-purpose-libs`, `unused-dependency`, `undeclared-dependency`, `outdated-major`, `conflicting-tooling`.

For every duplicate — options: which library to keep, with a migration cost estimate (how many files are affected).

With jcodemunch: find_importers on a package — actual dependency usage (for unused/duplicate-purpose); symbol search for wrappers around libraries.
