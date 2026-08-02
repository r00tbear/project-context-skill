# Stack auditor

Follow `_common.md`. Determine the actual languages, runtimes, platforms, dependencies, build/deployment tools, and equivalent domain facilities from authoritative declarations and real usage.

Inspect duplicate-purpose dependencies/subsystems, unused or undeclared facilities, incompatible or unsupported release lines, and conflicting tooling ownership. Do not assume semantic versioning, package imports, a package manager, CI, containers, or a conventional application stack.

Typical kinds: `duplicate-purpose-libs`, `unused-dependency`, `undeclared-dependency`, `outdated-release-line`, `conflicting-tooling`.

For duplicates, identify each competing owner and the migration surface in relevant units; the main agent formulates options during Decide. Read manifests, locks, toolchain/platform descriptors, and build definitions directly. Use jCodeMunch importers/references only when they represent the project's real linkage mechanism; confirm every unused/duplicate claim.
