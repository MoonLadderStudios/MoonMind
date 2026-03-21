# Implementation Plan: Queue Substrate Removal (Phase 1)

**Branch**: `095-queue-substrate-removal` | **Date**: 2026-03-21 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/095-queue-substrate-removal/spec.md`

## Summary

Ensure every action that currently routes through the legacy queue execution path (`/api/queue/jobs`) has a working Temporal equivalent, so the queue substrate can be deprecated. The audit shows that task creation already routes to Temporal — the remaining work is: (1) make `routing.py` always return `"temporal"` and remove the queue fallback, (2) verify attachment uploads work through the Temporal artifact system, (3) produce a feature audit documenting every queue endpoint's Temporal equivalent or deferral status, (4) verify recurring tasks use Temporal Schedules, and (5) verify step templates work with Temporal. This is primarily an audit + hardening + cleanup effort.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, Temporal Python SDK, SQLAlchemy, Pydantic
**Storage**: PostgreSQL (app + Temporal + visibility), MinIO (artifacts), Qdrant (vectors)
**Testing**: pytest via `./tools/test_unit.sh`
**Target Platform**: Docker Compose self-hosted deployment
**Project Type**: Web application (API + dashboard)
**Performance Goals**: No performance regressions — task submission latency must stay under current levels
**Constraints**: Zero feature loss for operators — every queue feature must have a Temporal equivalent or explicit deferral
**Scale/Scope**: ~15 files modified, ~3 files created (audit report, tests)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | ✅ PASS | Temporal is the standard orchestration engine — this removes a non-standard alternative |
| II. One-Click Deployment | ✅ PASS | Removing queue simplifies the deployment; no new services needed |
| III. Avoid Vendor Lock-In | ✅ PASS | Temporal replaces a bespoke queue; it has clear adapter patterns |
| IV. Own Your Data | ✅ PASS | No data ownership changes |
| V. Skills First-Class | ✅ PASS | Skills already execute through Temporal activities |
| VI. Bittersweet Lesson | ✅ PASS | Removing legacy scaffold is exactly this principle |
| VII. Runtime Configurability | ✅ PASS | Removing `submit_enabled` toggle is a simplification; fail-fast on any unsupported config |
| VIII. Modular Architecture | ✅ PASS | Removing the queue module reduces entanglement |
| IX. Resilient by Default | ✅ PASS | Temporal provides stronger resiliency than the custom queue |
| X. Continuous Improvement | ✅ PASS | Simplifying the execution model improves observability |
| XI. Spec-Driven Development | ✅ PASS | This work has a spec and plan |

No violations. No complexity tracking needed.

## Project Structure

### Documentation (this feature)

```text
specs/095-queue-substrate-removal/
├── spec.md
├── plan.md               # This file
├── research.md            # Phase 0 output
├── contracts/
│   └── requirements-traceability.md
├── checklists/
│   └── requirements.md
└── tasks.md               # Phase 2 output
```

### Source Code (repository root)

```text
# Files to MODIFY
moonmind/workflows/tasks/routing.py            # Remove queue fallback, always return temporal
api_service/api/routers/agent_queue.py          # Add deprecation warnings to queue-only endpoints
api_service/api/routers/task_dashboard_view_model.py  # Document queue endpoint deprecation status

# Files to CREATE
specs/095-queue-substrate-removal/contracts/queue-feature-audit.md  # Complete audit report

# Files to VERIFY (no changes expected)
api_service/api/routers/executions.py           # Already handles task+manifest creation
api_service/api/routers/recurring_tasks.py      # Verify uses Temporal Schedules
api_service/api/routers/task_step_templates.py  # Verify works with Temporal path
```

**Structure Decision**: This feature modifies existing backend files in the API service and workflows module. No new modules are created — the primary deliverable is the audit report and the routing hardening.

## Phase 0: Research Findings

All research was completed during the specification audit. Key findings:

1. **Task routing already defaults to Temporal** — `routing.py:get_routing_target_for_task()` returns `"temporal"` for both manifests and runs when `submit_enabled=True` (which is the default).
2. **Queue `create_job` already delegates to Temporal** — `agent_queue.py` lines 773–795 call `_create_execution_from_task_request` and `_create_execution_from_manifest_request` when target is `"temporal"`.
3. **Queue worker lifecycle is unused** — Temporal workers poll native task queues (`mm.workflow`, `mm.activity.*`), not `/api/queue/jobs/claim`.
4. **Attachments** — The Temporal path already supports artifact upload via presigned URLs. The queue attachment upload path (`/api/queue/jobs/with-attachments`) stores files locally on disk. The Temporal path uses MinIO.
5. **Live sessions** — Already available at `/api/task-runs/{id}/live-session` for Temporal-backed tasks. Queue path at `/api/queue/jobs/{id}/live-session` is redundant.
6. **SSE events** — Queue has `/api/queue/jobs/{id}/events/stream`. Temporal tasks use polling via the `temporal` dashboard source. No SSE gap for Temporal.
7. **Recurring tasks** — Created via `/api/recurring-tasks` which creates Temporal Schedules (validated by Spec 049, `WorkflowSchedulingGuide.md`).
8. **Step templates** — The template expansion API at `/api/task-step-templates` is source-agnostic — it returns parameters that can be used with either queue or Temporal submission.

## Phase 1: Design

### Data Model

No new data models. The change removes reliance on the `AgentJob`, `AgentJobEvent`, `AgentJobArtifact`, `AgentWorkerToken` models in favor of existing Temporal execution models.

### Contracts

No new API contracts. The change deprecates queue endpoints in favor of existing Temporal execution endpoints.

### Key Implementation Decisions

1. **Remove queue fallback in routing.py** — Change `get_routing_target_for_task()` to always return `"temporal"`. Remove the `submit_enabled` toggle fallback. If someone configures `submit_enabled=False`, fail fast instead of silently routing to queue.

2. **Produce audit report** — Create `contracts/queue-feature-audit.md` documenting every `/api/queue/*` endpoint with its status: `Temporal equivalent exists`, `Deprecated (worker internal)`, or `Deferred`.

3. **No code deletion in Phase 1** — Phase 1 is about proving parity, not removing code. Code removal happens in Phase 2+ of the migration plan.

4. **Add deprecation logging** — Add `logger.warning("Queue endpoint deprecated: ...")` to worker-facing queue endpoints (claim, heartbeat, complete, fail, recover).
