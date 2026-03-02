# Tasks: Manifest Phase 0 Rebaseline

**Input**: Design artifacts in `/specs/031-manifest-phase0/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Runtime validation is required by the feature spec (`DOC-REQ-009`); run `./tools/test_unit.sh` (never raw `pytest`) and runtime scope gates for final verification.

**Organization**: Tasks are grouped by user story to preserve independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]** indicates tasks that can run in parallel once dependencies are met.
- **[Story]** labels are used only in user-story phases (`[US1]`, `[US2]`, `[US3]`).
- Every task includes concrete file paths.

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present: `T004-T007`, `T010-T012`, `T015-T017`, `T020-T021`.
- Runtime validation tasks are explicitly present: `T008-T009`, `T013-T014`, `T018-T019`, `T023-T025`.
- `DOC-REQ-*` implementation + validation coverage is enforced by `T003` and the `DOC-REQ Coverage Matrix` in this file, with persistent mapping in `specs/031-manifest-phase0/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare shared fixtures/configuration and rebaseline metadata needed by all stories.

- [X] T001 [P] Refresh manifest scenario fixtures in `tests/fixtures/manifests/phase0/inline.yaml` and `tests/fixtures/manifests/phase0/registry.yaml` for supported + unsupported Phase 0 payloads.
- [X] T002 [P] Update Phase 0 runtime toggle defaults in `moonmind/config/settings.py` and `config.toml` for `allow_manifest_path_source` and base `manifest_required_capabilities` behavior.
- [X] T003 Capture updated requirement mappings in `specs/031-manifest-phase0/contracts/requirements-traceability.md` so each `DOC-REQ-*` has implementation and validation targets before coding.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish queue/contract primitives that every user story depends on.

**⚠️ CRITICAL**: Complete this phase before user-story work.

- [X] T004 Register and enforce the `manifest` queue type allowlist in `moonmind/workflows/agent_queue/job_types.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-009).
- [X] T005 Implement deterministic manifest normalization and fail-fast validation (`action`, `version`, source kinds, options) in `moonmind/workflows/agent_queue/manifest_contract.py` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-007, DOC-REQ-009).
- [X] T006 Enforce server-authoritative capability derivation and ignore client capability hints in `moonmind/workflows/agent_queue/manifest_contract.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-003, FR-009, DOC-REQ-009).
- [X] T007 Implement raw-secret rejection plus reference-only metadata extraction in `moonmind/workflows/agent_queue/manifest_contract.py` and sanitized payload projection in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-004, DOC-REQ-008, DOC-REQ-009).

**Checkpoint**: Foundational manifest primitives are ready for independent story delivery.

---

## Phase 3: User Story 1 - Rebaseline Manifest Runtime Contract (Priority: P1) 🎯 MVP

**Goal**: Make queue manifest submission/listing behavior match the updated runtime contract with deterministic normalization, metadata derivation, and token-safe responses.

**Independent Test**: Submit `type="manifest"` jobs through `/api/queue/jobs`, then verify persisted + returned payloads include required metadata (`manifestHash`, `manifestVersion`, `requiredCapabilities`, optional `manifestSecretRefs`) while rejecting invalid or unsafe payloads.

### Tests for User Story 1

- [X] T008 [P] [US1] Extend contract coverage in `tests/unit/workflows/agent_queue/test_manifest_contract.py` for normalization success/failure, unsupported source/action rejection, capability derivation, and raw secret blocking (DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008).
- [X] T009 [P] [US1] Extend queue API coverage in `tests/unit/api/routers/test_agent_queue.py` for manifest submission/listing sanitization and actionable 4xx error mapping (DOC-REQ-001, DOC-REQ-002, DOC-REQ-004, DOC-REQ-008).

### Implementation for User Story 1

- [X] T010 [US1] Route manifest submissions through the normalized contract and persist derived metadata in `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/models.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-009).
- [X] T011 [P] [US1] Ensure queue response serialization hides raw manifest content while surfacing safe metadata in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-004, DOC-REQ-008, DOC-REQ-009).
- [X] T012 [P] [US1] Update manifest queue request/response validation and canonical error payloads in `api_service/api/routers/agent_queue.py` (DOC-REQ-002, DOC-REQ-007, DOC-REQ-009).

**Checkpoint**: US1 delivers a valid, testable Phase 0 manifest queue contract.

---

## Phase 4: User Story 2 - Align Registry + Queue Flows With Current Strategy (Priority: P2)

**Goal**: Align manifest registry CRUD and registry-backed run submission with canonical MoonMind naming, queue contract semantics, and Phase 0 boundaries.

**Independent Test**: Upsert/read manifests and submit `/api/manifests/{name}/runs`; verify queue linkage metadata, fail-fast validation on unsupported inputs, and token-safe outputs.

### Tests for User Story 2

- [X] T013 [P] [US2] Expand service-level coverage in `tests/unit/services/test_manifests_service.py` for registry upsert/run behavior, hash/version updates, queue-job linkage, and secret-safe enforcement (DOC-REQ-004, DOC-REQ-005, DOC-REQ-008).
- [X] T014 [P] [US2] Expand router coverage in `tests/unit/api/routers/test_manifests.py` for list/get/put/post flows, canonical naming, not-found handling, and unsupported-action/source failures (DOC-REQ-005, DOC-REQ-006, DOC-REQ-007).

### Implementation for User Story 2

- [X] T015 [US2] Update registry run orchestration in `api_service/services/manifests_service.py` to reuse manifest normalization, enforce Phase 0 constraints, and persist run-link metadata (`last_run_*`, state) (DOC-REQ-002, DOC-REQ-005, DOC-REQ-009).
- [X] T016 [P] [US2] Update API contract and canonical response fields in `api_service/api/routers/manifests.py` and `api_service/api/schemas.py` (FR-008, DOC-REQ-005, DOC-REQ-008, DOC-REQ-009).
- [X] T017 [P] [US2] Align registry persistence fields in `api_service/db/models.py` and `api_service/migrations/versions/202602190003_manifest_registry_extensions.py` (DOC-REQ-005, DOC-REQ-009).

**Checkpoint**: US2 delivers strategy-aligned registry + queue interoperability.

---

## Phase 5: User Story 3 - Lock Behavior With Regression Tests (Priority: P3)

**Goal**: Add regression tests that prevent silent reintroduction of stale manifest behavior across queue, registry, and claim-routing paths.

**Independent Test**: Run targeted manifest suites and then `./tools/test_unit.sh`; verify intentional rule breaks produce deterministic test failures.

### Tests for User Story 3

- [X] T018 [P] [US3] Extend claim-eligibility regression coverage in `tests/unit/workflows/agent_queue/test_repositories.py` for manifest capability superset enforcement (DOC-REQ-001, DOC-REQ-003).
- [X] T019 [P] [US3] Add cross-flow regression cases in `tests/unit/api/routers/test_agent_queue.py` and `tests/unit/api/routers/test_manifests.py` that assert fail-fast behavior for unsupported actions/source kinds and raw secrets (DOC-REQ-004, DOC-REQ-006, DOC-REQ-007).

### Implementation for User Story 3

- [X] T020 [US3] Harden manifest claim + metadata consistency logic in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` to satisfy new regression assertions (DOC-REQ-001, DOC-REQ-003, DOC-REQ-008, DOC-REQ-009).
- [X] T021 [P] [US3] Close any regression gaps in validation/sanitization behavior in `moonmind/workflows/agent_queue/manifest_contract.py` and `api_service/services/manifests_service.py` surfaced by T018-T019 (DOC-REQ-002, DOC-REQ-004, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009).

**Checkpoint**: US3 locks the rebaseline with enforceable regression protection.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T022 [P] Refresh executable validation guidance in `specs/031-manifest-phase0/quickstart.md` and keep requirement/test evidence synchronized in `specs/031-manifest-phase0/contracts/requirements-traceability.md` (FR-011).
- [X] T023 Execute `./tools/test_unit.sh` to validate full runtime and regression coverage for this feature (FR-010, DOC-REQ-009).
- [X] T024 [P] Run runtime task-scope validation via `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and capture output in `specs/031-manifest-phase0/quickstart.md` (DOC-REQ-009).
- [X] T025 [P] Run runtime diff-scope validation via `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` and capture output in `specs/031-manifest-phase0/quickstart.md` (DOC-REQ-009).

---

## Dependencies & Execution Order

### Phase Dependencies

1. Phase 1 (Setup) starts immediately.
2. Phase 2 (Foundational) depends on Phase 1 and blocks all user stories.
3. Phase 3 (US1) depends on Phase 2 and is the MVP increment.
4. Phase 4 (US2) depends on Phase 2; sequence after US1 for safer integration.
5. Phase 5 (US3) depends on Phase 3 and Phase 4 because it locks behavior across both flows.
6. Phase 6 (Polish) runs after all user stories are complete.

### User Story Dependency Graph

1. `US1 (P1)` -> baseline manifest runtime contract.
2. `US2 (P2)` -> registry and queue strategy alignment on top of the contract.
3. `US3 (P3)` -> regression lock across US1 + US2 behavior.

### Parallel Opportunities

- Phase 1: `T001` and `T002` can run in parallel.
- US1: `T008` and `T009` can run in parallel; after `T010`, `T011` and `T012` can run in parallel.
- US2: `T013` and `T014` can run in parallel; after `T015`, `T016` and `T017` can run in parallel.
- US3: `T018` and `T019` can run in parallel; `T021` can run in parallel with finalizing non-overlapping fixes from `T020`.
- Phase 6: `T024` and `T025` can run in parallel after `T023`.

---

## Parallel Example: User Story 2

```bash
# Parallel test prep for US2:
Task: "Expand service-level coverage in tests/unit/services/test_manifests_service.py"
Task: "Expand router coverage in tests/unit/api/routers/test_manifests.py"

# Parallel implementation after service orchestration lands:
Task: "Update API contract fields in api_service/api/routers/manifests.py + api_service/api/schemas.py"
Task: "Align registry persistence fields in api_service/db/models.py + migration"
```

---

## Implementation Strategy

### MVP First (US1)

1. Complete Phase 1 and Phase 2.
2. Deliver US1 (Phase 3) and validate queue contract independently.
3. Treat this checkpoint as the minimum shippable rebaseline.

### Incremental Delivery

1. Add US2 to align registry and queue behavior with current strategy.
2. Add US3 to lock behavior with regression tests.
3. Finish with Phase 6 validation and traceability refresh.

### Quality Gates

1. Runtime scope gate (tasks): `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime scope gate (diff): `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`
3. Unit suite gate: `./tools/test_unit.sh`
4. Traceability gate: each `DOC-REQ-001` through `DOC-REQ-009` remains represented by at least one implementation task and one validation task.

## Task Summary

- Total tasks: **25**
- User story tasks: **US1 = 5**, **US2 = 5**, **US3 = 4**
- Parallelizable tasks (`[P]`): **14**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **All tasks follow `- [X] T### [P?] [US?] ...` with explicit paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T004, T010, T020 | T009, T018, T023 |
| DOC-REQ-002 | T005, T010, T012, T015, T021 | T008, T009, T013, T023 |
| DOC-REQ-003 | T006, T010, T020 | T008, T009, T018, T023 |
| DOC-REQ-004 | T007, T011, T012, T015, T021 | T008, T009, T013, T019, T023 |
| DOC-REQ-005 | T015, T016, T017 | T013, T014, T019, T023 |
| DOC-REQ-006 | T005, T012, T015, T021 | T008, T014, T019, T023 |
| DOC-REQ-007 | T005, T012, T015, T021 | T008, T014, T019, T023 |
| DOC-REQ-008 | T007, T011, T015, T016, T020, T021 | T008, T009, T013, T019, T023 |
| DOC-REQ-009 | T002, T004, T005, T006, T007, T010, T011, T012, T015, T016, T017, T020, T021 | T023, T024, T025 |

Coverage gate rule: each `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.
