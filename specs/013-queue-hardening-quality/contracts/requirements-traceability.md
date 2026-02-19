# Requirements Traceability: Agent Queue Hardening and Quality (Milestone 5)

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | docs/TaskQueueSystem.md:489-491 | FR-001 | `api_service/api/routers/agent_queue.py`, `moonmind/workflows/agent_queue/service.py` | Router tests verify worker mutation endpoints reject missing/invalid worker auth. |
| DOC-REQ-002 | docs/TaskQueueSystem.md:410-415 | FR-001, FR-002 | `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py` | Repository/service tests verify token hash lookup, identity resolution, and inactive token rejection. |
| DOC-REQ-003 | docs/TaskQueueSystem.md:419-420 | FR-002 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py`, `api_service/api/routers/agent_queue.py` | Claim tests verify repo/job-type allowlist filtering and worker mismatch handling. |
| DOC-REQ-004 | docs/TaskQueueSystem.md:97,493 | FR-003 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py`, `moonmind/schemas/agent_queue_models.py` | Repository/service tests verify required capability matching and claim skip behavior. |
| DOC-REQ-005 | docs/TaskQueueSystem.md:63-72 | FR-005 | `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py` | Tests verify append-only event persistence and serialization shape. |
| DOC-REQ-006 | docs/TaskQueueSystem.md:494 | FR-005 | `api_service/api/routers/agent_queue.py`, `moonmind/schemas/agent_queue_models.py` | API tests verify event polling endpoint supports incremental `after` filtering. |
| DOC-REQ-007 | docs/TaskQueueSystem.md:495 | FR-004 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py`, `moonmind/workflows/agent_queue/models.py` | Tests verify retryable fail schedules delayed requeue via `next_attempt_at`. |
| DOC-REQ-008 | docs/TaskQueueSystem.md:495 | FR-004 | `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/repositories.py` | Tests verify exhausted retries transition to `dead_letter` and remain unclaimable. |
| DOC-REQ-009 | docs/TaskQueueSystem.md:93-100 | FR-003, FR-006 | `moonmind/workflows/agent_queue/repositories.py` | Concurrent/lease-expiry tests verify atomic requeue/fail behavior and deterministic claim ordering. |
| DOC-REQ-010 | docs/TaskQueueSystem.md:421 | FR-007 | `moonmind/workflows/agent_queue/service.py`, `api_service/api/routers/agent_queue.py` | Existing artifact validation tests remain green; add regression coverage for authenticated worker flow. |
