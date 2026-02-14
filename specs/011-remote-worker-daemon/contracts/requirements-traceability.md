# Requirements Traceability Matrix: Agent Queue Remote Worker Daemon

**Feature**: `011-remote-worker-daemon`  
**Source**: `docs/CodexTaskQueue.md`

| DOC-REQ ID | Mapped FR(s) | Planned Implementation Surface | Validation Strategy |
|------------|--------------|--------------------------------|--------------------|
| `DOC-REQ-001` | `FR-001` | `moonmind/agents/codex_worker/cli.py` + poetry script registration | CLI unit tests + pyproject script assertion |
| `DOC-REQ-002` | `FR-001` | New package files `moonmind/agents/codex_worker/{worker,handlers,cli}.py` | Unit tests cover each module boundary |
| `DOC-REQ-003` | `FR-002`, `FR-006` | Worker daemon claim loop + heartbeat task in `worker.py` | Worker unit tests for claim and heartbeat cadence |
| `DOC-REQ-004` | `FR-003` | `codex_exec` handler dispatch and Codex subprocess execution in `handlers.py` | Handler unit tests for command invocation and outcomes |
| `DOC-REQ-005` | `FR-004` | Artifact upload client calls from worker/handler pipeline | Worker/handler tests assert artifact upload requests |
| `DOC-REQ-006` | `FR-004` | Job completion/failure REST transitions in worker client | Worker tests assert complete/fail endpoint invocation |
| `DOC-REQ-007` | `FR-002` | Environment-based worker config loader in `worker.py`/`cli.py` | Config unit tests for defaults and overrides |
| `DOC-REQ-008` | `FR-005` | Startup preflight (`verify_cli_is_executable` + `codex login status`) in `cli.py` + `utils.py` | CLI/preflight tests for pass/fail behavior |
| `DOC-REQ-009` | `FR-003` | `CodexExecPayload` parsing/validation in `handlers.py` | Handler validation tests for required payload fields |
| `DOC-REQ-010` | `FR-003`, `FR-004` | Checkout, codex exec, log capture, patch generation, publish mode branching | Handler unit tests for generated artifacts and publish behavior |
| `DOC-REQ-011` | `FR-006` | Heartbeat interval policy and crash-safe execution flow | Worker tests for lease renewal interval and failure handling |
| `DOC-REQ-012` | `FR-001` | Standalone daemon lifecycle independent of Celery imports | CLI tests verify direct worker entrypoint behavior |
