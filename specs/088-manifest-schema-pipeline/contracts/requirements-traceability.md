# Requirements Traceability: Manifest Schema & Data Pipeline

**Feature**: `088-manifest-schema-pipeline`

| DOC-REQ ID | FR IDs | Planned Implementation Surfaces | Validation Strategy |
|------------|--------|--------------------------------|---------------------|
| DOC-REQ-001 | FR-001 | `moonmind/manifest/validator.py`, `moonmind/schemas/manifest_models.py` | Unit tests: valid/invalid YAML against v0 schema, required field enforcement, type constraints |
| DOC-REQ-002 | FR-001 | `moonmind/manifest/validator.py` (semantic validation) | Unit tests: dimension ↔ model mismatch, missing auth, broken index refs |
| DOC-REQ-003 | FR-002 | `moonmind/indexers/{github,google_drive,confluence,local_data}_indexer.py`, `moonmind/manifest/reader_adapter.py` | Unit tests: adapter contract tests for each reader type; integration: fetch from test sources |
| DOC-REQ-004 | FR-003 | `moonmind/rag/cli.py` (manifest subcommands) | Unit tests: CLI argument parsing; integration: validate + plan example YAMLs |
| DOC-REQ-005 | FR-004 | `moonmind/manifest/runner.py`, `moonmind/workflows/temporal/activity_runtime.py` | Unit tests: pipeline stage ordering; integration: end-to-end run against test Qdrant |
| DOC-REQ-006 | FR-004 | `moonmind/manifest/runner.py` (chunking/batching config), manifest YAML `transforms`/`run` blocks | Unit tests: chunk size/overlap, batch size, concurrency enforcement |
| DOC-REQ-007 | FR-005 | `moonmind/manifest/validator.py` (PII check), `moonmind/workflows/agent_queue/manifest_contract.py` (secret leak detection) | Unit tests: reject raw secrets, PII redaction flag enforcement, metadata allowlist |
| DOC-REQ-008 | FR-006 | `moonmind/manifest/reader_adapter.py` (ReaderAdapter protocol + registry) | Unit tests: new adapter registration without schema changes; contract test for plan/fetch/state |
| DOC-REQ-009 | FR-007 | CI pipeline (`./tools/test_unit.sh`), example YAML validation | CI: all `examples/*.yaml` validate and plan; evaluation thresholds gate on regression |
