# Contract: Remediation Runtime

## Canonical Action Registry

The remediation action registry exposes only canonical dotted action kinds:

- `execution.pause`
- `execution.resume`
- `execution.request_rerun_same_workflow`
- `execution.start_fresh_rerun`
- `execution.cancel`
- `execution.force_terminate`
- `session.interrupt_turn`
- `session.clear`
- `session.cancel`
- `session.terminate`
- `session.restart_container`
- `provider_profile.evict_stale_lease`
- `workload.restart_helper_container`
- `workload.reap_orphan_container`

Each listed action returns metadata with:

- `actionKind`
- `riskTier`
- `targetType`
- `inputMetadata`
- `preconditions`
- `idempotency`
- `verificationRequired`
- `verificationHint`
- `auditPayloadShape`

Unsupported raw capabilities remain denied:

- host shell
- raw Docker daemon
- arbitrary SQL
- arbitrary storage-key reads
- decrypted secret reads

## Action Authority Boundary

Authority evaluation is side-effect-free. It can allow, require approval, dry-run, or deny a request. Side effects must be executed by an owning MoonMind control-plane service or subsystem adapter after authority, mutation guard, idempotency, and verification preconditions pass.

## Runtime Evidence

Accepted actions produce artifact-backed evidence:

- `remediation.action_request`
- `remediation.action_result`
- `remediation.verification`
- `remediation.summary`

Artifacts contain refs and bounded metadata, never presigned URLs, raw storage keys, local host paths, secret values, or unbounded log bodies.

## Target-Side Summary

Execution and task-run read models expose compact remediation summary fields:

- active remediation count
- latest remediation title/status
- latest action kind
- active lock holder/scope
- outcome
- updated time

Consumers must not parse deep artifact bodies to render this summary.
