# Maple — shared project context

Maple is an in-process library that normalizes text deterministically. It has no user-facing interface, persistence, or externally shared serialized contract.

## Working rules

- Keep changes scoped and record unrelated debt instead of refactoring it in passing.
- Preserve deterministic behavior and the public call boundary.
- Add dependencies or change policy only through an accepted decision.

## Context map

- [[context#stack]]
- [[context#architecture]]
- [[context#security]]
- [[context#testing]]
- [[context#edge-cases]]

<a id="stack"></a>
## Stack

- Runtime and verification commands come from the repository's authoritative tool files.

<a id="architecture"></a>
## Architecture

- The public entry point delegates to normalization stages; stages do not call one another out of order.

<a id="security"></a>
## Security

- Treat caller text as untrusted data and bound work for unusually large inputs.

<a id="testing"></a>
## Testing

- Verify public behavior, Unicode boundaries, malformed input, cancellation, and resource limits.
- For high-impact findings, make a separate bounded attempt to refute or reproduce the claim before accepting it.

<a id="edge-cases"></a>
## Edge cases

- Preserve behavior for empty, malformed, unusually large, and mixed-normalization input.

## Claude/Codex coordination

- Both hosts read this file as shared guidance; chat history is not shared state.
- Use separate worktrees for independent changes and stop before overlapping writes.
- Handoffs include changed paths, checks run, and remaining work.
