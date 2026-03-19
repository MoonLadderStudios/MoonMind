# Implementation Plan: Worker Pause System (Temporal Era)

**Branch**: `038-worker-pause` | **Date**: 2026-03-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/038-worker-pause/spec.md`

## Summary

Upgrade the existing Worker Pause System from legacy queue-table metrics to Temporal-native primitives. The existing DB singleton and API surface remain, but drain metrics are sourced from Temporal Visibility, Quiesce mode leverages the existing workflow `pause`/`resume` signals via Temporal Batch Operations, and a new API guard prevents workflow submission while paused.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, SQLAlchemy (async), Temporal Python SDK (`temporalio`)
**Storage**: PostgreSQL (via SQLAlchemy), Temporal Server (Visibility API)
**Testing**: pytest (unit + integration), `./tools/test_unit.sh` runner
**Target Platform**: Docker Compose (local-first)
**Project Type**: Web backend service (FastAPI)

## Constitution Check

| Principle | Status | Evidence |
|-----------|--------|----------|
| I. Orchestrate, Don't Recreate | PASS | Uses Temporal's native signals and graceful shutdown, not custom reimplementations. |
| II. One-Click Deployment | PASS | Drain = `docker compose stop`; no new infra dependencies. |
| III. Avoid Vendor Lock-In | PASS | Temporal is the chosen orchestrator; pause state is in portable DB. Signal delivery uses standard Temporal SDK APIs. |
| IV. Own Your Data | PASS | All pause state and audit data stored in operator-controlled PostgreSQL. |
| V. Skills First-Class | N/A | No skill changes required. |
| VI. Bittersweet Lesson | PASS | Thin scaffolding over Temporal SDK; workflow signal handlers already exist and are easy to evolve. |
| VII. Runtime Configurability | PASS | Pause state controlled via API at runtime. No image rebuilds needed. |
| VIII. Modular Architecture | PASS | Changes isolated to pause service layer, API router, and a Temporal client helper. |
| IX. Resilient by Default | PASS | Temporal durably persists paused workflow state in history. No data loss on worker restart. |
| X. Continuous Improvement | PASS | Audit trail captures every pause/resume with reason. |
| XI. Spec-Driven Development | PASS | This plan + spec + tasks triad is the implementation contract. |

## Project Structure

### Documentation (this feature)

```text
specs/038-worker-pause/
├── spec.md
├── plan.md                    # This file
├── research.md                # Phase 0 output
├── data-model.md              # Phase 1 output (existing, updated)
├── quickstart.md              # Phase 1 output (existing, updated)
├── speckit_analyze_report.md  # Analysis report
├── checklists/
│   └── requirements.md        # Spec quality checklist
├── contracts/
│   └── requirements-traceability.md
└── tasks.md                   # Phase 2 output
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
│   └── system_worker_pause.py      # [MODIFY] Add Temporal Visibility metrics, Batch Signal dispatch
├── api/
│   └── schemas.py                  # [MODIFY] Update metrics model (Temporal-sourced)
└── main.py                         # [MODIFY] Add pause guard on workflow start endpoint

moonmind/
├── workflows/agent_queue/
│   ├── service.py                  # [MODIFY] Replace queue-table metrics with Temporal Visibility
│   └── repositories.py             # [MODIFY] Remove legacy metric queries (or keep as fallback)
└── workflows/temporal/
    ├── temporal_client.py           # [NEW] Temporal client helper for Visibility + Batch Signals
    └── workflows/
        └── run.py                  # Already has pause/resume signals (no change needed)

tests/
├── unit/api/routers/
│   └── test_system_worker_pause.py # [MODIFY] Update tests for Temporal Visibility + Batch Signals
└── unit/workflows/
    └── test_temporal_client.py     # [NEW] Unit tests for Temporal client helper
```

## Implementation Phases

### Phase 1: Temporal Client Helper

Create `moonmind/workflows/temporal/temporal_client.py` with:
- `get_drain_metrics(task_queues)`: Query Temporal Visibility (`ListWorkflowExecutions` with `ExecutionStatus="Running"`) and return `queuedCount`, `runningCount`, `isDrained`.
- `send_batch_signal(signal_name, signal_payload, query)`: Use Temporal Batch Operations API to send a signal to all workflows matching a Visibility query.

### Phase 2: Service Layer Update

Modify `moonmind/workflows/agent_queue/service.py`:
- Replace legacy `_compute_worker_pause_metrics()` with a call to the new Temporal client helper.
- Add `send_pause_signal()` and `send_resume_signal()` methods that use batch signal delivery.

### Phase 3: API Router Update

Modify `api_service/api/routers/system_worker_pause.py`:
- On `POST action=pause mode=quiesce`: after DB update, call service `send_pause_signal()`.
- On `POST action=resume` (from quiesce): after DB update, call service `send_resume_signal()`.

### Phase 4: API Guard

Modify `api_service/main.py`:
- Before `temporal_client.start_workflow()`, check the DB singleton's `paused` state.
- If paused, return a "system paused" response with metadata instead of starting a workflow.

### Phase 5: Dashboard Integration

FR-009 (dashboard) is a frontend concern. The backend changes in this feature provide all the data the dashboard needs. Dashboard updates are tracked separately.

## Complexity Tracking

No constitution violations.
