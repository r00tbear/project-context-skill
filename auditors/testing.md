# Testing auditor

Follow `_common.md`. Inventory the project's actual automated, static, formal, simulated, hardware, and manual assurance mechanisms. Judge them against risk and architecture; do not demand a test pyramid, coverage percentage, CI, or every test level.

Inspect unverified critical behavior, assurance-level mismatch, tautological or implementation-copy checks, uncontrolled time/randomness/I/O/concurrency, indiscriminate mocking, and manual/simulation procedures without reproducible inputs or expected results.

Adversarial verification is mandatory where relevant: look for boundary values, invalid inputs, partial/forced failures, destructive-operation safeguards, race/order variation, compatibility/recovery cases, and attempts to falsify core invariants rather than happy-path confirmation only.

Typical kinds: `unverified-critical-path`, `assurance-level-mismatch`, `invalid-verification`, `flaky-pattern`, `no-verification-infra`.

Prioritize the smallest assurance that catches the real failure. Join jCodeMunch untested/hotspot signals with exact production and verification evidence; reachability heuristics are not coverage.
