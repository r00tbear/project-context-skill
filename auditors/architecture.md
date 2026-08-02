# Architecture auditor

Follow `_common.md`. Inspect the repository's actual responsibility and execution shape: library, script, CLI/TUI, plugin, service, app, pipeline, firmware, infrastructure, monorepo, or another domain-native form.

Look for evidenced boundary violations, cycles, inconsistent ownership/conventions, smeared core policy, and units with abnormally broad responsibility. Do not assume UI, storage, server processes, layers, or import graphs exist. Use coupling, cycle, hotspot, and churn signals only to choose exact code/config to inspect.

Typical kinds: `mixed-architecture`, `boundary-violation`, `circular-dependency`, `inconsistent-conventions`, `god-module`.

Describe enough exact responsibility and boundary evidence for the main agent to formulate a target during Decide. Preserving a coherent current shape is valid; never prescribe a folder rewrite or parade of named patterns merely to look architectural.
