# Implementation Plan: Temporal Local Artifact System

**Branch**: `045-temporal-artifact-local-dev` | **Date**: 2026-03-05 | **Spec**: `specs/045-temporal-artifact-local-dev/spec.md`  
**Input**: Feature specification from `/specs/045-temporal-artifact-local-dev/spec.md`

## Summary

Implement the runtime local/dev artifact system for Temporal with MinIO as the default blob backend, Postgres as metadata index, and activity-bounded artifact IO. The plan upgrades the current local filesystem artifact implementation to MinIO-first behavior, aligns auth behavior with `AUTH_PROVIDER` mode, preserves immutable `ArtifactRef` semantics, and adds validation coverage for create/upload/complete/read/list/link/pin/delete, preview safety, and lifecycle cleanup.

## Technical Context

**Language/Version**: Python 3.11, Docker Compose YAML, Alembic migrations, OpenAPI contracts  
**Primary Dependencies**: FastAPI, SQLAlchemy, Pydantic v2, Temporal activity interfaces, S3-compatible client path for MinIO (runtime adapter in artifact service)  
**Storage**: Postgres (`temporal_artifacts`, `temporal_artifact_links`, `temporal_artifact_pins`) + MinIO object storage bucket for artifact bytes  
**Testing**: `./tools/test_unit.sh`, targeted Temporal artifact integration checks under `tests/integration/temporal/`, runtime scope validation script  
**Target Platform**: Docker Compose local/dev deployment (`api`, `celery-worker`, `temporal*`, `minio`) on internal Docker networks  
**Project Type**: Multi-service backend runtime and API feature  
**Performance Goals**: Meet SC-001/SC-003 reliability thresholds (95%+ first-attempt success for local artifact and multipart validation paths) while keeping workflow payloads reference-sized  
**Constraints**: Runtime implementation mode is mandatory; docs-only output is non-compliant. Preserve workflow determinism by keeping byte IO in activities. No blob bytes in Postgres. MinIO is baseline local/dev backend.  
**Scale/Scope**: Artifact runtime stack for Temporal workflows, API contracts, auth-mode behavior, lifecycle cleanup, and production-facing validation tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. MinIO is treated as default compose backend with no-secret local defaults and internal-network reachability.
- **II. Avoid Vendor Lock-In**: PASS. Blob operations stay behind S3-compatible adapter boundaries; MinIO default remains overrideable.
- **III. Own Your Data**: PASS. Artifacts remain in operator-controlled Postgres + object storage, not proprietary external stores by default.
- **IV. Skills Are First-Class and Easy to Add**: PASS. Feature does not alter skill contracts; artifact references remain runtime-neutral inputs/outputs.
- **V. Design for Evolution / Scientific Method Loop**: PASS. Contracts + tests anchor behavior so storage adapter internals remain replaceable.
- **VI. Powerful Runtime Configurability**: PASS. Artifact backend, bucket, TTL, size thresholds, and auth mode remain env/config controlled.
- **VII. Modular and Extensible Architecture**: PASS. Changes stay in temporal artifact service/router/schema/config boundaries.
- **VIII. Self-Healing by Default**: PASS. Lifecycle cleanup and delete semantics are idempotent and retry-safe by design.
- **IX. Facilitate Continuous Improvement**: PASS. Artifact operations are auditable and can emit structured summaries/metrics for follow-up.
- **X. Spec-Driven Development**: PASS. `DOC-REQ-001` through `DOC-REQ-015` are traced in plan outputs and validation strategy.

### Post-Design Re-Check

- PASS. Phase 1 artifacts preserve runtime-first scope and explicit validation coverage.
- PASS. Runtime-vs-docs mode handling is explicitly gated and aligned to selected runtime mode.
- PASS. No constitution violations require complexity exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/045-temporal-artifact-local-dev/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── temporal-artifacts.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docker-compose.yaml
.env-template

moonmind/
├── config/settings.py
├── schemas/temporal_artifact_models.py
└── workflows/temporal/
    ├── artifacts.py
    └── __init__.py

api_service/
├── api/routers/temporal_artifacts.py
├── db/models.py
└── migrations/versions/
    └── 202603050001_temporal_artifact_system.py

tests/
├── unit/api/routers/test_temporal_artifacts.py
├── unit/workflows/temporal/test_artifacts.py
└── integration/temporal/
    └── test_temporal_artifact_local_dev.py                # planned
```

**Structure Decision**: Extend existing Temporal artifact runtime modules in place. Keep API/DB/service boundaries explicit, add MinIO wiring at config/compose level, and expand tests in current temporal unit/integration suites.

## Phase 0 - Research Summary

Research outcomes in `specs/045-temporal-artifact-local-dev/research.md` establish:

1. MinIO must be the default local/dev blob backend; local filesystem storage is dev fallback only when explicitly selected.
2. Artifact metadata/indexing remains in Postgres; blob bytes stay in object storage only.
3. Auth behavior must mirror app auth mode (`AUTH_PROVIDER=disabled` default no-end-user-auth local mode, stricter checks for authenticated modes).
4. Artifact API contract must include direct and multipart uploads plus short-lived, scoped presign behavior.
5. Lifecycle behavior must remain idempotent with soft-delete first, hard-delete/tombstone follow-up policy.
6. Runtime/docs behavior remains mode-aligned: runtime mode mandates production code plus tests; docs mode scope checks skip runtime gating.

## Phase 1 - Design Outputs

- `research.md`: decisions, rationale, and alternatives for MinIO defaulting, auth mode behavior, multipart strategy, lifecycle policy, and mode alignment.
- `data-model.md`: artifact entities, retention/link models, access grant semantics, and lifecycle state transitions.
- `contracts/temporal-artifacts.openapi.yaml`: planned REST contract for create/presign/complete/get/list/link/pin/delete flows.
- `contracts/requirements-traceability.md`: complete `DOC-REQ-*` mapping to FRs, implementation surfaces, and validation strategy.
- `quickstart.md`: deterministic runtime validation steps and mode-aware scope checks.

## Runtime-vs-Docs Mode Alignment Gate

- Selected orchestration mode for this feature: **runtime implementation mode**.
- Planning and subsequent task execution must include:
  - production runtime code changes (`api_service/`, `moonmind/`, `services/`, `docker-compose*.yaml`), and
  - validation tests (`tests/` + `./tools/test_unit.sh`).
- Docs mode behavior remains explicitly documented:
  - `./.specify/scripts/bash/validate-implementation-scope.sh --mode docs` skips runtime scope enforcement,
  - but this feature is not eligible for docs-only completion.

## Remediation Gates (Prompt B)

- Runtime mode must keep both production runtime implementation tasks and validation tasks in `tasks.md`; docs-only task sets are invalid.
- Every `DOC-REQ-*` row must map to at least one FR, one planned implementation surface, and one planned validation strategy in `contracts/requirements-traceability.md`.
- `DOC-REQ-001` through `DOC-REQ-015` must keep explicit implementation + validation task coverage in the `DOC-REQ Coverage Matrix` in `tasks.md`.
- Cross-artifact language for runtime scope, validation expectations, and traceability must remain deterministic across `spec.md`, `plan.md`, and `tasks.md`.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime-mode scope is now explicitly represented and deterministic in `tasks.md` with production runtime task coverage (`T001-T012`, `T017-T021`, `T025-T029`, `T033-T037`) and validation task coverage (`T013-T016`, `T022-T024`, `T030-T032`, `T039-T041`).
- `DOC-REQ-*` mapping remains complete from `DOC-REQ-001` through `DOC-REQ-015`, and each requirement is represented with implementation + validation task coverage in the `DOC-REQ Coverage Matrix`.
- Prompt B scope controls now explicitly require deterministic cross-artifact updates so scope gates stay aligned during downstream task execution.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope-control wording in `tasks.md` and aligned quality gate language so runtime/validation expectations are auditable.
- Added Prompt B remediation status language in `spec.md` to keep runtime-mode, traceability, and risk statements synchronized with planning/task artifacts.

### Residual risks

- Implementation will touch API/router/model/migration/activity boundaries; drift risk remains if task execution bypasses the defined runtime gates.
- Lifecycle, authorization, and multipart behavior still require full runtime validation execution (`./tools/test_unit.sh` and scope gates) before rollout confidence is justified.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
