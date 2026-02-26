# Requirements Traceability: Orchestrator Task Runtime Upgrade

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | `docs/OrchestratorTaskRuntime.md` §1, §3 Goal 1 | FR-001, FR-002 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/orchestrator.py`, `moonmind/workflows/orchestrator/serializers.py` | Dashboard unit tests assert task-first labels/links; API contract tests verify `taskId` + transitional `runId`. |
| DOC-REQ-002 | `docs/OrchestratorTaskRuntime.md` §1, §3 Goal 2, §5.3 | FR-006, FR-008 | `api_service/static/task_dashboard/dashboard.js`, `moonmind/schemas/workflow_models.py`, `api_service/api/routers/orchestrator.py` | JS submit tests cover step+skill orchestration mode; API tests cover explicit-step and no-step create behaviors. |
| DOC-REQ-003 | `docs/OrchestratorTaskRuntime.md` §1, §3 Goal 3, §5.2 | FR-003, FR-004, FR-005 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard.py`, `api_service/api/routers/task_dashboard_view_model.py` | Dashboard tests verify unified `/tasks/list` and `/tasks/:taskId` behavior for queue + orchestrator sources. |
| DOC-REQ-004 | `docs/OrchestratorTaskRuntime.md` §1, §3 Goal 4, §5.6 | FR-012, FR-013 | `moonmind/workflows/orchestrator/state_sink.py`, `moonmind/workflows/orchestrator/queue_worker.py` | Unit/integration tests simulate DB failure mid-task and assert continuation + later terminal reconciliation. |
| DOC-REQ-005 | `docs/OrchestratorTaskRuntime.md` §5.1 | FR-001, FR-002 | `api_service/api/routers/orchestrator.py`, `moonmind/schemas/workflow_models.py`, `moonmind/workflows/orchestrator/serializers.py` | Contract tests compare `/orchestrator/tasks*` and `/orchestrator/runs*` parity across create/list/detail/approval/retry/artifacts. |
| DOC-REQ-006 | `docs/OrchestratorTaskRuntime.md` §5.2.1 | FR-003 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard.py` | Route tests verify legacy `/tasks/orchestrator*` and `/tasks/queue*` redirects to canonical unified routes. |
| DOC-REQ-007 | `docs/OrchestratorTaskRuntime.md` §5.2.2 | FR-004 | `api_service/static/task_dashboard/dashboard.js` | Dashboard list tests verify normalized shared row contract fields (`source`, `runtime`, `status`, `title`, timestamps). |
| DOC-REQ-008 | `docs/OrchestratorTaskRuntime.md` §5.2.3 | FR-005 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/orchestrator.py`, `api_service/api/routers/agent_queue.py` | Detail-page tests verify `source` hint behavior and fallback source resolution for one task id. |
| DOC-REQ-009 | `docs/OrchestratorTaskRuntime.md` §5.3.1 | FR-006 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard.py` | Submit-flow tests verify runtime=orchestrator keeps step editor + orchestrator fields while preserving queue capability fields. |
| DOC-REQ-010 | `docs/OrchestratorTaskRuntime.md` §5.3.2 | FR-007 | `api_service/api/routers/task_dashboard.py`, `moonmind/workflows/orchestrator/skill_executor.py`, `moonmind/workflows/skills/resolver.py` | API tests validate grouped skill payload (`worker`, `orchestrator`) and legacy compatibility list. |
| DOC-REQ-011 | `docs/OrchestratorTaskRuntime.md` §5.3.3 | FR-008 | `moonmind/schemas/workflow_models.py`, `api_service/api/routers/orchestrator.py`, `moonmind/workflows/orchestrator/services.py` | Contract/unit tests verify absent `steps[]` uses legacy plan, provided `steps[]` executes in declared order. |
| DOC-REQ-012 | `docs/OrchestratorTaskRuntime.md` §5.4 | FR-009 | `api_service/db/models.py`, `moonmind/workflows/orchestrator/repositories.py`, `api_service/migrations/versions/*orchestrator*` | Repository/model tests verify arbitrary step counts with stable IDs/order/status/attempt and artifact references. |
| DOC-REQ-013 | `docs/OrchestratorTaskRuntime.md` §5.4.2, §8 Q3 | FR-010 | `api_service/migrations/versions/*orchestrator*`, `api_service/db/models.py`, `moonmind/workflows/orchestrator/repositories.py` | Migration tests validate data move to canonical task/step model and compatibility behavior after legacy table retirement strategy. |
| DOC-REQ-014 | `docs/OrchestratorTaskRuntime.md` §5.5 | FR-011 | `moonmind/workflows/orchestrator/queue_dispatch.py`, `moonmind/workflows/orchestrator/queue_worker.py`, `moonmind/workflows/agent_queue/job_types.py` | Worker/dispatch tests cover both `orchestrator_task` and `orchestrator_run` payload handling and event emission parity. |
| DOC-REQ-015 | `docs/OrchestratorTaskRuntime.md` §5.6.1, §6 Phase 4 | FR-012 | `moonmind/workflows/orchestrator/state_sink.py`, `moonmind/workflows/orchestrator/queue_worker.py`, reconciliation module (planned) | Tests force DB sink failure and assert artifact snapshots plus replay/reconciliation to DB on recovery. |
| DOC-REQ-016 | `docs/OrchestratorTaskRuntime.md` §5.6.2 | FR-013 | `moonmind/workflows/orchestrator/queue_worker.py`, queue API client retry/reconcile path (planned) | Fault-injection tests verify heartbeat/lease failures do not abort execution and terminal queue status is retried/reconciled. |
| DOC-REQ-017 | `docs/OrchestratorTaskRuntime.md` §5.7 | FR-014 | `moonmind/workflows/orchestrator/policies.py`, `moonmind/workflows/orchestrator/skill_executor.py`, `api_service/api/routers/orchestrator.py`, auth dependencies in dashboard routes | Security tests verify approval-token enforcement, skill arg command injection blocking, and auth dependency parity. |
| DOC-REQ-018 | `docs/OrchestratorTaskRuntime.md` §7 Testing Strategy | FR-016 | `tests/contract/test_orchestrator_api.py`, `tests/task_dashboard/test_queue_layouts.js`, `tests/task_dashboard/test_submit_runtime.js`, `tests/unit/workflows/orchestrator/test_queue_worker.py`, new resilience tests | Execute `./tools/test_unit.sh` plus targeted orchestrator integration scenarios for alias contracts, dashboard unification, and degraded mode. |
| DOC-REQ-019 | Runtime scope guard from objective | FR-015, FR-016 | Runtime surfaces in `api_service/`, `moonmind/`, `celery_worker/`, and test suites under `tests/` | Runtime-mode scope gate must pass (`validate-implementation-scope.sh --mode runtime`) and docs-only changes are treated as failing delivery. |

## Coverage Gate

- All `DOC-REQ-001` through `DOC-REQ-019` map to at least one FR, planned implementation surface, and validation strategy.
- No document requirement is left unmapped.

## Execution Evidence (2026-02-26)

- Runtime validation and test evidence is recorded in [quickstart.md](/work/agent_jobs/2ed8484e-3b81-4ce6-bdf5-42b1ed10de81/repo/specs/042-orchestrator-task-runtime/quickstart.md) under "Execution Evidence (2026-02-26)".
- `DOC-REQ-018` and `DOC-REQ-019` validation gate status:
  - `./tools/test_unit.sh` passed (`882 passed`, `8 subtests passed`).
  - Runtime scope gates passed for both `--check tasks --mode runtime` and `--check diff --mode runtime --base-ref origin/main`.
- `DOC-REQ-004` and `DOC-REQ-014` integration evidence:
  - `docker compose -f docker-compose.test.yaml run --rm orchestrator-tests` was attempted and recorded as environment-blocked (missing Docker buildx plugin and `403 Forbidden` from daemon during build).
- Reconciliation with [tasks.md](/work/agent_jobs/2ed8484e-3b81-4ce6-bdf5-42b1ed10de81/repo/specs/042-orchestrator-task-runtime/tasks.md): every `DOC-REQ-001` through `DOC-REQ-019` remains mapped with implementation + validation task coverage in the `DOC-REQ Coverage Matrix`.
