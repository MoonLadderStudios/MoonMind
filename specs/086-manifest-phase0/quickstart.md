# Quickstart: Manifest Phase 0 Temporal Alignment

**Feature**: 086-manifest-phase0
**Date**: 2026-03-17

## Prerequisites

- Temporal infrastructure running (server, workers) per `docs/Temporal/TemporalArchitecture.md`
- Docker Compose stack operational
- Python 3.11+ with project dependencies installed

## Run Unit Tests

```bash
./tools/test_unit.sh
```

All manifest-related tests should pass:
- `tests/unit/workflows/temporal/test_manifest_ingest.py`
- `tests/unit/workflows/temporal/test_manifest_ingest_artifacts.py`
- `tests/unit/workflows/agent_queue/test_manifest_contract.py`
- `tests/unit/services/test_manifests_service.py`
- `tests/unit/api/routers/test_manifests.py`

## Validation Evidence

After implementation, verify:

1. **Compile tests**: Manifest compilation produces stable node IDs and correct plan structure
2. **Update tests**: All 6 Updates produce correct state transitions
3. **Fan-out tests**: Concurrency limits and failure policies are enforced
4. **Artifact tests**: Summary and run-index artifacts contain correct content
5. **Secret tests**: Raw secret material is rejected; safe references are accepted
6. **API tests**: Queue and registry responses do not expose raw manifest content
