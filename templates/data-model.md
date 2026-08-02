# data-model.md — persisted and shared data model/contracts

<!-- Purpose: the agent never invents persisted structures, shared payload contracts, or storage semantics. Omit this artifact when the project has no data-contract/storage domain. -->

## Scope and source of truth
<!-- What persists or crosses a shared compatibility boundary? Which schema/model/type/protocol/example wins when sources diverge? -->

## Model and contracts
<!-- Use the system's own terms: entities, records, documents, keys/values, nodes/edges, series, events, objects, or files. Record shapes, identifiers, relationships/references, defaults, optionality, and version semantics only when evidenced. -->

## Invariants and storage semantics
<!-- Integrity, ordering, partitioning, consistency, concurrency, retention/lifecycle, and indexes/constraints only where applicable. -->

## Read and write ownership
<!-- Which modules/processes/adapters may read or write each store or contract; forbidden bypasses; must match [[architecture]]. -->

## Evolution, compatibility, and recovery
<!-- Migrations, conversion/backfill, event or format compatibility, rebuild/replay, rollback, backup/restore, and deletion policy only where the project uses them. -->

Related: [[context]] · [[architecture]] · [[decisions]]
