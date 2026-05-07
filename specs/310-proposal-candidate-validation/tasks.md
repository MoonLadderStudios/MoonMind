# Tasks: Proposal Candidate Validation

**Input**: Design documents from `/work/agent_jobs/mm:178269a2-85d8-40b9-a55f-ae8a6ca25e2d/repo/specs/310-proposal-candidate-validation/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/proposal-candidate-contract.md`, and `quickstart.md`
**Tests**: Unit tests and integration tests are required. Write or verify tests first, confirm red-first behavior on a pre-implementation baseline when replaying this plan, then implement production changes until tests pass.
**Source Traceability**: Original Jira preset brief `MM-596`; DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-032.

**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/workflows/test_run_proposals.py`
**Integration Test Command**: `./tools/test_integration.sh` when Docker is available; otherwise record the managed-container Docker socket blocker.
**Final Verification**: `/speckit.verify` through `moonspec-verify` for `/work/agent_jobs/mm:178269a2-85d8-40b9-a55f-ae8a6ca25e2d/repo/specs/310-proposal-candidate-validation/`

## Source Traceability Summary

This task list covers exactly one runtime story: proposal generation emits side-effect-free follow-up candidates from durable run evidence, trusted submission validates every candidate before delivery side effects, explicit skill selectors and reliable provenance are preserved by compact reference/selector only, unsafe executable tool types are rejected, and `proposal.generate` stays separate from `proposal.submit`.

`plan.md` currently classifies all 23 tracked FR/SC/DESIGN rows as `implemented_verified`. The tasks below preserve the complete red-first implementation path for replayability, but current execution should treat code changes as unnecessary unless validation exposes a regression. Final validation still rechecks every implemented-verified row and keeps `MM-596` traceability visible.

## Phase 1: Setup

- [X] T001 Confirm active feature context in `.specify/feature.json` points to `specs/310-proposal-candidate-validation` and verify `MM-596` is preserved in `specs/310-proposal-candidate-validation/spec.md`.
- [X] T002 Inspect refreshed planning inputs in `specs/310-proposal-candidate-validation/plan.md`, `specs/310-proposal-candidate-validation/research.md`, `specs/310-proposal-candidate-validation/data-model.md`, `specs/310-proposal-candidate-validation/contracts/proposal-candidate-contract.md`, and `specs/310-proposal-candidate-validation/quickstart.md`.
- [X] T003 [P] Confirm unit test tooling command from `specs/310-proposal-candidate-validation/quickstart.md` targets `tests/unit/workflows/temporal/test_proposal_activities.py`, `tests/unit/workflows/task_proposals/test_service.py`, and `tests/unit/workflows/temporal/workflows/test_run_proposals.py`.
- [X] T004 [P] Confirm integration test tooling command from `specs/310-proposal-candidate-validation/quickstart.md` uses `./tools/test_integration.sh` and documents the Docker-socket managed-container blocker.

## Phase 2: Foundational

- [X] T005 Inspect proposal activity and task-queue mappings in `moonmind/workflows/temporal/activity_runtime.py` for `proposal.generate` and `proposal.submit`. Covers FR-008, SC-006, DESIGN-REQ-032.
- [X] T006 Inspect trusted proposal service validation boundaries in `moonmind/workflows/task_proposals/service.py` before modifying candidate validation. Covers FR-002, FR-003, FR-009, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018.
- [X] T007 Inspect canonical task payload and skill/provenance models in `moonmind/workflows/tasks/task_contract.py` before modifying proposal candidate shape handling. Covers FR-002, FR-004, FR-005, FR-006, DESIGN-REQ-017, DESIGN-REQ-019.

## Phase 3: Story - Generate Validated Proposal Candidates

**Summary**: Proposal generation emits side-effect-free follow-up candidates from durable run evidence, then trusted submission validates candidates before any delivery side effect while preserving explicit skill/provenance intent and rejecting unsafe payloads.

**Independent Test**: Run proposal generation with durable evidence and proposal submission with mixed valid/invalid candidates; confirm generation performs no side effects, accepted candidates validate and submit, rejected candidates return redacted errors without service/repository calls, and the workflow schedules `proposal.generate` and `proposal.submit` as distinct activity boundaries.

**Traceability**: FR-001 through FR-010; acceptance scenarios 1-7; edge cases for incomplete evidence, malformed tool/skill selectors, large embedded bodies, absent provenance, validation failure before delivery, and global proposal disablement; SC-001 through SC-007; DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-032; MM-596.

**Test Plan**:

- Unit: proposal generation preservation/non-fabrication, no side effects, no embedded skill bodies, candidate validation, unsafe tool rejection, redacted errors, and service-call avoidance.
- Integration / boundary: workflow proposal stage schedules `proposal.generate` and `proposal.submit` distinctly; hermetic integration suite runs when Docker is available.

### Unit Tests

- [X] T008 [P] Add or verify red-first proposal generation unit coverage in `tests/unit/workflows/temporal/test_proposal_activities.py` for explicit `task.skills`, `steps[].skills`, `task.authoredPresets`, `steps[].source`, and absent-provenance non-fabrication. Covers FR-004, FR-005, FR-006, SC-004, SC-005, DESIGN-REQ-019.
- [X] T009 [P] Add or verify red-first no-side-effect and no-large-body unit coverage in `tests/unit/workflows/temporal/test_proposal_activities.py` proving generation has no proposal service dependency and does not embed skill bodies or runtime materialization state. Covers FR-001, FR-007, SC-001, DESIGN-REQ-007.
- [X] T010 [P] Add or verify red-first proposal submission unit coverage in `tests/unit/workflows/temporal/test_proposal_activities.py` proving `tool.type=skill` is accepted, `tool.type=agent_runtime` is rejected with redacted errors, malformed skill selectors are rejected, and rejected candidates do not call the proposal service. Covers FR-002, FR-003, FR-009, SC-002, SC-003, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018.
- [X] T011 [P] Add or verify red-first service validation unit coverage in `tests/unit/workflows/task_proposals/test_service.py` proving unsafe executable tool types and materialized skill bodies are rejected before repository creation. Covers FR-003, FR-004, FR-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019.

### Integration / Boundary Tests

- [X] T012 [P] Add or verify workflow-boundary coverage in `tests/unit/workflows/temporal/workflows/test_run_proposals.py` proving `proposal.generate` and `proposal.submit` remain distinct scheduled activities and submission receives candidates only after generation. Covers FR-008, SC-006, DESIGN-REQ-032.
- [X] T013 Run `./tools/test_integration.sh` for hermetic integration coverage when Docker is available; if unavailable, record the exact `/var/run/docker.sock` blocker in `specs/310-proposal-candidate-validation/verification.md`. Covers acceptance scenarios 1-7 and SC-001 through SC-007.

### Red-First Confirmation

- [X] T014 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/workflows/test_run_proposals.py` on a pre-implementation baseline when replaying this plan and confirm T008-T012 fail for the expected missing behavior.
- [X] T015 Confirm any red-first failures are tied to missing proposal candidate validation, skill/provenance preservation, unsafe tool rejection, no-side-effect generation, or workflow-boundary separation rather than fixture or environment errors in `tests/unit/workflows/temporal/test_proposal_activities.py`.

### Implementation

- [X] T016 Implement or preserve candidate validation helpers in `moonmind/workflows/temporal/activity_runtime.py` so `proposal_submit()` validates every candidate before counting or service calls, accepts `tool.type=skill`, rejects `tool.type=agent_runtime`, rejects malformed skill selectors, and returns bounded redacted errors. Covers FR-002, FR-003, FR-009, DESIGN-REQ-008, DESIGN-REQ-017, DESIGN-REQ-018.
- [X] T017 Implement or preserve skill/provenance preservation in `moonmind/workflows/temporal/activity_runtime.py` so `proposal_generate()` copies only explicit parent task evidence and does not fabricate absent provenance. Covers FR-004, FR-005, FR-006, DESIGN-REQ-019.
- [X] T018 Implement or preserve guardrails in `moonmind/workflows/temporal/activity_runtime.py` that omit skill bodies, resolved active skill snapshots, runtime materialization state, and large embedded context bodies from generated candidates. Covers FR-001, FR-004, FR-007, DESIGN-REQ-007, DESIGN-REQ-019.
- [X] T019 Implement or preserve service-level proposal payload validation in `moonmind/workflows/task_proposals/service.py` so unsafe executable tool types and materialized skill bodies are rejected before `create_proposal()` repository calls. Covers FR-002, FR-003, FR-004, FR-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019.
- [X] T020 Preserve workflow activity separation in `moonmind/workflows/temporal/workflows/run.py` and `moonmind/workflows/temporal/activity_catalog.py`; adjust only if T012 exposes a boundary regression. Covers FR-008, SC-006, DESIGN-REQ-032.

### Story Validation

- [X] T021 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/workflows/test_run_proposals.py` until focused unit and workflow-boundary coverage passes.
- [X] T022 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit validation and record results in `specs/310-proposal-candidate-validation/verification.md`.
- [X] T023 Run `rg -n "MM-596|DESIGN-REQ-007|DESIGN-REQ-008|DESIGN-REQ-017|DESIGN-REQ-018|DESIGN-REQ-019|DESIGN-REQ-032" specs/310-proposal-candidate-validation moonmind/workflows/temporal/activity_runtime.py moonmind/workflows/task_proposals/service.py tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py tests/unit/workflows/temporal/workflows/test_run_proposals.py` and confirm traceability remains present. Covers FR-010, SC-007.

## Final Phase: Polish And Verification

- [X] T024 [P] Review `specs/310-proposal-candidate-validation/spec.md`, `specs/310-proposal-candidate-validation/plan.md`, `specs/310-proposal-candidate-validation/research.md`, `specs/310-proposal-candidate-validation/data-model.md`, `specs/310-proposal-candidate-validation/contracts/proposal-candidate-contract.md`, `specs/310-proposal-candidate-validation/quickstart.md`, and `specs/310-proposal-candidate-validation/tasks.md` for single-story scope and `MM-596` traceability.
- [X] T025 [P] Confirm `specs/310-proposal-candidate-validation/tasks.md` contains red-first unit tests, integration tests, implementation tasks, story validation, and final `/speckit.verify` work.
- [X] T026 Run `/speckit.verify` equivalent through `moonspec-verify` for `specs/310-proposal-candidate-validation/` and update `specs/310-proposal-candidate-validation/verification.md` with a FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, or NO_DETERMINATION verdict.

## Dependencies And Execution Order

- Phase 1 setup tasks T001-T004 complete before foundational inspection.
- Phase 2 foundational tasks T005-T007 complete before story test authoring or verification.
- Unit test tasks T008-T011 and boundary test task T012 can run in parallel because they touch distinct files or independent test areas.
- Integration command task T013 can run after T012 or after focused unit coverage is green when Docker is available.
- Red-first confirmation tasks T014-T015 must complete before implementation tasks T016-T020 when replaying this plan from a pre-implementation baseline.
- Implementation tasks T016-T020 run only after red-first confirmation, or are verified as already implemented when using the current `implemented_verified` plan state.
- Story validation tasks T021-T023 run after implementation tasks pass.
- Final phase tasks T024-T026 run after story validation.

## Parallel Example

```bash
# After T005-T007:
# T008/T009/T010 in tests/unit/workflows/temporal/test_proposal_activities.py should be coordinated because they share one file.
# T011 can run independently in tests/unit/workflows/task_proposals/test_service.py.
# T012 can run independently in tests/unit/workflows/temporal/workflows/test_run_proposals.py.
```

## Implementation Strategy

For a clean replay, start with red-first tests for unsafe tool type rejection, canonical candidate validation, skill/provenance preservation, non-fabrication, no embedded skill bodies, and distinct activity boundaries. Confirm those tests fail on the pre-implementation baseline, then implement the smallest proposal activity/service changes needed for the tests to pass. For the current repository state, `plan.md` marks every tracked row `implemented_verified`; therefore, execute the verification tasks first and only revisit implementation tasks if a regression appears.
