# Requirements Traceability Matrix: Agent Queue MVP

**Feature**: `009-agent-queue-mvp`  
**Source**: `docs/CodexTaskQueue.md`

| DOC-REQ ID | Mapped FR(s) | Planned Implementation Surface | Validation Strategy |
|------------|--------------|--------------------------------|--------------------|
| `DOC-REQ-001` | `FR-001` | Alembic migration + queue ORM model for `agent_jobs` | Migration unit/integration check verifies table creation |
| `DOC-REQ-002` | `FR-001` | Queue ORM fields and schema serialization | Unit tests verify persisted fields and defaults |
| `DOC-REQ-003` | `FR-001` | Status enum and transition validation | Unit tests verify allowed/terminal statuses |
| `DOC-REQ-004` | `FR-002` | Repository claim query using transactional locking | Concurrency unit test validates no duplicate claim |
| `DOC-REQ-005` | `FR-002` | Expired lease requeue/fail pre-claim step | Unit tests verify expired running jobs are reprocessed |
| `DOC-REQ-006` | `FR-002`, `FR-003` | `repositories.py` + `service.py` lifecycle methods | Unit tests verify repository transitions and service guardrails |
| `DOC-REQ-007` | `FR-003`, `FR-004` | FastAPI queue router with enqueue/claim/heartbeat/complete/fail/get/list handlers | Router unit/contract tests verify all endpoints and payloads |
| `DOC-REQ-008` | `FR-004` | Router module + `api_service/main.py` registration | API boot test verifies route availability at `/api/queue` |
| `DOC-REQ-009` | `FR-004` | Router dependencies use standard current-user auth | Router tests verify auth enforcement on protected endpoints |
| `DOC-REQ-010` | `FR-005` | Unit test modules for state transitions + SKIP LOCKED concurrency | `./tools/test_unit.sh` must pass with new tests included |
