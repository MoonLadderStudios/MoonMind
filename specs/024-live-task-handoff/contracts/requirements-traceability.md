# Requirements Traceability: 024-live-task-handoff

| Source Requirement | Spec FR Mapping | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-002, FR-004 | `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/service.py`, `moonmind/agents/codex_worker/worker.py` | Unit tests for lifecycle status transitions and worker report behavior |
| DOC-REQ-002 | FR-002 | `api_service/migrations/versions/202602180001_live_task_handoff.py`, `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/repositories.py` | Migration + repository/service tests validating persisted session fields |
| DOC-REQ-003 | FR-003 | `api_service/migrations/versions/202602180001_live_task_handoff.py`, `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/service.py` | API/service tests validating control event creation and metadata |
| DOC-REQ-004 | FR-001 | `api_service/api/routers/task_runs.py`, `moonmind/schemas/agent_queue_models.py`, `api_service/main.py` | Router unit tests for create/get/grant/revoke behaviors |
| DOC-REQ-005 | FR-006 | `api_service/api/routers/task_runs.py`, `moonmind/workflows/agent_queue/service.py`, `moonmind/workflows/agent_queue/repositories.py` | Router/service tests for pause/resume/takeover validations |
| DOC-REQ-006 | FR-007 | `api_service/api/routers/task_runs.py`, `moonmind/workflows/agent_queue/service.py` | Router tests for operator message persistence and response model |
| DOC-REQ-007 | FR-004, FR-005 | `moonmind/agents/codex_worker/worker.py`, `moonmind/workflows/agent_queue/service.py` | Worker tests for setup/report of ready/error state and attach metadata |
| DOC-REQ-008 | FR-008 | `moonmind/agents/codex_worker/worker.py`, `api_service/api/routers/task_runs.py` | Worker heartbeat tests + router worker-heartbeat authorization tests |
| DOC-REQ-009 | FR-006 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/agents/codex_worker/worker.py`, `api_service/static/task_dashboard/dashboard.js` | Worker/control-flow tests verifying pause checkpoints and takeover semantics |
| DOC-REQ-010 | FR-007 | `moonmind/workflows/agent_queue/service.py`, `api_service/static/task_dashboard/dashboard.js` | Operator message API tests + UI state handling checks |
| DOC-REQ-011 | FR-005, FR-010 | `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/service.py`, `api_service/static/task_dashboard/dashboard.js` | Tests ensuring RW reveal only through grant endpoint and audit events |
| DOC-REQ-012 | FR-011 | `moonmind/config/settings.py`, `.env-template`, `docker-compose.yaml`, `moonmind/agents/codex_worker/worker.py` | Settings/env parsing unit tests and runtime config wiring assertions |
| DOC-REQ-013 | FR-012 | `moonmind/agents/codex_worker/worker.py`, `moonmind/workflows/agent_queue/service.py` | Worker tests for tmate error path and continued run behavior |
| DOC-REQ-014 | FR-009 | `api_service/api/routers/task_dashboard_view_model.py`, `api_service/static/task_dashboard/dashboard.js` | Dashboard router config tests and UI interaction tests/manual smoke |
| DOC-REQ-015 | FR-013 | `api_service/Dockerfile`, `docker-compose.yaml` | Image/dependency validation via container startup and worker runtime checks |
| DOC-REQ-016 | FR-014 | `tests/unit/api/routers/test_task_runs.py`, `tests/unit/agents/codex_worker/test_worker.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, `tests/unit/config/test_settings.py` | `./tools/test_unit.sh` full regression must pass with live-handoff coverage |
