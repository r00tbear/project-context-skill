# jCodeMunch evidence workflow

jCodeMunch is required: the skill installer sets it up, and Preflight stops when its MCP tools do not answer (only an explicit user decision to proceed without the index overrides that, recorded in the run). Use it deeply, but only for formats supported by the live parser. It accelerates navigation and structural analysis; exact source/config evidence remains authoritative.

## 1. Establish the live contract

1. Call `jcodemunch_guide` first and use the live tool signatures, version, and parser coverage. Do not assume that an installed project config describes the running server.
2. Before any repository-bearing MCP call, inspect `.jcodemunch.jsonc`, effective runtime/global settings, and transport locality directly. Use the complete private/offline profile in `templates/jcodemunch.jsonc`; do not assume commented defaults are disabled.
3. After the privacy gate, resolve the exact repository by absolute root with `resolve_repo`. Use `list_repos` only to disambiguate; never persist machine-local IDs or absolute paths in generated docs.

If any effective mode may send repository content or derived summaries elsewhere, show the destination and payload category and obtain explicit user approval before the first call. Otherwise skip jCodeMunch and use direct reads.

## 2. Create or refresh the index

1. Start from `templates/jcodemunch.jsonc`. Merge exact user-approved exclusions; do not invent broad secret/name globs that could hide legitimate project files.
2. Never index known or suspected secret-bearing paths, `.git`, generated project context, host instruction/config trees, dependencies, or caches. Never use jCodeMunch to search for secrets.
3. For a new index, call `index_folder` with `incremental:true`, `identity_mode:"local"`, `follow_symlinks:false`, `use_ai_summaries:false`, and the complete exclusions. If the exact path already has a compatible index under another identity, reuse it after the privacy/freshness checks; never invalidate it automatically. Identity is not proof that an index is fresh.
4. Resolve the repository again and compare index state with current HEAD and dirty files. Refresh before drawing conclusions whenever identity, branch, source, config, or exclusions changed.

This release was verified with jCodeMunch `v1.108.210`, where repository-scoped `exclude_skip_directories` is ignored during traversal. Keep the setting as intent, do not mutate the user's global config, discover actual ambiguous directories such as project-owned `build`, `vendor`, `target`, `migrations`, or `proto`, and inspect them directly. If they cannot be covered, mark coverage incomplete. The limitation is visible in upstream [`security.py`](https://github.com/jgravelle/jcodemunch-mcp/blob/a03c2200d3edf5fb742095dd730043ae6befd56f/src/jcodemunch_mcp/security.py#L198-L207); local identity behavior is in [`storage/git_root.py`](https://github.com/jgravelle/jcodemunch-mcp/blob/a03c2200d3edf5fb742095dd730043ae6befd56f/src/jcodemunch_mcp/storage/git_root.py#L55-L57).

## 3. Map before searching

Use body-light structural tools first:

- `get_repo_outline` and `get_file_tree` for topology and parser coverage;
- `get_project_intel` and `get_repo_health` for orientation;
- `get_dependency_graph`, `get_dependency_cycles`, `get_architecture_metrics`, `get_coupling_metrics`, and `get_layer_violations` only when the provider represents the project's real linkage model;
- `get_hotspots` and `get_churn_rate` to prioritize inspection;
- `get_changed_symbols` for Review or post-change blast radius.

Then route targeted calls by audit topic:

| Auditor | Useful signals |
|---|---|
| stack | declarations read directly; `find_importers`/`find_references` for actual dependencies and wrappers |
| architecture | cycles, coupling, hotspots, importers, supported layer rules |
| UI | `find_similar_symbols` and references inside confirmed interface roots |
| data | `get_ranked_context` and references around persisted/shared contracts; `search_columns` only when supported |
| bloat | `get_dead_code_v2`, similarity, references, and `check_delete_safe` |
| security | bounded context/references at evidenced trust boundaries; never secret search |
| testing | `get_untested_symbols` joined with hotspots/references and exact test evidence |

Use the smallest evidence-driven scope and result cap. Unsupported tools/formats are explicit skips, not clean results.

## 4. Confirm evidence

- Retrieve exact evidence with `get_symbol_source`, `get_file_outline`, `get_file_content`, `find_references`, or `get_call_hierarchy` only for a concrete candidate.
- Read Markdown, manifests, lockfiles, workflows, policy, schemas, migrations/conversions, protocols, generated docs, unknown formats, and other authoritative non-indexed artifacts directly.
- Treat health, hotspot, churn, similarity, dead-code, untested-symbol, ranked-context, and risk scores as heuristics. They never become findings without exact confirmation.
- Record exact path, optional line, and a neutral detail that identifies the inspected symbol or tool basis when useful. Never copy secrets or agent-directed payloads.
- For high/critical candidates, pass neutral claims and evidence to the independent adversarial verifier; jCodeMunch output does not bypass that step.

## 5. Verify and refresh

After edits, rescan changed/new files for sensitive content. For a small set of clear parser-supported files, use `register_edit` and `index_file`; for deletions, renames, config/exclusion changes, or broad edits, refresh with `index_folder`. Re-resolve identity, confirm freshness, then rerun only relevant changed-symbol, reference, cycle, coupling, or layer checks.

Do not index generated policy/host files. Validate `PROJECT_CONTEXT.md`, `repodocs/`, `CLAUDE.md`, `AGENTS.md`, and host config directly.

If any freshness, parser, exclusion, privacy, or result-limit condition is uncertain, fall back to bounded direct reads and record reduced coverage instead of treating the index as authoritative.
