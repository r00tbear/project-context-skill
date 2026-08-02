# edge-cases.md — unusual scenarios

<!-- Purpose: the agent knows more than the happy path. -->

## Invalid, empty, missing, and boundary values
## Errors, partial failure, and recovery
## Cancellation, retry, duplication, ordering, and concurrency
<!-- include only semantics the system can encounter; document idempotency and rollback/compensation where applicable -->

## Resource and environment limits
<!-- time, memory, disk, handles, rate/capacity, offline/network, clock/randomness, device/platform constraints, or large inputs where applicable -->

## Compatibility and evolution
<!-- versions, formats, protocols, persisted data, downgrade/upgrade, feature negotiation, and mixed-version operation only for supported capabilities -->

## Permissions and trust-boundary failures
<!-- authorization/capability/ownership, filesystem permissions, hostile input, unavailable dependencies, or unsupported operations where applicable -->

## Interface and unusual user interaction
<!-- empty/loading/error/focus/navigation, rapid/repeated/canceled actions, accessibility and localization only when a user interface exists -->

## Persistence and external I/O
<!-- data-store, file, message, device, process, or service failures only for boundaries the project actually has -->

## Security constraints
<!-- details in [[security]] -->

Related: [[context]] · [[testing]] · [[security]]
