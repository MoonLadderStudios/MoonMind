# Tasks: Mission Control Report Presentation

**Input**: `specs/229-mission-control-report-presentation/spec.md`
**Plan**: `specs/229-mission-control-report-presentation/plan.md`
**Unit Test Command**: `./tools/test_unit.sh`
**Focused UI Test Command**: `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-detail.test.tsx`
**Focused API Contract Command**: `./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py`
**Integration Test Command**: `./tools/test_integration.sh` when backend artifact behavior changes beyond the existing read-only query/serialization contract

## Source Traceability

- Jira: MM-462
- Story: Present canonical reports in Mission Control task detail surfaces.
- Story count: exactly one independently testable story from `spec.md`.
- Independent test: load an execution detail surface with report artifacts, verify report-first presentation, related content openability, presentation-field viewer selection, and no fabricated report panel when `report.primary` is absent.
- Requirements: FR-001 through FR-008; SC-001 through SC-006.
- Source design coverage: DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020, DESIGN-REQ-022.
- Requirement statuses from plan: FR-002, FR-007, DESIGN-REQ-014 are missing; FR-003, FR-005, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-020 are partial; FR-001, FR-004, FR-006, FR-008, DESIGN-REQ-011, DESIGN-REQ-022 are implemented_unverified.

## Phase 1: Setup

- [X] T001 Confirm active feature context points to `specs/229-mission-control-report-presentation` in `.specify/feature.json` (MM-462).
- [X] T002 Inspect existing artifact list parsing and rendering in `frontend/src/entrypoints/task-detail.tsx` before editing (FR-001 through FR-007).
- [X] T003 Inspect existing task detail and artifact API tests in `frontend/src/entrypoints/task-detail.test.tsx` and `tests/contract/test_temporal_artifact_api.py` before adding failures (FR-001 through FR-007).

## Phase 2: Foundational Tests

Unit test plan: frontend unit tests cover report-first UI, related report content, fallback behavior, and viewer/open-target selection.
Integration/contract test plan: API contract coverage verifies the execution artifact boundary accepts `link_type=report.primary&latest_only=true` and preserves links/default read refs; `./tools/test_integration.sh` is reserved for backend behavior changes beyond this existing read-only contract.

- [X] T004 [P] Add failing frontend unit test in `frontend/src/entrypoints/task-detail.test.tsx` asserting the UI queries `/artifacts?link_type=report.primary&latest_only=true` and renders a Report section before Artifacts when a primary report exists (FR-001, FR-002, FR-007, SC-001, DESIGN-REQ-011, DESIGN-REQ-014).
- [X] T005 [P] Add failing frontend unit test in `frontend/src/entrypoints/task-detail.test.tsx` asserting related `report.summary`, `report.structured`, and `report.evidence` artifacts are displayed as related report content and individually openable (FR-003, SC-002, DESIGN-REQ-012, DESIGN-REQ-020).
- [X] T006 [P] Add failing frontend unit test in `frontend/src/entrypoints/task-detail.test.tsx` asserting no report panel/status is fabricated when latest `report.primary` returns empty but generic artifacts exist (FR-004, FR-007, SC-003).
- [X] T007 [P] Add failing frontend unit test in `frontend/src/entrypoints/task-detail.test.tsx` asserting report open targets use `default_read_ref` and viewer labels reflect `render_hint` or `content_type` for markdown, JSON, text, diff, image, and binary/PDF cases (FR-005, SC-004, DESIGN-REQ-013).
- [X] T008 [P] Add failing API contract regression in `tests/contract/test_temporal_artifact_api.py` asserting the execution artifact list can be called with `link_type=report.primary&latest_only=true` and returns links/default read refs needed by Mission Control (FR-001, FR-006, DESIGN-REQ-011, DESIGN-REQ-022).
- [X] T009 Run focused failing tests for T004-T008 and capture red-first evidence in `specs/229-mission-control-report-presentation/tasks.md` or verification notes.

## Phase 3: Implementation

- [X] T010 Extend `ArtifactSummarySchema` and artifact normalization in `frontend/src/entrypoints/task-detail.tsx` to preserve `links`, `metadata`, `default_read_ref`, `download_url`, `content_type`, and raw access fields needed for report presentation (FR-003, FR-005).
- [X] T011 Add report link classification, report open-target, and viewer-label helpers in `frontend/src/entrypoints/task-detail.tsx` (FR-003, FR-005, FR-007, DESIGN-REQ-013).
- [X] T012 Add latest primary report query in `frontend/src/entrypoints/task-detail.tsx` using `link_type=report.primary&latest_only=true` and include it in invalidation/live polling (FR-001, FR-007, DESIGN-REQ-011).
- [X] T013 Render a report-first section in `frontend/src/entrypoints/task-detail.tsx` before Timeline and Artifacts when a latest primary report exists (FR-002, SC-001, DESIGN-REQ-014).
- [X] T014 Render related report summary, structured, and evidence artifacts in the report section while preserving individual open links and leaving the generic Artifacts table unchanged (FR-003, FR-004, SC-002, DESIGN-REQ-012, DESIGN-REQ-020).
- [X] T015 Preserve fallback behavior in `frontend/src/entrypoints/task-detail.tsx` so empty latest report data shows no report status and generic artifacts still render normally (FR-004, FR-007, SC-003).
- [X] T016 Update `tests/contract/test_temporal_artifact_api.py` test doubles only as needed to verify existing latest-report query serialization without introducing a new backend storage path (FR-001, FR-006, DESIGN-REQ-022).

## Phase 4: Validation

- [X] T017 Run `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-detail.test.tsx` and fix failures (FR-001 through FR-007).
- [X] T018 Run `./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py` and fix failures (FR-001, FR-006).
- [X] T019 Run traceability check `rg -n "MM-462|DESIGN-REQ-011|DESIGN-REQ-012|DESIGN-REQ-013|DESIGN-REQ-014|DESIGN-REQ-020|DESIGN-REQ-022" specs/229-mission-control-report-presentation docs/tmp/jira-orchestration-inputs/MM-462-moonspec-orchestration-input.md` (FR-008, SC-006).
- [X] T020 Run final `./tools/test_unit.sh` unless blocked by environment constraints.

## Phase 5: Verify

- [X] T021 Run `/speckit.verify` equivalent through `moonspec-verify` for `specs/229-mission-control-report-presentation/spec.md` and record verdict in `specs/229-mission-control-report-presentation/verification.md`.
- [X] T022 Mark completed tasks `[X]` only after implementation and verification evidence exists.

## Dependencies And Order

1. Setup tasks T001-T003.
2. Failing tests T004-T009.
3. Implementation T010-T016.
4. Focused and final validation T017-T020.
5. Final verification T021-T022.

## Parallel Work

- T004-T008 can be authored in parallel because they cover different test cases.
- T010-T011 can be implemented before T012-T015 once failing tests exist.

## Implementation Strategy

Start with UI and API contract tests. Keep backend behavior unchanged unless the contract regression reveals that the existing endpoint does not expose required fields. The report UI must be additive: it may render a new Report section, but it must not remove or reorder the generic observability surfaces except placing the Report section before generic artifact inspection.
