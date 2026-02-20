# Requirements Traceability Matrix: Agent Queue Artifact Upload

**Feature**: `010-agent-queue-artifacts`  
**Source**: `docs/TaskQueueSystem.md`

| DOC-REQ ID | Mapped FR(s) | Planned Implementation Surface | Validation Strategy |
|------------|--------------|--------------------------------|--------------------|
| `DOC-REQ-001` | `FR-001`, `FR-002` | Queue router artifact upload endpoint + artifact root config in settings | Router unit tests + `tests/unit/config/test_settings.py` |
| `DOC-REQ-002` | `FR-001` | `POST /api/queue/jobs/{jobId}/artifacts/upload` handler in queue router | API unit tests validate endpoint behavior and schema |
| `DOC-REQ-003` | `FR-001` | Multipart parsing and request validation in router/service schemas | API unit tests validate required/optional multipart fields |
| `DOC-REQ-004` | `FR-002` | Job-scoped storage helper computing `<root>/<job_id>/<name>` paths | Storage/repository tests verify computed path placement |
| `DOC-REQ-005` | `FR-003` | `agent_job_artifacts` table + repository persistence | Migration and repository tests validate metadata row creation |
| `DOC-REQ-006` | `FR-004` | `GET /artifacts` and `GET /artifacts/{artifactId}/download` endpoints | API unit tests validate list/download happy path and error paths |
| `DOC-REQ-007` | `FR-005` | Traversal + max-size validations in storage/service | Unit tests for traversal rejection and size-limit failures |
| `DOC-REQ-008` | `FR-002` | Settings fields `AGENT_JOB_ARTIFACT_ROOT` and `AGENT_JOB_ARTIFACT_MAX_BYTES` for queue artifact constraints | `tests/unit/config/test_settings.py` verifies default and env override behavior |
