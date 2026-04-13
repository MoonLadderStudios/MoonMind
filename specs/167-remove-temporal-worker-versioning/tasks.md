# Tasks: Remove Temporal Worker Deployment Routing

## Implementation

- [X] Remove Worker Deployment configuration from `moonmind/workflows/temporal/worker_runtime.py`.
- [X] Remove `TEMPORAL_WORKER_VERSIONING_DEFAULT_BEHAVIOR` from settings and `.env-template`.
- [X] Remove worker-versioning behavior from managed-session deployment-safety validation.
- [X] Update worker runtime tests to assert direct task-queue polling.
- [X] Update deployment-safety tests to require replay/cutover gates only.
- [X] Delete the Worker Deployment runbook and align canonical docs/specs.

## Validation

- [X] Run focused worker-runtime and deployment-safety unit tests.
- [X] Search for stale worker-versioning code/config references.
- [X] Run the full unit suite if feasible.
