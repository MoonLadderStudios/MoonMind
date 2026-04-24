# Tasks: Apply Report Access and Lifecycle Policy

**Input**: `specs/231-sensitive-report-access-retention/spec.md`  
**Plan**: `specs/231-sensitive-report-access-retention/plan.md`  
**Unit Test Command**: `./tools/test_unit.sh`  
**Focused Unit Test Command**: `./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifact_authorization.py`  
**Focused Integration Test Command**: `pytest tests/integration/temporal/test_temporal_artifact_lifecycle.py -m integration_ci -q --tb=short`  
**Integration Test Command**: `./tools/test_integration.sh`

## Source Traceability

- Jira: MM-495
- Story: Apply report access and lifecycle policy.
- Story count: exactly one independently testable story from `spec.md`.
- Independent test: create sensitive report artifacts and validate preview/default-read behavior, bounded report metadata, report-aware retention defaults, pin/unpin restoration, and no deletion cascade to observability artifacts.
- Requirements: FR-001 through FR-010; SC-001 through SC-007.
- Source design coverage: DESIGN-REQ-011, DESIGN-REQ-017, DESIGN-REQ-018.
- Resume note: Existing implementation and tests were already complete; this pass updated the MoonSpec artifacts so the canonical source input and downstream traceability preserve MM-495.

## Phase 1: Resume And Context

- [X] T001 Confirm the canonical Jira brief is `docs/tmp/jira-orchestration-inputs/MM-495-moonspec-orchestration-input.md` and the active feature directory is `specs/231-sensitive-report-access-retention`.
- [X] T002 Inspect existing artifact service behavior in `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/report_artifacts.py` for authorization, metadata validation, retention, pin/unpin, and deletion boundaries.
- [X] T003 Inspect existing evidence in `tests/unit/workflows/temporal/test_artifacts.py`, `tests/unit/workflows/temporal/test_artifact_authorization.py`, and `tests/integration/temporal/test_temporal_artifact_lifecycle.py`.

## Phase 2: Existing Validation Evidence

Unit test evidence covers report metadata validation, restricted preview/default-read behavior, retention defaults, and unpin restoration.
Integration evidence covers report deletion without cascade to observability artifacts.

- [X] T004 Confirm unit coverage for bounded and safe report metadata validation (FR-003, SC-002, DESIGN-REQ-011).
- [X] T005 Confirm unit coverage for restricted report metadata/default-read behavior and raw presign denial (FR-001, FR-002, FR-004, SC-001, DESIGN-REQ-017).
- [X] T006 Confirm unit coverage for `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, and `report.export` long retention plus `report.structured`/`report.evidence` non-observability retention defaults (FR-005, FR-006, SC-003, SC-004, DESIGN-REQ-018).
- [X] T007 Confirm unit coverage for pin then unpin restoring report-derived retention (FR-007, SC-005, DESIGN-REQ-018).
- [X] T008 Confirm integration coverage for deleting a report artifact without mutating unrelated observability artifacts (FR-008, FR-009, SC-006, DESIGN-REQ-018).

## Phase 3: Artifact Alignment

- [X] T009 Update `spec.md` to use the MM-495 Jira preset brief as the canonical input, preserve runtime mode, classify the request correctly, and record resume-from-existing-artifacts behavior.
- [X] T010 Update `plan.md`, `research.md`, `contracts/`, `quickstart.md`, and the requirements checklist so downstream artifacts map to MM-495 and DESIGN-REQ-011/017/018.
- [X] T011 Update `tasks.md` and `verification.md` so traceability, requirement mapping, and final evidence preserve MM-495 instead of MM-463.
- [X] T012 Update `.specify/feature.json` to point the active feature context at `specs/231-sensitive-report-access-retention` for this resumed story.

## Phase 4: Validation

- [X] T013 Run traceability check `rg -n "MM-495|DESIGN-REQ-011|DESIGN-REQ-017|DESIGN-REQ-018" specs/231-sensitive-report-access-retention docs/tmp/jira-orchestration-inputs/MM-495-moonspec-orchestration-input.md` and confirm the updated artifact set preserves the new Jira key and source design IDs.
- [X] T014 Reuse existing focused unit and integration evidence because production code did not change during this alignment pass; record that decision in `verification.md`.

## Phase 5: Verify

- [X] T015 Update the final verification report for `specs/231-sensitive-report-access-retention/spec.md` so the verdict and evidence reference MM-495.
- [X] T016 Keep all completed tasks marked `[X]` only where evidence exists in tests or aligned artifacts.

## Dependencies And Order

1. Resume and inspect the existing feature artifacts and evidence.
2. Confirm the implementation already satisfies MM-495 runtime behavior.
3. Realign spec/plan/tasks/verification artifacts to MM-495.
4. Run the updated traceability check.
5. Record the final verification state.

## Implementation Strategy

Do not regenerate completed implementation work. Reuse the existing `specs/231-sensitive-report-access-retention` feature directory, preserve the validated code and test evidence, and realign the MoonSpec artifacts so MM-495 is the canonical input for this story.
