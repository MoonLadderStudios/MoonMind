# AgentSession deployment safety cutover

Operator playbook for **replay-safe rollout** of durable `AgentSession` workflow changes. The deployment-safety gate expects this file to mention the topics below when sensitive workflow paths change.

## Shared prerequisites

- **replay-safe rollout** gates are in place before production promotion.
- **replay** validation (managed-session replayer tests) passes on representative histories.

## Enabling `SteerTurn`

`SteerTurn` changes require explicit validation gates and staged rollout.

## Enabling Continue-As-New

**Continue-As-New** migrations require explicit validation gates and operator sign-off.

## CancelSession and TerminateSession

Changing cancel/terminate semantics must go through **CancelSession** and **TerminateSession** rollout gates with clear operator messaging.

## Search Attributes

Introducing **Search Attributes** or visibility metadata requires registry updates before relying on new fields in production.
