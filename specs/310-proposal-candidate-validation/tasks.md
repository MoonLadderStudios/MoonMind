# Tasks: Proposal Candidate Validation

**Input**: `specs/310-proposal-candidate-validation/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/proposal-candidate-contract.md`, `quickstart.md`
**Prerequisites**: Spec, plan, research, data model, contract, and quickstart artifacts are present.
**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/workflows/test_run_proposals.py`
**Integration Test Command**: `./tools/test_integration.sh` when Docker is available; otherwise record the managed-container Docker socket blocker.

## Source Traceability Summary

The original Jira preset brief for `MM-596` is preserved in `spec.md`. This task list covers exactly one runtime story: generate proposal candidates from run evidence without side effects, validate them before delivery, preserve explicit skill/provenance intent, reject unsafe executable tool types, and keep generation separated from trusted submission. Requirement status from `plan.md`: missing rows require red-first tests and implementation (`FR-003`, `FR-006`, parts of `FR-009`, `DESIGN-REQ-018`); partial rows require tests and completion work (`FR-001`, `FR-002`, `FR-004`, `FR-005`, `FR-009`, `DESIGN-REQ-007`, `DESIGN-REQ-008`, `DESIGN-REQ-017`, `DESIGN-REQ-019`); implemented-unverified rows require verification tests with conditional fallback implementation (`FR-007`, `FR-008`, `SC-001`, `SC-006`, `DESIGN-REQ-032`); implemented-verified traceability rows are preserved through final verification (`FR-010`, `SC-007`).

## Phase 1: Setup

- [X] T001 Confirm active feature context in `.specify/feature.json` points to `specs/310-proposal-candidate-validation` and inspect `specs/310-proposal-candidate-validation/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/proposal-candidate-contract.md`, and `quickstart.md` for `MM-596` traceability.
- [X] T002 Inspect current proposal activity, service, and workflow boundaries in `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/task_proposals/service.py`, `moonmind/workflows/temporal/workflows/run.py`, and `moonmind/workflows/temporal/activity_catalog.py` before writing tests.

## Phase 2: Foundational

- [X] T003 Identify the canonical candidate validation entry point and helper placement in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/task_proposals/service.py` for FR-002, FR-003, FR-009, DESIGN-REQ-008, DESIGN-REQ-017, and DESIGN-REQ-018.
- [X] T004 Confirm existing task contract validation behavior in `moonmind/workflows/tasks/task_contract.py` for `tool.type=skill`, `tool.type=agent_runtime`, `task.skills`, `steps[].skills`, `authoredPresets`, and `steps[].source` before adding proposal-specific tests.

## Phase 3: Generate Validated Proposal Candidates

**Story Summary**: Proposal generation emits side-effect-free follow-up candidates from durable run evidence, then trusted submission validates candidates before any delivery side effect while preserving explicit skill/provenance intent and rejecting unsafe payloads.

**Independent Test**: Run proposal generation with durable evidence and proposal submission with mixed valid/invalid candidates; confirm generation performs no side effects, accepted candidates validate and submit, rejected candidates return redacted errors without service/repository calls, and the workflow schedules `proposal.generate` and `proposal.submit` as distinct activity boundaries.

**Traceability IDs**: FR-001 through FR-010; acceptance scenarios 1-7; edge cases for incomplete evidence, malformed tool/skill selectors, large embedded bodies, absent provenance, validation failure before delivery, and global proposal disablement; SC-001 through SC-007; DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-032; MM-596.

### Unit Test Plan

Add failing tests in `tests/unit/workflows/temporal/test_proposal_activities.py` for generation preservation/non-fabrication and submission candidate validation. Add focused service validation tests in `tests/unit/workflows/task_proposals/test_service.py` where repository calls must not happen for invalid stored proposal payloads.

### Integration / Boundary Test Plan

Extend `tests/unit/workflows/temporal/workflows/test_run_proposals.py` as the workflow-boundary test surface for activity ordering and distinct route/capability separation. Use the hermetic integration suite only if Docker is available.

- [X] T005 Add failing proposal generation unit tests in `tests/unit/workflows/temporal/test_proposal_activities.py` proving explicit `task.skills`, `steps[].skills`, `task.authoredPresets`, and `steps[].source` are preserved from reliable parent task evidence and absent provenance is not fabricated. Covers FR-004, FR-005, FR-006, SC-004, SC-005, DESIGN-REQ-019.
- [X] T006 Add failing proposal generation no-side-effect and no-large-body unit tests in `tests/unit/workflows/temporal/test_proposal_activities.py` proving generation has no proposal service dependency, emits refs/selectors only, and does not embed skill bodies or runtime materialization state. Covers FR-001, FR-007, SC-001, DESIGN-REQ-007.
- [X] T007 Add failing proposal submission unit tests in `tests/unit/workflows/temporal/test_proposal_activities.py` proving `tool.type=skill` candidates are accepted, `tool.type=agent_runtime` candidates are rejected with redacted errors, malformed skill selectors are rejected, and rejected candidates do not call the proposal service. Covers FR-002, FR-003, FR-009, SC-002, SC-003, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018.
- [X] T008 [P] Add failing service validation tests in `tests/unit/workflows/task_proposals/test_service.py` proving candidate task payload validation rejects unsafe executable tool types and materialized skill bodies before repository creation. Covers FR-003, FR-004, FR-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019.
- [X] T009 [P] Add workflow-boundary tests in `tests/unit/workflows/temporal/workflows/test_run_proposals.py` proving `proposal.generate` and `proposal.submit` remain distinct scheduled activities and submission receives candidates only after generation. Covers FR-008, SC-006, DESIGN-REQ-032.
- [X] T010 Run the focused unit command and confirm the new tests from T005-T009 fail for the expected missing behavior before production changes.
- [X] T011 Implement proposal candidate validation helpers in `moonmind/workflows/temporal/activity_runtime.py` so `proposal_submit()` validates each candidate before counting or service calls, accepts `tool.type=skill`, rejects `tool.type=agent_runtime`, rejects malformed skill selectors, and returns bounded redacted errors. Covers FR-002, FR-003, FR-009, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018.
- [X] T012 Implement skill/provenance preservation and non-fabrication in `moonmind/workflows/temporal/activity_runtime.py` for `proposal_generate()` using explicit parent task evidence only. Covers FR-004, FR-005, FR-006, DESIGN-REQ-019.
- [X] T013 Implement guardrails in `moonmind/workflows/temporal/activity_runtime.py` to omit skill bodies, resolved skill snapshots, runtime materialization state, and large embedded context bodies from generated candidates. Covers FR-001, FR-004, FR-007, DESIGN-REQ-007, DESIGN-REQ-019.
- [X] T014 Implement or tighten service-level proposal payload validation in `moonmind/workflows/task_proposals/service.py` so unsafe executable tool types and materialized skill bodies are rejected before `create_proposal()` repository calls. Covers FR-002, FR-003, FR-004, FR-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019.
- [X] T015 Preserve existing workflow activity separation in `moonmind/workflows/temporal/workflows/run.py` and `moonmind/workflows/temporal/activity_catalog.py`; only adjust code if T009 exposes a boundary regression. Covers FR-008, DESIGN-REQ-032.
- [X] T016 Run the focused unit command until tests for T005-T009 pass.
- [X] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for the full unit suite and capture the result.

## Final Phase: Polish And Verification

- [X] T018 [P] Review `specs/310-proposal-candidate-validation/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/proposal-candidate-contract.md`, `quickstart.md`, and `tasks.md` for `MM-596` and DESIGN-REQ traceability. Covers FR-010, SC-007.
- [X] T019 [P] Run `rg -n "MM-596|DESIGN-REQ-007|DESIGN-REQ-008|DESIGN-REQ-017|DESIGN-REQ-018|DESIGN-REQ-019|DESIGN-REQ-032" specs/310-proposal-candidate-validation moonmind/workflows/temporal/activity_runtime.py moonmind/workflows/task_proposals/service.py tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/workflows/test_run_proposals.py` and confirm traceability is present where expected.
- [X] T020 Run `./tools/test_integration.sh` when Docker is available; if unavailable in the managed container, record the exact Docker/socket blocker in verification evidence.
- [X] T021 Run `/speckit.verify` equivalent through `moonspec-verify` for `specs/310-proposal-candidate-validation/` and produce `verification.md` with a FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, or NO_DETERMINATION verdict.

## Dependencies And Execution Order

- T001-T004 must complete before story tests.
- T008 and T009 can be written in parallel after T004 because they touch distinct test files. T005-T007 target the same file and should be sequenced together.
- T010 must run after T005-T009 and before T011-T015.
- T011-T014 implement the tested behavior; T015 is conditional if boundary tests fail.
- T016 must pass before full suite T017.
- T018-T021 run after implementation and focused tests pass.

## Parallel Examples

```bash
# After setup/foundation, these can be done independently:
# T008 in tests/unit/workflows/task_proposals/test_service.py
# T009 in tests/unit/workflows/temporal/workflows/test_run_proposals.py
# T005-T007 target one file and should be done sequentially
```

## Implementation Strategy

Start with tests that fail for the missing or partial contract behavior: unsafe tool type rejection, canonical candidate validation, skill/provenance preservation, non-fabrication, no embedded skill bodies, and distinct activity boundaries. Implement the smallest proposal activity/service helper changes needed for those tests. Preserve existing proposal policy, deduplication, notification, promotion, and best-effort workflow behavior. Do not add new persistent storage, compatibility aliases, external tracker calls, or UI surfaces for this story.
