# AgentSession deployment safety cutover

Operator playbook for replay-safe rollout of durable `AgentSession` workflow changes. Compatibility evidence comes from executing worker and replayer tests against representative histories.

## Shared prerequisites

- Managed-session worker and replayer tests pass on representative histories before rollout.
- Verify affected callers and workers support changed workflow commands.

## Enabling `SteerTurn`

Exercise `SteerTurn` updates against running sessions before rollout.

## Enabling Continue-As-New

Validate Continue-As-New state transfer and replay across the rollover boundary.

## CancelSession and TerminateSession

Exercise `CancelSession` and `TerminateSession` completion, cleanup and operator-visible outcomes when their semantics change.

## Search Attributes

Register new Search Attributes before relying on those visibility fields in production.
