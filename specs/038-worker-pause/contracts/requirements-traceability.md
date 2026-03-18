# Requirements Traceability: Worker Pause System (038)

| DOC-REQ | Spec FR | Plan Phase | Implementation Surface | Validation Strategy |
|---------|---------|------------|----------------------|---------------------|
| DOC-REQ-001 | FR-001, FR-004, FR-005 | Phase 3-4 | `system_worker_pause.py` (router), `main.py` (guard), `service.py` | Unit tests: pause/resume toggle + API guard |
| DOC-REQ-002 | FR-006 | Phase 1 | `temporal_client.py` (drain metrics), docs (`quickstart.md`) | Unit test: Visibility query returns drain metrics |
| DOC-REQ-003 | FR-007, FR-010 | Phase 1-3 | `temporal_client.py` (batch signal), `service.py`, `system_worker_pause.py` | Integration test: signal delivery → workflow pause |
| DOC-REQ-004 | FR-001 | Existing | `models.py` (DB model), migration | Existing unit tests |
| DOC-REQ-005 | FR-005 | Phase 4 | `main.py` (guard before `start_workflow`) | Unit test: POST /api/workflows returns 503 when paused |
| DOC-REQ-006 | FR-009 | Phase 5 (future) | Dashboard JavaScript (frontend) | Manual verification / browser test |
| DOC-REQ-007 | FR-003, FR-004 | Phase 2-3 | `system_worker_pause.py`, `service.py` | Unit tests: GET/POST endpoints |
| DOC-REQ-008 | FR-007 | Existing | `run.py` (`@workflow.signal pause/resume`, `wait_condition`) | Integration test: signal → workflow blocks |
| DOC-REQ-009 | FR-008 | Phase 1 | `temporal_client.py` (heartbeat checkpoint helper) | Unit test: checkpoint data yielded |
| DOC-REQ-010 | FR-002 | Existing | `service.py` (audit event insertion) | Existing unit tests |
