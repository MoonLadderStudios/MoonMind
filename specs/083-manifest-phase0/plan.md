# Implementation Plan: Manifest Phase 0 Temporal Alignment

**Branch**: `083-manifest-phase0` | **Date**: 2026-03-17 | **Spec**: `specs/083-manifest-phase0/spec.md`

## Summary

Harden and validate the existing Temporal-based manifest ingest implementation to match the updated `docs/Tasks/ManifestTaskSystem.md` design. This consolidates specs 032 and 034 into a single aligned spec with full test coverage. Primary work is test augmentation and verification, with targeted code fixes for any gaps discovered.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, SQLAlchemy 2 (async), Pydantic 2, PyYAML, Temporal SDK
**Storage**: PostgreSQL (registry + queue), MinIO (artifacts)
**Testing**: `./tools/test_unit.sh` (required)
**Target Platform**: Linux containers via Docker Compose (api, temporal-worker, temporal-server, postgres, minio)
**Project Type**: Monorepo backend services
**Constraints**: Runtime mode only, Temporal-native execution, no Celery/RabbitMQ references

## Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I. One-Click Deployment with Smart Defaults | PASS | Uses existing Docker Compose runtime |
| II. Powerful Runtime Configurability | PASS | Execution policy (concurrency, failure) configurable per workflow |
| III. Modular and Extensible Architecture | PASS | Manifest contract, workflow, models in separate modules |
| IV. Avoid Exclusive Proprietary Vendor Lock-In | PASS | Temporal is self-hosted; capability derivation supports multiple providers |
| V. Self-Healing by Default | PASS | Temporal retry policies and heartbeating for Activities |
| VI. Facilitate Continuous Improvement | PASS | Full traceability from DOC-REQ through FR to tests |
| VII. Spec-Driven Development Is the Source of Truth | PASS | This spec consolidates 032+034 and aligns with updated ManifestTaskSystem.md |
| VIII. Skills Are First-Class and Easy to Add | PASS | Manifest nodes can execute any skill via child MoonMind.Run |

## Project Structure

### Spec artifacts (this feature)

```text
specs/083-manifest-phase0/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
└── contracts/
    └── requirements-traceability.md
```

### Source code (in scope)

```text
moonmind/
├── workflows/temporal/
│   ├── manifest_ingest.py          # MoonMind.ManifestIngest workflow + helpers
│   └── workflows/manifest_ingest.py # Lightweight variant
├── workflows/agent_queue/
│   └── manifest_contract.py        # Validation, normalization, secret detection
└── schemas/
    ├── manifest_ingest_models.py   # Pydantic models
    └── agent_queue_models.py       # Queue response models

tests/unit/
├── workflows/temporal/
│   ├── test_manifest_ingest.py
│   └── test_manifest_ingest_artifacts.py
├── workflows/agent_queue/
│   └── test_manifest_contract.py
├── services/
│   └── test_manifests_service.py
└── api/routers/
    └── test_manifests.py
```

## Phase Plan

### Phase 0: Gap Analysis

- Review existing test coverage for all 6 Updates, fan-out with concurrency/failure policy, artifact generation, and secret detection.
- Identify test gaps that must be filled to satisfy DOC-REQ traceability requirements.

### Phase 1: Test Augmentation

- Add missing test coverage for Temporal Updates (if gaps found).
- Add fan-out concurrency and failure policy tests.
- Add artifact generation verification tests.
- Verify secret leak detection and safe reference handling tests are comprehensive.

### Phase 2: Code Hardening

- Fix any implementation gaps discovered during test augmentation.
- Ensure execution policy boundaries are enforced (structural fields not overridable).
- Verify `ParentClosePolicy.REQUEST_CANCEL` propagation.

### Phase 3: Validation

- Run `./tools/test_unit.sh` and confirm all manifest test suites pass.
- Record evidence in `quickstart.md`.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I–VIII | PASS | No changes from initial check; plan stays within existing architecture |

## Complexity Tracking

No constitution violations identified. Scope is focused on test coverage and validation of existing implementation.
