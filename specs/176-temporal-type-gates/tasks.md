# Tasks: Temporal Type-Safety Gates

**Input**: Design documents from `/specs/176-temporal-type-gates/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/temporal-type-safety-gates.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around the single MM-331 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Each task references the relevant `FR-*`, acceptance scenario, success criterion, or `DESIGN-REQ-*` source mapping from `spec.md`.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh` when Docker is available; targeted Temporal workflow-boundary or replay-style tests are required for this story.
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and does not depend on incomplete tasks.
- Every task includes exact file paths.
- This task list covers exactly one story: `MM-331` / `STORY-005` / `temporal-type-gates`.

## Source Traceability Summary

- **Original Jira input**: T001, T020
- **FR-001**: T004, T007, T010, T011, T018
- **FR-002**: T004, T010, T012, T018
- **FR-003**: T004, T010, T018
- **FR-004**: T005, T007, T012, T013, T018
- **FR-005**: T005, T012, T013, T018
- **FR-006**: T005, T007, T012, T013, T018
- **FR-007**: T006, T011, T014, T018
- **FR-008**: T004, T005, T006, T010, T014, T018
- **Acceptance Scenarios 1-5**: T007, T009, T018
- **SC-001**: T004, T007, T010, T018
- **SC-002**: T005, T007, T012, T013, T018
- **SC-003**: T006, T014, T018
- **SC-004**: T004, T007, T010, T018
- **SC-005**: T001, T020
- **DESIGN-REQ-005**: T004, T010, T011, T018
- **DESIGN-REQ-018**: T007, T008, T009, T018, T019
- **DESIGN-REQ-019**: T006, T014, T018
- **DESIGN-REQ-020**: T005, T012, T013, T018

---

## Phase 1: Setup

**Purpose**: Confirm feature artifacts and test targets are ready before TDD work starts.

- [ ] T001 Confirm MM-331 from TOOL board, source design mappings, and single-story scope remain preserved in `specs/176-temporal-type-gates/spec.md`. [SC-005]
- [ ] T002 [P] Create the planned gate test placeholder path in `tests/unit/workflows/temporal/test_temporal_type_safety_gates.py`. [FR-001, FR-008]
- [ ] T003 [P] Create the planned workflow-boundary test placeholder path in `tests/integration/temporal/test_temporal_type_safety_gates.py`. [DESIGN-REQ-018]

---

## Phase 2: Foundational

**Purpose**: Establish the shared gate surface that the story tests and implementation will target.

**CRITICAL**: No production implementation work begins until this phase and the red-first test authoring tasks are complete.

No separate foundational code task is required before the story phase. Existing plan artifacts define the gate surface, data model, and contract; the next executable work is red-first story test authoring.

**Checkpoint**: Foundation ready. Story test authoring may begin.

---

## Phase 3: Story - Temporal Type-Safety Gates

**Summary**: As a reviewer of Temporal changes, I want compatibility evidence, replay or in-flight safety checks, and anti-pattern gates to catch unsafe type-safety migrations before they can affect running workflows.

**Independent Test**: Submit representative safe and unsafe Temporal contract changes to the review gates. Unsafe cases must be rejected with actionable reasons, safe compatibility-reviewed cases must pass, and final verification must trace the behavior back to MM-331 from TOOL board.

**Traceability**: FR-001 through FR-008, SC-001 through SC-005, DESIGN-REQ-005, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-020.

**Test Plan**:

- Unit: rule models, finding output, compatibility evidence decisions, anti-pattern detection, escape-hatch validation, and failure remediation messages.
- Integration: acceptance scenarios across representative Temporal contract fixtures, including workflow-boundary or replay-style compatibility evidence.

### Unit Tests (write first)

- [ ] T004 Add failing unit tests for compatibility evidence, additive evolution, unsafe non-additive changes, and actionable compatibility findings in `tests/unit/workflows/temporal/test_temporal_type_safety_gates.py`. [FR-001, FR-002, FR-003, FR-008, SC-001, SC-004, DESIGN-REQ-005]
- [ ] T005 Add failing unit tests for raw dictionary activity payloads, public raw dictionary handlers, generic action envelopes, provider-shaped workflow-facing results, untyped status leaks, nested raw bytes, and large workflow-history state in `tests/unit/workflows/temporal/test_temporal_type_safety_gates.py`. [FR-004, FR-005, FR-006, FR-008, SC-002, DESIGN-REQ-020]
- [ ] T006 Add failing unit tests for transitional, boundary-only, compatibility-justified escape hatch acceptance and rejection in `tests/unit/workflows/temporal/test_temporal_type_safety_gates.py`. [FR-007, FR-008, SC-003, DESIGN-REQ-019]

### Integration Tests (write first)

- [ ] T007 Add failing integration or workflow-boundary tests for acceptance scenarios 1-5 in `tests/integration/temporal/test_temporal_type_safety_gates.py`. [FR-001, FR-004, FR-006, SC-001, SC-002, SC-004, DESIGN-REQ-018]

### Red-First Confirmation

- [ ] T008 Run `pytest tests/unit/workflows/temporal/test_temporal_type_safety_gates.py -q` and confirm T004-T006 fail for the expected missing gate implementation. [DESIGN-REQ-018]
- [ ] T009 Run `pytest tests/integration/temporal/test_temporal_type_safety_gates.py -q` and confirm T007 fails for the expected missing gate implementation or fixture support. [DESIGN-REQ-018]

**Checkpoint**: Red-first evidence exists. Production implementation may begin after T008 and T009 record expected failures.

### Implementation

- [ ] T010 Create review gate rule, finding, compatibility evidence, and anti-pattern fixture models in `moonmind/workflows/temporal/type_safety_gates.py`. [FR-001, FR-002, FR-003, FR-008, DESIGN-REQ-005]
- [ ] T011 Implement compatibility evidence evaluation and non-additive migration/cutover rejection in `moonmind/workflows/temporal/type_safety_gates.py`. [FR-001, FR-002, FR-003, DESIGN-REQ-005]
- [ ] T012 Implement anti-pattern rule evaluation for raw dictionary payloads, public raw dictionary handlers, generic action envelopes, provider-shaped workflow-facing results, untyped status leaks, nested raw bytes, and large workflow-history state in `moonmind/workflows/temporal/type_safety_gates.py`. [FR-004, FR-005, FR-006, DESIGN-REQ-020]
- [ ] T013 Wire representative Temporal contract fixtures into the gate tests in `tests/unit/workflows/temporal/test_temporal_type_safety_gates.py`. [FR-004, FR-005, FR-006, SC-002]
- [ ] T014 Implement escape-hatch justification evaluation in `moonmind/workflows/temporal/type_safety_gates.py`. [FR-007, FR-008, DESIGN-REQ-019]
- [ ] T015 Add a reusable CLI/repository validation entry point in `tools/validate_temporal_type_safety.py` only for the gate contract defined in `specs/176-temporal-type-gates/contracts/temporal-type-safety-gates.md`. [FR-008]
- [ ] T016 Wire the CLI validation entry point to the gate implementation without adding network calls, runtime polling, or persistent storage in `tools/validate_temporal_type_safety.py`. [FR-008]
- [ ] T017 Update package exports only if needed for test imports in `moonmind/workflows/temporal/__init__.py`. [FR-008]
- [ ] T018 Run `pytest tests/unit/workflows/temporal/test_temporal_type_safety_gates.py tests/integration/temporal/test_temporal_type_safety_gates.py -q` and fix failures until the single story passes independently. [FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004]
- [ ] T019 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and record PASS or exact blocker in `specs/176-temporal-type-gates/verification.md`. [DESIGN-REQ-018]
- [ ] T020 Run `./tools/test_integration.sh` when Docker is available, or record the exact Docker socket blocker and targeted workflow-boundary evidence in `specs/176-temporal-type-gates/verification.md`. [SC-005, DESIGN-REQ-018]

**Checkpoint**: The story is fully functional, covered by unit and integration or documented integration-blocker evidence, and testable independently.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T021 [P] Update `specs/176-temporal-type-gates/quickstart.md` if implementation changes the targeted validation commands. [SC-005]
- [ ] T022 [P] Update `specs/176-temporal-type-gates/contracts/temporal-type-safety-gates.md` if the final finding shape or rule IDs change during implementation. [FR-008]
- [ ] T023 Review `moonmind/workflows/temporal/type_safety_gates.py` and `tools/validate_temporal_type_safety.py` for scope drift, hidden compatibility aliases, network calls, runtime polling, or persistent storage. [DESIGN-REQ-005, DESIGN-REQ-019]
- [ ] T024 Run the quickstart validation commands from `specs/176-temporal-type-gates/quickstart.md` and update `specs/176-temporal-type-gates/verification.md` with command results. [SC-001, SC-002, SC-003, SC-004]
- [ ] T025 Run `/speckit.verify` for `specs/176-temporal-type-gates` after implementation and tests pass, then record the verdict in `specs/176-temporal-type-gates/verification.md`. [SC-005]

---

## Dependencies And Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Starts immediately.
- **Foundational (Phase 2)**: Depends on T001-T003 and confirms no extra blocking setup is needed.
- **Story (Phase 3)**: Depends on Phase 2 and includes T004-T009 red-first confirmation before implementation.
- **Polish And Verification (Phase 4)**: Depends on T018 story validation and required verification evidence.

### Within The Story

- Unit tests T004-T006 must be written before implementation tasks T010-T017.
- Integration/workflow-boundary test T007 must be written before implementation tasks T010-T017.
- Red-first confirmation T008-T009 must complete before implementation tasks T010-T017.
- Gate models T010 come before compatibility, anti-pattern, and escape-hatch logic T011-T014.
- Validation entry point T015-T016 depends on the gate implementation T010-T014.
- Story validation T018 precedes full unit and integration verification T019-T020.
- Final `/speckit.verify` T025 runs only after implementation and tests pass.

### Parallel Opportunities

- T002 and T003 can run in parallel because they touch different test files.
- T004, T005, and T006 can be authored in parallel within the same test file only if one worker owns merge coordination; otherwise do them serially.
- T021 and T022 can run in parallel because they touch different design artifacts.

---

## Parallel Example

```bash
# Safe setup parallelism:
Task: "T002 Create tests/unit/workflows/temporal/test_temporal_type_safety_gates.py"
Task: "T003 Create tests/integration/temporal/test_temporal_type_safety_gates.py"

# Safe polish parallelism:
Task: "T021 Update specs/176-temporal-type-gates/quickstart.md"
Task: "T022 Update specs/176-temporal-type-gates/contracts/temporal-type-safety-gates.md"
```

---

## Implementation Strategy

1. Preserve the MM-331 single-story scope and source-design mappings.
2. Create failing unit and integration/workflow-boundary tests before any production code.
3. Confirm the failures are caused by missing gate implementation, not broken setup.
4. Implement the smallest gate model and evaluator needed to satisfy FR-001 through FR-008.
5. Keep the gate deterministic: no network calls, no runtime polling, no persistent storage.
6. Preserve stable Temporal-facing names and reject hidden compatibility aliases.
7. Run targeted story tests, full unit verification, integration verification or exact Docker blocker documentation, quickstart validation, and final `/speckit.verify`.

---

## Notes

- This task list covers one story only: `MM-331` / `STORY-005` / `temporal-type-gates`.
- The story is not complete until both red-first evidence and final verification evidence are recorded.
- Integration testing is required; if Docker is unavailable, record the exact blocker and the targeted workflow-boundary evidence that was run locally.
