# Requirements Traceability: Manifest Phase 0 Temporal Alignment

**Feature**: 083-manifest-phase0
**Date**: 2026-03-17

## DOC-REQ to FR Mapping

| DOC-REQ | FR IDs | Planned Implementation Surface | Validation Strategy |
|---------|--------|-------------------------------|---------------------|
| DOC-REQ-001 | FR-001 | `manifest_compile` Activity in `moonmind/workflows/temporal/manifest_ingest.py` | Unit tests: verify compiled plan structure from valid manifest YAML |
| DOC-REQ-002 | FR-002 | Node ID derivation in `moonmind/workflows/temporal/manifest_ingest.py` | Unit tests: verify deterministic node IDs from same content, different IDs from different content |
| DOC-REQ-003 | FR-001 | Manifest hash in `moonmind/workflows/agent_queue/manifest_contract.py` | Unit tests: verify content-addressable hash and version tracking |
| DOC-REQ-004 | FR-003 | Secret leak detection in `moonmind/workflows/agent_queue/manifest_contract.py` | Unit tests: verify rejection of raw secrets, acceptance of safe references |
| DOC-REQ-005 | FR-004 | 6 Update handlers in `ManifestIngestWorkflow` class | Unit tests: verify each Update produces correct state transition |
| DOC-REQ-006 | FR-005 | Summary/run-index artifact generation via `manifest_write_summary` Activity | Unit tests: verify artifacts written on successful and failed runs |
| DOC-REQ-007 | FR-006 | `ParentClosePolicy.REQUEST_CANCEL` + `CancelNodes` Update | Unit tests: verify cancellation propagation and per-node cancellation |
| DOC-REQ-008 | FR-007 | Execution policy validation in workflow `run()` method | Unit tests: verify policy limits apply, structural override rejected |
| DOC-REQ-009 | FR-008 | Child `MoonMind.Run` workflow spawning in `run_node()` | Unit tests: verify concurrency-limited fan-out and failure policy enforcement |
| DOC-REQ-010 | FR-009 | `normalize_manifest_job_payload` in `manifest_contract.py` | Unit tests: verify normalization, capability derivation, hash computation |
| DOC-REQ-011 | FR-010 | Response sanitization in `moonmind/schemas/agent_queue_models.py` | Unit tests: verify raw YAML content hidden in API responses |
| DOC-REQ-012 | FR-011 | All preceding test files | `./tools/test_unit.sh` pass gate |

## Coverage Status

- All DOC-REQ IDs mapped: ✅
- All FRs have validation strategy: ✅
- Implementation surfaces identified: ✅
