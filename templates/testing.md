# testing.md — verification and testing rules

<!-- Purpose: the agent knows how this project proves behavior and what evidence is required. -->

## How to perform verification
## Where checks and evidence live; how they are named
## Assurance strategy by risk
<!-- map critical behavior to the cheapest level that proves it: pure/module/component, contract/integration, system/journey, property/model, simulation/hardware, static analysis, or manual validation. Include only levels the project actually uses; do not invent target ratios. -->

## Mandatory assurance
<!-- evidenced critical paths/invariants that must not change without suitable automated or explicitly recorded manual verification -->

## Adversarial checks
<!-- for critical paths, checks that try to BREAK the implementation: boundaries, malformed/hostile input, partial or forced failures, concurrency/order, resource pressure, compatibility, and recovery scenarios where applicable—not just the happy path -->

## Fixtures, simulators, environments, and infrastructure
<!-- reusable setup and the policy for clocks/randomness, filesystems, networks, external services, persistent stores, devices, or platform-specific runners only when relevant -->

## Unsupported or banned patterns
<!-- evidence-derived project rules, such as assertion-free/tautological tests or uncontrolled nondeterminism. Do not ban real I/O, snapshots, mocks, or manual tests categorically when they are the correct validation surface. -->

Related: [[context]] · [[edge_cases]]
