# Implementation Plan: Manifest Queue Plumbing (Phase 0)

**Branch**: `028-manifest-queue` | **Date**: 2026-02-19 | **Spec**: specs/028-manifest-queue/spec.md  
**Input**: Feature specification from `specs/028-manifest-queue/spec.md`

## Summary

Phase 0 adds queue plumbing for manifest ingestion. We will (1) register `manifest` as a supported job type and gate it through a manifest-specific normalization pipeline, (2) introduce a manifest contract module that parses YAML, enforces name consistency, hashes the payload, and derives required capabilities, and (3) expose `/api/manifests` CRUD plus `/runs` submission endpoints backed by the existing `manifest` table. The work happens inside the FastAPI service (`api_service`) and shared `moonmind` package, with regression coverage wired into the existing pytest suite via `./tools/test_unit.sh`.

## Technical Context

**Language/Version**: Python 3.11 (repo standard)  
**Primary Dependencies**: FastAPI (API surface), Pydantic (payload models), SQLAlchemy + Postgres (agent queue + manifest tables), internal Agent Queue service layer  
**Storage**: Postgres (`agent_queue` tables, `manifest` table with `state_json`)  
**Testing**: pytest executed through `./tools/test_unit.sh` (includes API + service tests)  
**Target Platform**: Linux containers (Docker Compose stack)  
**Project Type**: Backend services + shared library (`moonmind` package consumed by api_service and workers)  
**Performance Goals**: Queue job creation must remain sub-50 ms p95 and not increase DB load beyond existing task submissions; manifest registry GET requests should stay cache-friendly (<10ms p95 hitting Postgres)  
**Constraints**: Queue payloads must remain token-free, and manifest normalization must be deterministic for hashing/deletion semantics described in docs/ManifestTaskSystem.md  
**Scale/Scope**: Support dozens of concurrent manifest submissions/day with capability derivation covering GitHub, Google Drive, Confluence, Local FS, embeddings, and vector store combinations

## Constitution Check

The `.specify/memory/constitution.md` placeholder does not define enforceable principles, so no blocking gates exist beyond standard repo guardrails (test coverage + security guardrails). We will still treat "Test-First" and "Observability" as implied requirements by ensuring new runtime code ships with pytest coverage and emits structured validation errors. **Gate Status: PASS**

## Project Structure

### Documentation (this feature)

```text
specs/028-manifest-queue/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    └── manifests-api.md
```

### Source Code (repository root)

```text
moonmind/
└── workflows/
    └── agent_queue/
        ├── job_types.py            # new manifest allowlist module
        ├── manifest_contract.py    # new manifest payload normalization
        ├── service.py              # route manifest jobs through contract
        └── task_contract.py        # untouched (task-only contracts)

api_service/
├── api/
│   └── manifests.py               # new FastAPI router for registry CRUD + runs
├── services/
│   └── manifests_service.py       # orchestrates DB access + queue submissions
├── db/
│   └── models/manifest.py         # extend ManifestRecord columns
└── tests/
    ├── api/
    │   └── test_manifests_api.py  # endpoint coverage
    └── workflows/
        └── test_agent_queue_manifest.py  # manifest contract + queue gating tests
```

**Structure Decision**: Work stays inside `moonmind/workflows/agent_queue` for shared queue plumbing and `api_service` for HTTP exposure. No new top-level packages are needed; adopting dedicated modules keeps manifest-specific logic isolated from existing task contract files and simplifies future worker reuse.

## Complexity Tracking

_No additional complexity exemptions required; changes reuse existing queue + API stack and add only the minimal manifest-specific modules described above._
