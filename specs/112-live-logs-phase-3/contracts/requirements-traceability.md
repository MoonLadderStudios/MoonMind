# Requirements Traceability: Live Logs Phase 3

| DOC-REQ ID | Functional Requirements | Planned Component/API | Validation Strategy |
| ---------- | ----------------------- | --------------------- | ------------------- |
| DOC-REQ-001 | FR-001 | `GET /api/task-runs/{id}/logs/stream` | Integration test connecting to endpoint checking SSE headers. |
| DOC-REQ-002 | FR-002 | `ObservabilityPublisher` (Service) | Unit test demonstrating fan-out distribution from a channel to multiple mock subscribers. |
| DOC-REQ-003 | FR-003 | `LogStreamEvent` model and payload structure | Pytest payload assertions checking sequence, text, streams, offsets. |
| DOC-REQ-004 | FR-001, FR-004 | API + `ObservabilityPublisher.subscribe(since=...)` | Request streaming starting from sequence X and verify earlier sequences are skipped. |
| DOC-REQ-005 | FR-004, FR-005 | FastAPI `Request.is_disconnected()` handler | Test client disconnect releases generator locks immediately. |
| DOC-REQ-006 | FR-006 | Task run observer background job or pub/sub hook | Assert DB states/metadata `last_log_at` and stream capability flags get updated. |
| DOC-REQ-007 | FR-007 | API Endpoint Response + Summary Metadata | API check returns 404 or `no_content` properly when streaming is unavailable, instructing client fallback. |
| DOC-REQ-008 | FR-003 | System Event Logger Publisher | Integration test producing a supervisor event, observing it tagged as `stream=system` by the client. |
