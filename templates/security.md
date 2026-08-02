# security.md — security rules

<!-- Purpose: the agent never trades security for "just make it work". -->

## Secrets
<!-- approved sources and injection mechanisms for this project; handling, redaction, rotation/escalation policy when a secret is found. Do not assume environment variables or a remote secret manager are available. -->

## Trust boundaries and validation
<!-- identify actual boundaries (library/API/message/file/CLI/config/plugin/device/user/persistence, etc.); validate before lower-trust data crosses into trusted behavior, using the owner appropriate to this architecture -->

## Identity, access, and privileges
<!-- when applicable: authentication, authorization/ownership/capabilities, privilege separation, credential/session lifetime, and mandatory checks for new operations. Say explicitly when the project has no such capability. -->

## Sensitive data, observability, and transport
<!-- classification/retention, what must not be logged or emitted, redaction, encryption/integrity, file/system permissions, and transport/browser-origin controls only where applicable -->

## Dependencies and supply chain
<!-- provenance/pinning/update and vulnerability policy for the package/build/distribution mechanisms the project actually uses; severities that block; trusted scanners and verification -->

## Failure and response
<!-- safe defaults, containment, disclosure/escalation, and recovery behavior appropriate to this project's execution environment -->

Related: [[context]] · [[edge_cases]] · [[testing]]
