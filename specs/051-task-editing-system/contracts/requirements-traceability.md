# Requirements Traceability: Task Editing System

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | `docs/TaskEditingSystem.md` §1, §2 Goal 1 | FR-001 | `moonmind/workflows/agent_queue/service.py`, `api_service/static/task_dashboard/dashboard.js` | Service + dashboard tests assert editability requires `task` + `queued` + `startedAt=null`. |
| DOC-REQ-002 | `docs/TaskEditingSystem.md` §2 Goal 2 | FR-002 | `moonmind/workflows/agent_queue/service.py`, `api_service/api/routers/agent_queue.py` | Service/router tests assert update preserves same job ID and mutates in place. |
| DOC-REQ-003 | `docs/TaskEditingSystem.md` §2 Goal 3, §3.1 | FR-003 | `api_service/static/task_dashboard/dashboard.js` | Dashboard tests verify Edit entry point appears for eligible detail jobs (list action optional). |
| DOC-REQ-004 | `docs/TaskEditingSystem.md` §3.2 | FR-004 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard_view_model.py` | Dashboard tests verify `editJobId` route parsing (including `/tasks/new` alias behavior), prefill flow, Update CTA label, and cancel navigation target. |
| DOC-REQ-005 | `docs/TaskEditingSystem.md` §3.2 step 5, §4.2, §6.2 | FR-005 | `api_service/static/task_dashboard/dashboard.js`, `moonmind/schemas/agent_queue_models.py`, `moonmind/workflows/agent_queue/service.py` | Dashboard/service tests verify update submit uses create-like envelope plus `expectedUpdatedAt` conflict behavior. |
| DOC-REQ-006 | `docs/TaskEditingSystem.md` §4.1 | FR-006 | `api_service/api/routers/agent_queue.py`, `moonmind/workflows/agent_queue/service.py` | Router/service tests verify authenticated `PUT /api/queue/jobs/{id}` accepts only queued never-started jobs. |
| DOC-REQ-007 | `docs/TaskEditingSystem.md` §4.2 | FR-007 | `moonmind/schemas/agent_queue_models.py`, `moonmind/workflows/agent_queue/service.py` | Schema/service tests verify optional `expectedUpdatedAt` and `note` handling with `note` retained in audit event payload only. |
| DOC-REQ-008 | `docs/TaskEditingSystem.md` §4.3 | FR-006 | `api_service/api/routers/agent_queue.py` | Router tests verify successful update returns updated `JobModel` (HTTP 200). |
| DOC-REQ-009 | `docs/TaskEditingSystem.md` §4.4 | FR-008 | `api_service/api/routers/agent_queue.py`, `moonmind/workflows/agent_queue/service.py` | Router tests verify `404/403/409/422/400` mappings and stable error codes/messages. |
| DOC-REQ-010 | `docs/TaskEditingSystem.md` §5.1 | FR-009 | `moonmind/workflows/agent_queue/service.py`, `moonmind/workflows/agent_queue/repositories.py` | Service tests verify lock + auth + invariants + normalization + update + audit event + commit path. |
| DOC-REQ-011 | `docs/TaskEditingSystem.md` §5.2 | FR-010 | `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/repositories.py` | Repository/service tests confirm no new-table dependency and reuse of existing mutable fields/event append path. |
| DOC-REQ-012 | `docs/TaskEditingSystem.md` §6.1 | FR-011 | `moonmind/workflows/agent_queue/service.py`, `moonmind/workflows/agent_queue/repositories.py` | Concurrency tests verify claim/update race resolves safely with deterministic conflict outcomes. |
| DOC-REQ-013 | `docs/TaskEditingSystem.md` §6.2 | FR-005, FR-011 | `moonmind/workflows/agent_queue/service.py`, `api_service/static/task_dashboard/dashboard.js` | Service/dashboard tests verify stale `expectedUpdatedAt` requests are rejected with explicit conflict. |
| DOC-REQ-014 | `docs/TaskEditingSystem.md` §7, §8 | FR-012 | `api_service/api/routers/task_dashboard_view_model.py`, `api_service/static/task_dashboard/dashboard.js` | View-model/dashboard tests verify queue update endpoint template and edit-mode behavior on create route. |
| DOC-REQ-015 | `docs/TaskEditingSystem.md` §9 | FR-016 | `moonmind/workflows/agent_queue/service.py`, `api_service/api/routers/agent_queue.py`, `api_service/static/task_dashboard/dashboard.js` | Required coverage includes `tests/unit/workflows/agent_queue/test_service_update.py`, `tests/unit/api/routers/test_agent_queue.py`, and `tests/task_dashboard/test_submit_runtime.js` for service update rules, router mappings, and dashboard edit-mode/update-submit behavior. |
| DOC-REQ-016 | `docs/TaskEditingSystem.md` §10 | FR-013 | `moonmind/workflows/agent_queue/service.py`, `api_service/static/task_dashboard/dashboard.js` | Service/dashboard tests assert attachment mutation is excluded from v1 edit flow. |
| DOC-REQ-017 | `docs/TaskEditingSystem.md` §11 | FR-014 | `docs/TaskQueueSystem.md`, `docs/TaskUiArchitecture.md`, `api_service/api/routers/agent_queue.py` | Documentation/API surface checks ensure additive rollout and queue-update endpoint visibility. |
| DOC-REQ-018 | Runtime scope guard from task objective | FR-015, FR-016 | Runtime code in `moonmind/` + `api_service/` and tests in `tests/unit` + `tests/task_dashboard` | Acceptance requires production runtime code + validation tests; docs/spec-only output is a failing condition. |

## Coverage Gate

- Row count matches source requirements: `18` `DOC-REQ-*` rows.
- Every row maps to at least one FR, one implementation surface, and one validation strategy.
- Runtime-vs-docs alignment is explicit in `DOC-REQ-018` and enforced through runtime validation expectations.
