# Data auditor

Follow `_common.md`. Run only when persisted state or an externally shared serialized/file/message/protocol contract exists. Data may be relational, document, key-value, graph, time-series, object, event/log, embedded, file-backed, or another form.

Inspect sources of truth, read/write ownership, model/contract shapes, identifiers and relationships, integrity/concurrency/idempotency risks, retention, and the project's real evolution/compatibility/rollback mechanism. Do not invent tables, migrations, indexes, or relational vocabulary for systems that do not use them.

Typical kinds: `data-contract-conflict`, `mixed-data-access`, `data-access-boundary`, `integrity-risk`, `evolution-gap`.

Make the evidence detailed enough for the main agent to reconstruct `data-model.md`. Read schemas, migrations/conversions, protocol definitions, serializers, and representative contracts directly; use jCodeMunch references/context only where its parser supports the format.
