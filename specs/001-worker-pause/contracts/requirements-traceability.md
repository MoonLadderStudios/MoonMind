# Requirements Traceability Matrix: Worker Pause System

**Feature**: `001-worker-pause`  
**Source**: `docs/WorkerPauseSystem.md`

| DOC-REQ ID | Mapped FR(s) | Planned Implementation Surface | Validation Strategy |
|------------|--------------|--------------------------------|--------------------|
| `DOC-REQ-001` | `FR-001` | New SQLAlchemy model + migration for `system_worker_pause_state`; repository helpers in `moonmind/workflows/agent_queue/repositories.py` | Migration tests ensure defaults + version increments; repository unit tests cover singleton fetch/update semantics |
| `DOC-REQ-002` | `FR-002` | `SystemControlEvent` model + append helper + audit API response in `GET /api/system/worker-pause` | Repository tests assert events written per toggle; API tests verify event count increments |
| `DOC-REQ-003` | `FR-003`, `FR-004`, `FR-010` | FastAPI router `api_service/api/routers/system_worker_pause.py`, new Pydantic schemas, integration into dashboard + MCP | API unit tests hit GET/POST, ensuring metrics, validation, and error handling; dashboard Cypress/unit tests stub endpoints |
| `DOC-REQ-004` | `FR-005`, `FR-010` | `AgentQueueService.claim_job` guard, `ClaimJobResponse` schema updates, queue service tests | Service tests verify `_requeue_expired_jobs` not invoked when paused; REST tests assert `system` block + HTTP 200 |
| `DOC-REQ-005` | `FR-006` | `JobModel` / heartbeat response extended with `system` metadata, service packaging that metadata | API tests simulate heartbeat to ensure `system` block appears; worker unit tests confirm new payload shape respected |
| `DOC-REQ-006` | `FR-007` | `QueueApiClient` + `CodexWorker` loop updates to read `system` metadata, adjust logging/backoff | Worker unit tests assert pause-aware sleep/backoff; integration test ensures logs emitted once per version |
| `DOC-REQ-007` | `FR-008` | `CodexWorker` safe-checkpoint pause wiring triggered by quiesce mode metadata; future worker runtime hooks share same event | Worker tests cover `_wait_if_paused` triggered by heartbeat `system.mode="quiesce"` |
| `DOC-REQ-008` | `FR-009` | Dashboard banner + controls in `api_service/static/task_dashboard/dashboard.js` + view model; new `/api/system/worker-pause` fetchers | Frontend Jest/unit tests (or jsdom) for new components; manual quickstart ensures banner + controls operate |
| `DOC-REQ-009` | `FR-003`, `FR-006` | Shared schema `SystemWorkerPauseStatusModel` reused by REST and `moonmind/mcp/tool_registry.py` results | MCP router tests confirm `queue.claim` and `queue.heartbeat` responses now carry `system` metadata |
