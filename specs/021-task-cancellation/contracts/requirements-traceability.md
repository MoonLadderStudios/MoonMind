# Requirements Traceability: 021-task-cancellation

| Source Requirement | Spec FR Mapping | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-002 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py`, `api_service/api/routers/agent_queue.py` | Repository + API unit tests for queued cancel transition/idempotency |
| DOC-REQ-002 | FR-003 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py` | Service/repository unit tests for running cancel metadata semantics |
| DOC-REQ-003 | FR-003, FR-006 | `moonmind/workflows/agent_queue/models.py`, `moonmind/schemas/agent_queue_models.py`, migration file | Schema/model tests and API serialization assertions |
| DOC-REQ-004 | FR-001 | `api_service/api/routers/agent_queue.py`, queue request schema updates | Router unit tests for `POST /cancel` |
| DOC-REQ-005 | FR-004 | `api_service/api/routers/agent_queue.py`, service/repository ack path | Router + repository tests for ownership/state checks |
| DOC-REQ-006 | FR-005 | `moonmind/workflows/agent_queue/repositories.py` | Repository unit tests for lease-expiry and retry suppression |
| DOC-REQ-007 | FR-002, FR-007 | `moonmind/workflows/agent_queue/service.py` | Service/API tests verifying cancellation events appended |
| DOC-REQ-008 | FR-007 | `moonmind/mcp/tool_registry.py` | MCP registry unit tests for `queue.cancel` discovery and dispatch |
| DOC-REQ-009 | FR-008 | `api_service/api/routers/task_dashboard_view_model.py`, `api_service/static/task_dashboard/dashboard.js` | Dashboard view-model unit tests + queue detail behavior checks |
| DOC-REQ-010 | FR-009 | `moonmind/agents/codex_worker/worker.py` (heartbeat loop/client model) | Worker unit tests for heartbeat-driven cancel flagging and interval cap |
| DOC-REQ-011 | FR-010 | `moonmind/agents/codex_worker/worker.py` | Worker unit tests ensuring cancel ack path and no completion on cancellation |
| DOC-REQ-012 | FR-011 | `moonmind/agents/codex_worker/handlers.py`, worker command paths | Handler/worker tests for cancellation-aware command interruption |
| DOC-REQ-013 | FR-012 | `tests/unit/...` queue/api/mcp/worker/dashboard suites | `./tools/test_unit.sh` with targeted assertions across all affected subsystems |
