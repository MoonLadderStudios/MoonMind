# Tasks: Report Artifact Contract

**Input**: Design documents from `/specs/226-report-artifact-contract/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/report-artifact-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration-style artifact service tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: This task list covers exactly one user story: `Publish Report Artifacts`.

**Source Traceability**: Original Jira issue `MM-460` and the original Jira preset brief are preserved in `spec.md` and `docs/tmp/jira-orchestration-inputs/MM-460-moonspec-orchestration-input.md`.

**Test Commands**:

- Unit and artifact service tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py`
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Optional hermetic integration verification: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when the task touches different files and does not depend on incomplete tasks.
- Include exact file paths in task descriptions.
- Include requirement, scenario, success criterion, or source IDs when the task implements or validates behavior.

## Path Conventions

- Backend artifact code lives under `moonmind/workflows/temporal/`.
- Unit and service integration-style tests live under `tests/unit/workflows/temporal/`.
- Moon Spec artifacts live under `specs/226-report-artifact-contract/`.

---

## Phase 1: Setup

**Purpose**: Confirm the active feature context and artifact service surfaces.

- [X] T001 Confirm `.specify/feature.json` points at `specs/226-report-artifact-contract` and preserve MM-460 traceability in `specs/226-report-artifact-contract/spec.md` (FR-010)
- [X] T002 [P] Inspect artifact create/link/list behavior in `moonmind/workflows/temporal/artifacts.py` (FR-001, FR-008, DESIGN-REQ-001)
- [X] T003 [P] Inspect existing artifact service tests in `tests/unit/workflows/temporal/test_artifacts.py` (FR-006, DESIGN-REQ-004)
- [X] T004 [P] Inspect source report requirements in `docs/Artifacts/ReportArtifacts.md` (DESIGN-REQ-001 through DESIGN-REQ-005, DESIGN-REQ-009)

---

## Phase 2: Foundational

**Purpose**: Add the focused validation surface before story behavior.

- [X] T005 Create Moon Spec planning artifacts in `specs/226-report-artifact-contract/plan.md`, `research.md`, `data-model.md`, `contracts/report-artifact-contract.md`, and `quickstart.md` (FR-010)

---

## Phase 3: Story - Publish Report Artifacts

**Summary**: As a workflow producer, I want to publish report deliverables using explicit report artifact link types and bounded metadata so consumers can distinguish reports from generic outputs without a separate storage plane.

**Independent Test**: Create artifacts through the existing artifact service using report-specific and generic link types, then verify report link types and bounded metadata are accepted, unsafe report metadata is rejected, latest-report lookup works through existing execution linkage, and generic output links remain accepted.

**Traceability**: FR-001 through FR-010, SC-001 through SC-005, DESIGN-REQ-001 through DESIGN-REQ-005, DESIGN-REQ-009.

**Unit Test Plan**: Cover pure validation for supported report link types, unsupported report link types, allowed report metadata, unknown metadata keys, secret-like metadata, and large inline metadata.

**Integration Test Plan**: Cover real `TemporalArtifactService.create()`, `link_artifact()`, and `list_for_execution(latest_only=True)` behavior using the existing isolated SQLite/local store test fixture.

### Unit Tests

- [X] T006 [P] Add failing pure validation tests for supported and unsupported report link types in `tests/unit/workflows/temporal/test_artifacts.py` covering FR-002, FR-003, and DESIGN-REQ-003
- [X] T007 [P] Add failing pure validation tests for bounded report metadata, unknown keys, secret-like values, and large inline values in `tests/unit/workflows/temporal/test_artifacts.py` covering FR-004, FR-005, and DESIGN-REQ-005

### Integration Tests

- [X] T008 [P] Add failing artifact service test proving `report.primary` create/link succeeds with bounded metadata in `tests/unit/workflows/temporal/test_artifacts.py` covering FR-001, FR-002, and DESIGN-REQ-001
- [X] T009 [P] Add failing artifact service tests proving unsupported `report.*` link types and unsafe report metadata are rejected before storage/linking in `tests/unit/workflows/temporal/test_artifacts.py` covering FR-003, FR-004, FR-005, SC-001, and SC-002
- [X] T010 [P] Add failing artifact service test proving generic `output.primary`, `output.summary`, and `output.agent_result` links remain accepted with generic metadata in `tests/unit/workflows/temporal/test_artifacts.py` covering FR-006 and DESIGN-REQ-004
- [X] T011 [P] Add failing artifact service test proving latest `report.primary` lookup uses existing execution linkage in `tests/unit/workflows/temporal/test_artifacts.py` covering FR-008 and SC-004

### Red-First Confirmation

- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py` and confirm T006 through T011 fail for missing report artifact contract behavior

### Implementation

- [X] T013 Add report link-type constants and metadata validation helpers in `moonmind/workflows/temporal/report_artifacts.py` covering FR-002 through FR-005
- [X] T014 Wire report link and metadata validation into `TemporalArtifactService.create()` in `moonmind/workflows/temporal/artifacts.py` covering FR-001 through FR-005
- [X] T015 Wire report link and metadata validation into `TemporalArtifactService.link_artifact()` in `moonmind/workflows/temporal/artifacts.py` covering FR-003 through FR-005
- [X] T016 Preserve generic output behavior without report metadata requirements in `moonmind/workflows/temporal/artifacts.py` covering FR-006 and DESIGN-REQ-004

### Story Validation

- [X] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py` and fix failures until the story passes independently
- [X] T018 Run `rg -n "MM-460|report.primary|report.findings_index|Report Artifact Contract" specs/226-report-artifact-contract docs/tmp/jira-orchestration-inputs/MM-460-moonspec-orchestration-input.md moonmind/workflows/temporal tests/unit/workflows/temporal/test_artifacts.py` and confirm traceability evidence exists

---

## Phase 4: Polish & Cross-Cutting Concerns

- [X] T019 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification after targeted tests pass
- [X] T020 Run `./tools/test_integration.sh` when Docker is available, or record Docker unavailability in final verification notes
- [X] T021 Run a secret-pattern scan on changed files before final handoff
- [X] T022 Review `git diff -- specs/226-report-artifact-contract docs/tmp/jira-orchestration-inputs/MM-460-moonspec-orchestration-input.md moonmind/workflows/temporal tests/unit/workflows/temporal/test_artifacts.py .specify/feature.json`
- [X] T023 Run `/moonspec-verify` for `specs/226-report-artifact-contract` and preserve verification evidence for MM-460

---

## Dependencies & Execution Order

- Phase 1 and Phase 2 must complete before story implementation.
- T006 through T011 must be written before T012.
- T012 must confirm red-first failures before T013 through T016.
- T013 precedes T014 and T015.
- T017 and T018 validate the completed story before Phase 4.
- T019 through T023 are final verification and handoff tasks.

## Parallel Opportunities

- T002 through T004 can run in parallel.
- T006 through T011 can be authored in parallel because they are focused test additions.
- T014 and T015 can be implemented after T013 and affect separate service paths.

## Implementation Strategy

1. Complete Moon Spec setup and planning.
2. Add red-first validator and artifact service tests.
3. Confirm the targeted tests fail for missing report contract behavior.
4. Add report contract helpers.
5. Wire validation into existing artifact create/link paths.
6. Run targeted tests, full unit verification, optional integration verification, secret scan, diff review, and final MoonSpec verification.

## Notes

- `MM-460` must remain visible in Moon Spec artifacts, verification output, commit text, and pull request metadata.
- Do not add a report-specific database table, storage backend, or mutable latest-report pointer.
- Do not reclassify generic `output.*` links as report artifacts.
- Verification note: `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` cannot run in this managed branch because `run-jira-orchestrate-for-mm-460-report-a-a0795596` does not match the numeric feature branch pattern; artifact paths were resolved manually from `.specify/feature.json`.
- Verification note: Docker is unavailable in this managed container (`/var/run/docker.sock` missing), so `./tools/test_integration.sh` was not run.
- Verification note: the secret-pattern scan only matched deliberate redaction test fixtures and regex literals in `tests/unit/workflows/temporal/test_artifacts.py` and `moonmind/workflows/temporal/report_artifacts.py`.
