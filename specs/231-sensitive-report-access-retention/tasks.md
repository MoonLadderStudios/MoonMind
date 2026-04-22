# Tasks: Sensitive Report Access and Retention

**Input**: `specs/231-sensitive-report-access-retention/spec.md`  
**Plan**: `specs/231-sensitive-report-access-retention/plan.md`  
**Unit Test Command**: `./tools/test_unit.sh`  
**Focused Unit Test Command**: `./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifact_authorization.py`  
**Focused Integration Test Command**: `pytest tests/integration/temporal/test_temporal_artifact_lifecycle.py -m integration_ci -q --tb=short`  
**Integration Test Command**: `./tools/test_integration.sh`

## Source Traceability

- Jira: MM-463
- Story: Preserve sensitive report access and retention.
- Story count: exactly one independently testable story from `spec.md`.
- Independent test: create sensitive report artifacts and validate preview/default-read behavior, report-aware retention defaults, pin/unpin restoration, and no deletion cascade to observability artifacts.
- Requirements: FR-001 through FR-010; SC-001 through SC-006.
- Source design coverage: DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022.
- Requirement statuses from plan: FR-004 and FR-005 are missing; FR-007 is partial; FR-001, FR-002, FR-003, FR-006, FR-008, FR-009, FR-010, DESIGN-REQ-015, and DESIGN-REQ-022 are implemented_unverified; DESIGN-REQ-016 is partial.

## Phase 1: Setup

- [X] T001 Confirm active feature context points to `specs/231-sensitive-report-access-retention` in `.specify/feature.json` (MM-463).
- [X] T002 Inspect existing artifact service retention, preview/default-read, pin/unpin, and deletion behavior in `moonmind/workflows/temporal/artifacts.py` (FR-001 through FR-009).
- [X] T003 Inspect existing report contract helpers in `moonmind/workflows/temporal/report_artifacts.py` and artifact tests in `tests/unit/workflows/temporal/test_artifacts.py`, `tests/unit/workflows/temporal/test_artifact_authorization.py`, and `tests/integration/temporal/test_temporal_artifact_lifecycle.py` (DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022).

## Phase 2: Foundational Tests

Unit test plan: focused artifact service tests cover report retention defaults, pin/unpin restoration, restricted raw denial, and preview/default-read policy.

Integration test plan: a compose-compatible lifecycle regression covers report deletion with unrelated observability artifacts linked to the same execution.

- [X] T004 [P] Add failing unit test in `tests/unit/workflows/temporal/test_artifacts.py` proving `report.primary` and `report.summary` default to `long` retention without explicit override (FR-004, FR-005, SC-002, DESIGN-REQ-016).
- [X] T005 [P] Add unit regression in `tests/unit/workflows/temporal/test_artifacts.py` proving `report.structured` and `report.evidence` default to `standard` and explicit `long` retention is honored (FR-006, SC-003, DESIGN-REQ-016).
- [X] T006 [P] Add failing unit test in `tests/unit/workflows/temporal/test_artifacts.py` proving pin then unpin of `report.primary` restores report-derived `long` retention (FR-007, SC-004, DESIGN-REQ-016).
- [X] T007 [P] Add unit test in `tests/unit/workflows/temporal/test_artifact_authorization.py` proving restricted report metadata uses preview `default_read_ref` for a metadata-readable caller without raw access and raw presign remains denied (FR-001, FR-002, FR-003, SC-001, DESIGN-REQ-015).
- [X] T008 [P] Add integration regression in `tests/integration/temporal/test_temporal_artifact_lifecycle.py` proving deleting a report artifact does not delete unrelated runtime observability artifacts for the same execution (FR-008, FR-009, SC-005, DESIGN-REQ-022).
- [X] T009 Run focused tests for T004-T008 and capture red-first evidence before production changes. Red-first evidence: `pytest tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifact_authorization.py tests/integration/temporal/test_temporal_artifact_lifecycle.py -q --tb=short` failed before production changes with `report.primary`/`report.summary` retaining `standard` instead of `long`.

## Phase 3: Implementation

- [X] T010 Update `_derive_retention` in `moonmind/workflows/temporal/artifacts.py` so `report.primary` and `report.summary` default to `long`, while `report.structured` and `report.evidence` default to `standard` unless explicitly overridden (FR-004, FR-005, FR-006).
- [X] T011 Update `TemporalArtifactService.unpin` in `moonmind/workflows/temporal/artifacts.py` to restore retention from existing artifact link types when a pinned report artifact is unpinned (FR-007).
- [X] T012 Keep deletion code artifact-native in `moonmind/workflows/temporal/artifacts.py`; only change deletion code if T008 exposes a report-specific cascade defect (FR-008, FR-009).

## Phase 4: Validation

- [X] T013 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifact_authorization.py` and fix failures (FR-001 through FR-007). Evidence: 46 Python tests passed; the unit runner also completed 367 frontend tests.
- [X] T014 Run `pytest tests/integration/temporal/test_temporal_artifact_lifecycle.py -m integration_ci -q --tb=short` and fix failures, or run `./tools/test_integration.sh` when Docker Compose is required and available (FR-008, FR-009). Evidence: included in focused pytest command; final post-fix result was 48 passed.
- [X] T015 Run traceability check `rg -n "MM-463|DESIGN-REQ-015|DESIGN-REQ-016|DESIGN-REQ-022" specs/231-sensitive-report-access-retention docs/tmp/jira-orchestration-inputs/MM-463-moonspec-orchestration-input.md` (FR-010, SC-006).
- [X] T016 Run final `./tools/test_unit.sh` unless blocked by environment constraints. Evidence: 3,766 Python tests passed, 1 xpassed, 16 subtests passed; 367 frontend tests passed.

## Phase 5: Verify

- [X] T017 Run `/speckit.verify` equivalent through `moonspec-verify` for `specs/231-sensitive-report-access-retention/spec.md` and record verdict in `specs/231-sensitive-report-access-retention/verification.md`.
- [X] T018 Mark completed tasks `[X]` only after implementation and verification evidence exists.

## Dependencies And Order

1. Setup tasks T001-T003.
2. Failing tests T004-T009.
3. Implementation T010-T012.
4. Focused and final validation T013-T016.
5. Final verification T017-T018.

## Parallel Work

- T004-T008 can be authored in parallel because they touch different test cases.
- T010 and T011 can be implemented after red-first evidence exists.

## Implementation Strategy

Start with service-boundary tests. Keep authorization, preview, pin, and deletion APIs unchanged unless tests expose a defect. The expected production change is limited to retention derivation and unpin restoration; deletion should remain artifact-native.
