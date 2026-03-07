# Research: Task Recurring Schedules System

## Decision 1: Retain the two-stage scheduler transaction model

- **Decision**: Keep scheduler execution split into Loop A (`recurring_task_definitions` -> `recurring_task_runs`) and Loop B (`recurring_task_runs` -> queue/manifest dispatch side effects).
- **Rationale**: This mirrors the reliability model in `docs/TaskRecurringSchedulesSystem.md` and avoids long-lived transactions that combine due-scan mutations with external enqueue side effects.
- **Alternatives considered**:
  - Single-loop schedule-and-dispatch transaction: rejected because queue/manifest submission paths commit independently and increase uncertainty windows.
  - Broker-beat orchestration: rejected by DOC-REQ-004 and FR-003 (DB-backed scheduler required).

## Decision 2: Use run identity as idempotency anchor and reconciliation key

- **Decision**: Treat `(definition_id, scheduled_for)` plus `run_id` recurrence metadata as canonical identity for effectively-once enqueue semantics.
- **Rationale**: Unique run rows prevent duplicate logical occurrences; recurrence metadata allows post-failure reconciliation before creating new queue artifacts.
- **Alternatives considered**:
  - Idempotency only at queue layer: rejected because recurring run history could drift from dispatch outcomes.
  - Exactly-once worker execution guarantees: rejected as out-of-scope and incompatible with at-least-once worker retry semantics.

## Decision 3: Keep minute-level cron parser scope with timezone/DST correctness

- **Decision**: Continue with a strict five-field cron parser and `zoneinfo` timezone handling, including DST transition coverage.
- **Rationale**: Meets FR-016/FR-023 and avoids introducing broader cron semantics than v1 requires.
- **Alternatives considered**:
  - Add seconds-field or sub-minute support: rejected by DOC-REQ-007 and FR-023.
  - Replace with broader cron dependency now: rejected because current parser already satisfies required semantics and minimizes migration risk.

## Decision 4: Normalize policy behavior with explicit global bounds

- **Decision**: Policy payloads support overlap/catchup/misfire/jitter, while global backfill and batch ceilings remain environment-configured (`MOONMIND_SCHEDULER_*`).
- **Rationale**: Per-schedule flexibility is required, but global bounds protect scheduler stability and blast radius.
- **Alternatives considered**:
  - Unlimited catchup per schedule: rejected due to thundering-herd and backlog risk.
  - No overlap policies: rejected because FR-014 requires explicit skip/allow semantics.

## Decision 5: Dispatch all target kinds through existing queue/manifest pathways

- **Decision**: Support `queue_task`, `queue_task_template`, `manifest_run`, and queue-backed `housekeeping` target kinds through existing service abstractions.
- **Rationale**: Reuses queue lifecycle, provenance tooling, and dashboard linking while keeping target handling centralized in recurring-task service logic.
- **Alternatives considered**:
  - Inline housekeeping execution in scheduler: rejected for weaker observability and less consistent operations.
  - Separate scheduler-specific manifest submission path: rejected because `ManifestsService.submit_manifest_run` already captures required behavior.

## Decision 6: Enforce security and scope authorization at schedule boundaries

- **Decision**: Keep personal/global authorization checks in API/service layers and reject raw secret material in recurring target payloads.
- **Rationale**: FR-002 and FR-020 require ownership boundaries plus secret hygiene for persisted definitions.
- **Alternatives considered**:
  - UI-only authorization and secret checks: rejected because API-level enforcement is required for safety.
  - Accept raw secrets with warning logs: rejected by security constraints.

## Decision 7: Runtime mode alignment is mandatory for this feature

- **Decision**: Plan and execution assume **runtime orchestration mode** (production code + tests), not docs-only mode.
- **Rationale**: Feature spec FR-021/FR-022 and task objective require runnable behavior changes and automated validation coverage.
- **Alternatives considered**:
  - Deliver docs/spec artifacts only: rejected because it would fail acceptance scope.
  - Defer tests to a follow-up slice: rejected because validation is a required deliverable.

## Decision 8: Verification uses repository-standard test entrypoints

- **Decision**: Use `./tools/test_unit.sh` as the primary validation entrypoint and keep orchestrator integration verification via `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests`.
- **Rationale**: Aligns with repository CI expectations and avoids local command drift.
- **Alternatives considered**:
  - Calling `pytest` directly: rejected by repository testing instructions.
  - Manual-only dashboard verification: rejected because FR-022 requires automated coverage.
