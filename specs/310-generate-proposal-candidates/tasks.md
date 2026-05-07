# Tasks: Generate and Validate Proposal Candidates

**Input**: Design documents from `specs/310-generate-proposal-candidates/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and boundary-style integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-596 and DESIGN-REQ-001 through DESIGN-REQ-007 are preserved in `spec.md`. FR-001 through FR-011 map to the task proposal activity and canonical task contract boundaries.

**Test Commands**:

- Unit tests: `python -m pytest tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py -q`
- Integration tests: `python -m pytest tests/unit/workflows/temporal/test_proposal_activities.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm existing proposal activity and task proposal service test structure.

- [X] T001 Verify proposal activity and proposal service test files exist at `tests/unit/workflows/temporal/test_proposal_activities.py` and `tests/unit/workflows/task_proposals/test_service.py`

---

## Phase 2: Foundational

**Purpose**: Confirm canonical task contract and proposal activity surfaces before story work.

- [X] T002 Inspect `moonmind/workflows/tasks/task_contract.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/workflows/task_proposals/service.py` for existing validation and provenance helpers covering FR-003 through FR-010

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Evidence-Based Proposal Candidates

**Summary**: As a MoonMind operator, I want proposal candidate generation to use durable run evidence and validate candidates before delivery so follow-up work can be proposed without unintended side effects or invalid task payloads.

**Independent Test**: Invoke proposal generation and submission activities with representative workflow evidence, canonical and invalid task payloads, skill selectors, preset provenance, and a mock proposal service.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, SC-001, SC-002, SC-003, SC-004

**Test Plan**:

- Unit: canonical candidate validation, skill-tool acceptance, `agent_runtime` rejection, provenance preservation, absent-provenance non-fabrication, redacted errors.
- Integration: boundary-style activity tests for generation/submission separation and proposal service call behavior.

### Unit Tests (write first)

- [X] T003 [P] Add failing unit tests for canonical candidate validation accepting `tool.type = "skill"` and rejecting `tool.type = "agent_runtime"` in `tests/unit/workflows/temporal/test_proposal_activities.py` covering FR-003, FR-004, FR-005, FR-009, DESIGN-REQ-004, DESIGN-REQ-005
- [X] T004 [P] Add failing unit tests for preserving compact skill selectors, authored presets, and reliable step source provenance in `tests/unit/workflows/temporal/test_proposal_activities.py` covering FR-006, FR-007, FR-008, DESIGN-REQ-002, DESIGN-REQ-006
- [X] T005 Run `python -m pytest tests/unit/workflows/temporal/test_proposal_activities.py -q` to confirm T003-T004 fail for the expected reason

### Integration Tests (write first)

- [X] T006 Add failing boundary test proving `proposal_generate` does not call the proposal service and `proposal_submit` only calls it after validation in `tests/unit/workflows/temporal/test_proposal_activities.py` covering FR-001, FR-002, FR-010, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, SC-002
- [X] T007 Run `python -m pytest tests/unit/workflows/temporal/test_proposal_activities.py -q` to confirm T006 fails for the expected reason

### Implementation

- [X] T008 Add candidate task validation and redacted skip errors before delivery in `moonmind/workflows/temporal/activity_runtime.py` covering FR-003, FR-004, FR-005, FR-009
- [X] T009 Preserve compact task skill selectors, authored preset bindings, and reliable step source metadata during generation in `moonmind/workflows/temporal/activity_runtime.py` covering FR-006, FR-007, FR-008
- [X] T010 Verify `proposal_generate` remains side-effect-free and `proposal_submit` remains the first side-effect boundary in `moonmind/workflows/temporal/activity_runtime.py` covering FR-002, FR-010
- [X] T011 Run `python -m pytest tests/unit/workflows/temporal/test_proposal_activities.py tests/unit/workflows/task_proposals/test_service.py -q` and fix failures for FR-001 through FR-010

**Checkpoint**: The story is fully functional, covered by unit and boundary tests, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen traceability and run final validation.

- [X] T012 Update `specs/310-generate-proposal-candidates/verification.md` with final MoonSpec verification evidence preserving MM-596 and DESIGN-REQ-001 through DESIGN-REQ-007
- [X] T013 Run `./tools/test_unit.sh` for required unit verification, or record the exact blocker in `specs/310-generate-proposal-candidates/verification.md`
- [X] T014 Run `/moonspec-verify` to validate the final implementation against the MM-596 source request and record the result in `specs/310-generate-proposal-candidates/verification.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion.
- **Story (Phase 3)**: Depends on Foundational phase completion.
- **Polish (Phase 4)**: Depends on story tests and implementation passing.

### Within The Story

- T003, T004, and T006 must be written before production code changes.
- T005 and T007 confirm red-first behavior before T008 through T010.
- T008 through T010 modify the same production file and must be sequenced.
- T011 validates the focused unit and boundary suites.
- T012 through T014 happen after implementation.

### Parallel Opportunities

- T003 and T004 can be authored in parallel because they add separate test cases.
- T006 can be authored after T003/T004 without production code changes.

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 context checks.
2. Add failing proposal activity tests for validation, provenance, and side-effect boundaries.
3. Implement only the proposal activity changes needed to satisfy those tests.
4. Run focused proposal tests and required unit verification.
5. Record final MoonSpec verification with MM-596 traceability.

---

## Notes

- This task list covers one story only.
- No database migration or new provider integration is planned.
- Proposal generation must remain side-effect-free.
- Proposal submission must validate before delivery.
