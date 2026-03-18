# Research: Manifest Phase 0 Temporal Alignment

**Feature**: 083-manifest-phase0
**Date**: 2026-03-17

## Research Summary

This rebaseline builds on existing implementation. No fundamental unknowns exist; all research confirms alignment with current codebase.

## R-001: Current Manifest Ingest Workflow Implementation

**Decision**: The `MoonMind.ManifestIngest` workflow at `moonmind/workflows/temporal/manifest_ingest.py` is fully implemented with compile, fan-out, finalize stages plus 6 Updates.

**Rationale**: Direct code review confirms the workflow class `ManifestIngestWorkflow` (lines 793–1133) implements all lifecycle stages described in `ManifestTaskSystem.md`.

**Alternatives considered**: None — the implementation already exists and is the target for test coverage hardening.

## R-002: Manifest Contract Validation

**Decision**: `moonmind/workflows/agent_queue/manifest_contract.py` provides deterministic normalization, secret leak detection, capability derivation, and secret ref collection.

**Rationale**: Code review of the 726-line contract module confirms `normalize_manifest_job_payload`, `detect_manifest_secret_leaks`, `derive_required_capabilities`, and `collect_manifest_secret_refs` all exist and are functional.

**Alternatives considered**: None — extending existing contract rather than replacing.

## R-003: Existing Test Coverage

**Decision**: Existing test suites cover manifest contract (`test_manifest_contract.py`), manifest ingest workflow (`test_manifest_ingest.py`, `test_manifest_ingest_artifacts.py`), manifest service (`test_manifests_service.py`, `test_manifest_sync_service.py`), and API routers (`test_manifests.py`).

**Rationale**: File listing in `tests/` confirms 7+ manifest-related test files exist. Gap analysis needed for Update handler coverage and fan-out policy tests.

**Alternatives considered**: None — augment existing suites.

## R-004: Temporal Worker Topology

**Decision**: Manifest ingest workflows run on the `mm.workflow` task queue. Activities (`manifest_read`, `manifest_compile`, `manifest_write_summary`) route via appropriate activity queues.

**Rationale**: Per `ActivityCatalogAndWorkerTopology.md`, workflow workers handle orchestration while activity workers handle I/O operations.

**Alternatives considered**: None — follows established topology.
