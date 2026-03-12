# Tasks: Temporal Local Artifact System

**Input**: Design documents from `/specs/058-temporal-artifact-local-dev/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Tests are required because the feature specification mandates runtime validation coverage (`FR-002`).  
**Organization**: Tasks are grouped by user story to preserve independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only
- Every task includes concrete file path(s)

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly represented in `T001-T012`, `T017-T021`, `T025-T029`, `T033-T037`, and `T044`.
- Runtime validation tasks are explicitly represented in `T013-T016`, `T022-T024`, `T030-T032`, `T039-T041`, and `T045`.
- `DOC-REQ-001` through `DOC-REQ-016` implementation + validation coverage is enforced by the per-task tags and the `DOC-REQ Coverage Matrix` in this file, with persistent requirement mapping in `specs/058-temporal-artifact-local-dev/contracts/requirements-traceability.md`.
- Deterministic updates across `spec.md`, `plan.md`, and `tasks.md` are required for this remediation step.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish MinIO-first local/dev runtime wiring and artifact configuration defaults.

- [X] T001 Add MinIO as the default artifact backend service and wire API/worker connectivity in `docker-compose.yaml` (DOC-REQ-003, DOC-REQ-005).
- [X] T002 Add MinIO-first temporal artifact environment defaults (endpoint, bucket, credentials, TTL, thresholds) in `.env-template` (DOC-REQ-003, DOC-REQ-005, DOC-REQ-011).
- [X] T003 [P] Extend temporal artifact settings for S3-compatible backend selection and guardrails in `moonmind/config/settings.py` (DOC-REQ-003, DOC-REQ-010, DOC-REQ-011).
- [X] T004 [P] Mirror MinIO-backed artifact test stack wiring for local runtime validation in `docker-compose.test.yaml` (DOC-REQ-003, DOC-REQ-005).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build shared runtime/data/API primitives required by every user story.

**⚠️ CRITICAL**: User story implementation starts only after this phase is complete.

- [X] T005 Update temporal artifact persistence enums/columns/indexes for S3-backed metadata-only storage, multipart/session state, and lifecycle metadata in `api_service/db/models.py` (DOC-REQ-004, DOC-REQ-009, DOC-REQ-013).
- [X] T006 Update Alembic schema evolution for the temporal artifact model changes in `api_service/migrations/versions/202603050001_temporal_artifact_system.py` (DOC-REQ-004, DOC-REQ-013).
- [X] T007 [P] Extend request/response contracts for multipart upload, download grants, preview metadata, and lifecycle fields in `moonmind/schemas/temporal_artifact_models.py` (DOC-REQ-008, DOC-REQ-010, DOC-REQ-011, DOC-REQ-014).
- [X] T008 Implement MinIO/S3-compatible artifact store adapter and runtime backend selection in `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-008, DOC-REQ-011).
- [X] T009 Implement repository/service query helpers for execution linkage and deterministic latest-output selection in `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-009).
- [X] T010 Implement mode-aware principal resolution and API auth wiring for artifact routes in `api_service/api/routers/temporal_artifacts.py` and `api_service/auth.py` (DOC-REQ-006, DOC-REQ-007).
- [X] T011 Implement presign policy/audit helper surfaces (short-lived, scoped grants) in `moonmind/workflows/temporal/artifacts.py` and `api_service/api/routers/temporal_artifacts.py` (DOC-REQ-010).
- [X] T012 [P] Wire updated temporal artifact service exports/factories in `moonmind/workflows/temporal/__init__.py` and `moonmind/workflows/__init__.py` (DOC-REQ-015).

**Checkpoint**: Shared artifact runtime primitives are ready and story work can proceed.

---

## Phase 3: User Story 1 - Local Dev Artifact Flow Works End-to-End (Priority: P1) 🎯 MVP

**Goal**: Deliver create/upload/complete/read/list/link behavior with MinIO-backed bytes and `ArtifactRef`-based runtime contracts.

**Independent Test**: Start default local stack and execute create/upload/complete/read/list flows while verifying Temporal state carries references/small JSON only.

### Tests for User Story 1

- [X] T013 [P] [US1] Add service-level unit tests for `art_<ULID>` IDs, immutable completion semantics, digest/size validation, Postgres metadata-only behavior, and `ArtifactRef` payload boundaries in `tests/unit/workflows/temporal/test_artifacts.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004, DOC-REQ-008, DOC-REQ-015).
- [X] T014 [P] [US1] Add router-level API tests for create/direct-upload/multipart-presign/complete/get/list/link contract behavior in `tests/unit/api/routers/test_temporal_artifacts.py` (DOC-REQ-011, DOC-REQ-014).
- [X] T015 [P] [US1] Add end-to-end local MinIO artifact flow integration coverage in `tests/integration/temporal/test_temporal_artifact_local_dev.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-005, DOC-REQ-011).
- [X] T016 [P] [US1] Add OpenAPI contract conformance tests for artifact create/presign/complete/get/list/link surfaces in `tests/contract/test_temporal_artifact_api.py` (DOC-REQ-014).

### Implementation for User Story 1

- [X] T017 [US1] Implement create flow upload-mode selection (single vs multipart) and threshold gating in `api_service/api/routers/temporal_artifacts.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-011, DOC-REQ-014).
- [X] T018 [US1] Implement multipart part-presign and completion handling with integrity checks in `api_service/api/routers/temporal_artifacts.py`, `moonmind/workflows/temporal/artifacts.py`, and `moonmind/schemas/temporal_artifact_models.py` (DOC-REQ-008, DOC-REQ-011, DOC-REQ-014).
- [X] T019 [US1] Enforce immutable artifact updates and stable storage-key construction with `art_<ULID>` references in `moonmind/workflows/temporal/artifacts.py` and `api_service/db/models.py` (DOC-REQ-002, DOC-REQ-008).
- [X] T020 [US1] Implement execution link creation/listing and deterministic latest-output query behavior in `moonmind/workflows/temporal/artifacts.py` and `api_service/api/routers/temporal_artifacts.py` (DOC-REQ-009, DOC-REQ-014).
- [X] T021 [US1] Implement streaming download/read handling while preserving `ArtifactRef`-first workflow activity interfaces in `api_service/api/routers/temporal_artifacts.py`, `moonmind/workflows/temporal/artifacts.py`, and `moonmind/schemas/temporal_artifact_models.py` (DOC-REQ-001, DOC-REQ-011, DOC-REQ-015).

**Checkpoint**: US1 delivers the MVP local/dev artifact runtime.

---

## Phase 4: User Story 2 - Authorization and Preview Safety Match App Mode (Priority: P2)

**Goal**: Enforce mode-aligned authorization and redaction-aware preview behavior for artifact metadata and blob access.

**Independent Test**: Validate `AUTH_PROVIDER=disabled` local behavior, authenticated-mode denials, and preview-vs-raw policy handling.

### Tests for User Story 2

- [X] T022 [P] [US2] Add no-auth-local vs authenticated-mode router tests for principal attribution and access denials in `tests/unit/api/routers/test_temporal_artifact_auth.py` (DOC-REQ-006, DOC-REQ-007).
- [X] T023 [P] [US2] Add service authorization and presign-gating unit tests in `tests/unit/workflows/temporal/test_artifact_authorization.py` (DOC-REQ-007, DOC-REQ-010).
- [X] T024 [P] [US2] Add integration tests for preview generation and restricted raw-access behavior in `tests/integration/temporal/test_temporal_artifact_auth_preview.py` (DOC-REQ-012).

### Implementation for User Story 2

- [X] T025 [US2] Implement default local principal attribution for `AUTH_PROVIDER=disabled` artifact routes in `api_service/api/routers/temporal_artifacts.py` and `api_service/auth.py` (DOC-REQ-006).
- [X] T026 [US2] Implement execution-linked authorization checks for metadata, presign, link, and list operations in `api_service/api/routers/temporal_artifacts.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-007).
- [X] T027 [US2] Implement short-lived scoped presign grants with auditable operation metadata in `moonmind/workflows/temporal/artifacts.py` and `moonmind/schemas/temporal_artifact_models.py` (DOC-REQ-010).
- [X] T028 [US2] Implement redaction-aware preview artifact generation and restricted raw fallback policy in `moonmind/workflows/temporal/artifacts.py` and `api_service/api/routers/temporal_artifacts.py` (DOC-REQ-012, DOC-REQ-015).
- [X] T029 [US2] Expose preview/raw-access policy metadata and responses for UI-safe reads in `moonmind/schemas/temporal_artifact_models.py` and `api_service/api/routers/temporal_artifacts.py` (DOC-REQ-012, DOC-REQ-014).

**Checkpoint**: US2 delivers consistent auth and safe-preview behavior across app modes.

---

## Phase 5: User Story 3 - Retention and Lifecycle Keep Storage Predictable (Priority: P3)

**Goal**: Implement retention classes and idempotent lifecycle cleanup while preserving execution traceability.

**Independent Test**: Seed artifacts by retention class, run repeated cleanup sweeps, and verify deterministic latest-output plus idempotent delete transitions.

### Tests for User Story 3

- [X] T030 [P] [US3] Add unit tests for retention mapping, pin/unpin transitions, and soft-delete idempotency in `tests/unit/workflows/temporal/test_artifact_lifecycle.py` (DOC-REQ-013).
- [X] T031 [P] [US3] Add integration tests for repeated lifecycle sweep behavior, pinned exemptions, and tombstone handling in `tests/integration/temporal/test_temporal_artifact_lifecycle.py` (DOC-REQ-013).
- [X] T032 [P] [US3] Add API contract tests for pin/unpin/delete and deterministic latest-output query semantics in `tests/contract/test_temporal_artifact_lifecycle_api.py` (DOC-REQ-009, DOC-REQ-014).

### Implementation for User Story 3

- [X] T033 [US3] Implement retention-class defaults and link-type retention mapping for create/link flows in `moonmind/workflows/temporal/artifacts.py` and `moonmind/schemas/temporal_artifact_models.py` (DOC-REQ-013).
- [X] T034 [US3] Implement idempotent lifecycle sweep logic (soft-delete then hard-delete/tombstone) as artifact service/activity methods in `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/__init__.py` (DOC-REQ-013, DOC-REQ-015).
- [X] T035 [US3] Persist lifecycle/tombstone metadata and cleanup indexes in `api_service/db/models.py` and `api_service/migrations/versions/202603050001_temporal_artifact_system.py` (DOC-REQ-004, DOC-REQ-013).
- [X] T036 [US3] Align pin/unpin/delete endpoint behavior with lifecycle policy and idempotent semantics in `api_service/api/routers/temporal_artifacts.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-013, DOC-REQ-014).
- [X] T037 [US3] Finalize deterministic latest-output-by-link query contract behavior in `moonmind/workflows/temporal/artifacts.py` and `api_service/api/routers/temporal_artifacts.py` (DOC-REQ-009).

**Checkpoint**: US3 delivers predictable retention and lifecycle operations.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize traceability, run repository-standard validation, and enforce runtime gates.

- [X] T038 [P] Sync artifact API and traceability docs to final runtime behavior in `specs/058-temporal-artifact-local-dev/contracts/temporal-artifacts.openapi.yaml` and `specs/058-temporal-artifact-local-dev/contracts/requirements-traceability.md` (DOC-REQ-014, DOC-REQ-015).
- [X] T039 Run artifact-focused unit/contract/integration regression through `tools/test_unit.sh` covering `tests/unit/workflows/temporal/`, `tests/unit/api/routers/`, `tests/contract/`, and `tests/integration/temporal/` (DOC-REQ-001 through DOC-REQ-016 validation sweep).
- [X] T040 Run full repository unit validation via `tools/test_unit.sh` and resolve runtime regressions in `tests/unit/` and `tests/integration/` paths (DOC-REQ-015).
- [X] T041 Execute runtime scope gates with `.specify/scripts/bash/validate-implementation-scope.sh` and validate both `--check tasks --mode runtime` and `--check diff --base-ref origin/main --mode runtime` (DOC-REQ-015).
- [X] T042 [P] Record final runtime verification steps and expected outcomes in `specs/058-temporal-artifact-local-dev/quickstart.md` (DOC-REQ-015).
- [X] T043 [P] Finalize DOC-REQ evidence links and implementation/test mapping in `specs/058-temporal-artifact-local-dev/contracts/requirements-traceability.md` (DOC-REQ-001 through DOC-REQ-016).
- [X] T044 Implement catalog-derived routing for execution-path `artifact.*` activity calls so `artifact.read` is consumed on the artifact queue in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-016).
- [X] T045 Add regression tests that fail when `artifact.read` is routed to non-artifact queues in `tests/unit/workflows/temporal/workflows/test_run.py` and `tests/unit/workflows/temporal/test_run_artifacts.py` (DOC-REQ-016).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No prerequisites.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all story work.
- **Phase 3 (US1)**: Depends on Phase 2 completion.
- **Phase 4 (US2)**: Depends on Phase 2 completion; can proceed independently after foundations land.
- **Phase 5 (US3)**: Depends on Phase 2 completion; full lifecycle validation benefits from US1/US2 surfaces.
- **Phase 6 (Polish)**: Depends on completion of targeted story phases.

### User Story Dependencies

- **US1 (P1)**: Primary MVP and first runtime slice after foundational work.
- **US2 (P2)**: Independent auth/preview hardening once artifact APIs exist.
- **US3 (P3)**: Independent lifecycle controls after core storage/API flows exist.

### Within Each User Story

- Add/adjust tests for the story first, verify they fail, then implement.
- Complete data/service behaviors before endpoint integration polish.
- Re-run story-specific tests before moving to the next story.

### Parallel Opportunities

- Setup tasks `T003` and `T004` can run in parallel after `T001-T002`.
- Foundational tasks `T007` and `T012` can run in parallel with data/service work once schema direction is fixed.
- US1 test tasks `T013-T016` can run in parallel.
- US2 test tasks `T022-T024` can run in parallel.
- US3 test tasks `T030-T032` can run in parallel.
- Polish doc tasks `T042-T043` can run in parallel after validation tasks complete.

---

## Parallel Example: User Story 1

```bash
# Execute US1 validation tracks concurrently:
Task T013: tests/unit/workflows/temporal/test_artifacts.py
Task T014: tests/unit/api/routers/test_temporal_artifacts.py
Task T015: tests/integration/temporal/test_temporal_artifact_local_dev.py
Task T016: tests/contract/test_temporal_artifact_api.py
```

## Parallel Example: User Story 2

```bash
# Execute US2 validation tracks concurrently:
Task T022: tests/unit/api/routers/test_temporal_artifact_auth.py
Task T023: tests/unit/workflows/temporal/test_artifact_authorization.py
Task T024: tests/integration/temporal/test_temporal_artifact_auth_preview.py
```

## Parallel Example: User Story 3

```bash
# Execute US3 validation tracks concurrently:
Task T030: tests/unit/workflows/temporal/test_artifact_lifecycle.py
Task T031: tests/integration/temporal/test_temporal_artifact_lifecycle.py
Task T032: tests/contract/test_temporal_artifact_lifecycle_api.py
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2 foundations.
2. Deliver Phase 3 (US1) create/upload/complete/read/list/link flows.
3. Validate US1 independently with `T013-T016`.
4. Demo/deploy local-dev MVP behavior.

### Incremental Delivery

1. Foundation (Phases 1-2) establishes storage/auth/schema baseline.
2. Add US1 MVP runtime.
3. Add US2 auth + preview safety.
4. Add US3 retention + lifecycle cleanup.
5. Finish with Phase 6 validation and scope gates.

### Parallel Team Strategy

1. Collaborate on Phase 1-2 foundations.
2. Split by story once foundations are stable:
   - Engineer A: US1 runtime + tests
   - Engineer B: US2 auth/preview + tests
   - Engineer C: US3 lifecycle + tests
3. Rejoin for cross-cutting regression and gates.

---

## Quality Gates

1. Runtime tasks gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime diff gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`
3. Unit/integration gate: `./tools/test_unit.sh`
4. Traceability gate: each `DOC-REQ-*` has at least one implementation task and one validation task.
5. Prompt B runtime gate: runtime implementation + validation task coverage must remain explicit and deterministic across `spec.md`, `plan.md`, and `tasks.md`.

## Task Summary

- Total tasks: **45**
- Story task count: **US1 = 9**, **US2 = 8**, **US3 = 8**
- Parallelizable tasks (`[P]`): **17**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **all tasks follow `- [ ] T### [P?] [US?] ...` with explicit path references**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T021 | T013, T015 |
| DOC-REQ-002 | T019 | T013 |
| DOC-REQ-003 | T001, T002, T003, T008 | T015 |
| DOC-REQ-004 | T005, T006, T008, T035 | T013, T031 |
| DOC-REQ-005 | T001, T002, T004 | T015 |
| DOC-REQ-006 | T010, T025 | T022 |
| DOC-REQ-007 | T010, T026 | T022, T023 |
| DOC-REQ-008 | T007, T008, T018, T019 | T013, T014 |
| DOC-REQ-009 | T005, T009, T020, T037 | T032 |
| DOC-REQ-010 | T003, T011, T027 | T023 |
| DOC-REQ-011 | T002, T003, T008, T017, T018, T021 | T014, T015 |
| DOC-REQ-012 | T028, T029 | T024 |
| DOC-REQ-013 | T005, T006, T033, T034, T035, T036 | T030, T031 |
| DOC-REQ-014 | T007, T017, T018, T020, T029, T036 | T014, T016, T032 |
| DOC-REQ-015 | T012, T021, T028, T034 | T013, T039, T040, T041 |
| DOC-REQ-016 | T044 | T045 |

Coverage rule: do not close implementation until every `DOC-REQ-*` row keeps both implementation and validation coverage.
