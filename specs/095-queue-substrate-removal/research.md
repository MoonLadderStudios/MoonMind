# Research: Queue Substrate Removal (Phase 1)

## Research completed during specification audit (2026-03-21)

### R1: Task Routing Target

**Decision**: `routing.py` already routes to Temporal by default.
**Rationale**: `get_routing_target_for_task()` returns `"temporal"` for both manifests (`is_manifest=True`) and runs (`is_run=True`) when `settings.temporal_dashboard.submit_enabled` is `True` (default). The queue fallback only activates when `submit_enabled` is explicitly `False`.
**Alternatives considered**: Gradual rollout with percentage-based routing — rejected because Temporal routing is already 100% live.

### R2: Queue Job Creation Delegation

**Decision**: Queue `create_job` already delegates to Temporal.
**Rationale**: `agent_queue.py` calls `get_routing_target_for_task()` and, when result is `"temporal"`, delegates to `_create_execution_from_task_request` or `_create_execution_from_manifest_request`.
**Alternatives considered**: None needed — delegation already works.

### R3: Worker Lifecycle (Claim/Heartbeat/Complete)

**Decision**: Queue worker lifecycle endpoints are unused by Temporal workers and can be deprecated.
**Rationale**: Temporal workers poll native task queues (`mm.workflow`, `mm.activity.*`). They never call `/api/queue/jobs/claim`.
**Alternatives considered**: Keeping queue endpoints for backward compatibility — rejected because no workers use them.

### R4: Attachments

**Decision**: Temporal artifact system replaces queue attachments.
**Rationale**: Queue attachments use local disk storage via `AgentJobArtifact`. Temporal path uses MinIO via the artifact system with presigned upload/download. The dashboard submit form already sends attachments through the Temporal path when routing goes to Temporal.
**Alternatives considered**: Migrating queue attachments on disk to MinIO — deferred to Phase 3 DB migration.

### R5: Live Sessions

**Decision**: Temporal path already has live session support.
**Rationale**: `/api/task-runs/{id}/live-session` serves Temporal-backed tasks. The queue equivalent at `/api/queue/jobs/{id}/live-session` is redundant.
**Alternatives considered**: None needed — already covered.

### R6: SSE Events

**Decision**: Temporal tasks use dashboard polling, not SSE.
**Rationale**: The Temporal dashboard source polls `/api/executions` at configurable intervals. Queue SSE at `/api/queue/jobs/{id}/events/stream` is not needed.
**Alternatives considered**: Adding SSE for Temporal tasks — deferred; polling works well enough.

### R7: Recurring Tasks

**Decision**: Recurring tasks already use Temporal Schedules.
**Rationale**: Spec 049 (recurring-tasks) and `WorkflowSchedulingGuide.md` document Temporal Schedule creation. The `recurring_tasks.py` router creates Temporal Schedules.
**Alternatives considered**: None needed.

### R8: Step Templates

**Decision**: Step templates are source-agnostic.
**Rationale**: `/api/task-step-templates` returns parameter objects that can be used with any submission path. No queue-specific logic.
**Alternatives considered**: None needed.
