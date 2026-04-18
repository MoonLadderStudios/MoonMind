# Tasks: Jira Orchestrate Blocker Preflight

**Input**: Design documents from `/specs/202-jira-orchestrate-blocker-preflight/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production change until they pass.

**Organization**: Tasks are grouped around the single user story "Stop Blocked Jira Orchestration" so the work remains independently testable and traceable to MM-398.

**Source Traceability**: Coverage includes FR-001 through FR-010, acceptance scenarios 1-5, edge cases, SC-001 through SC-005, and DESIGN-REQ-001 through DESIGN-REQ-008 from `spec.md`.

**Test Commands**:

- Unit tests: `pytest tests/unit/api/test_task_step_templates_service.py -q`
- Integration tests: `pytest tests/integration/test_startup_task_template_seeding.py -q`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel only when tasks touch different files and do not depend on incomplete work.
- Every task includes concrete file paths and requirement, scenario, success criterion, or source IDs where applicable.

## Phase 1: Setup

**Purpose**: Confirm the active feature context and the existing seeded preset/test surfaces before changing behavior.

- [X] T001 Confirm `.specify/feature.json` points to `specs/202-jira-orchestrate-blocker-preflight` and that `specs/202-jira-orchestrate-blocker-preflight/spec.md` defines exactly one user story for MM-398.
- [X] T002 Review existing Jira Orchestrate preset shape in `api_service/data/task_step_templates/jira-orchestrate.yaml` and existing assertions in `tests/unit/api/test_task_step_templates_service.py` and `tests/integration/test_startup_task_template_seeding.py`.

---

## Phase 2: Foundational

**Purpose**: Lock implementation boundaries before story test authoring begins.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Confirm the bounded seeded-preset scope by reviewing `specs/202-jira-orchestrate-blocker-preflight/plan.md`, `specs/202-jira-orchestrate-blocker-preflight/research.md`, and `api_service/data/task_step_templates/jira-orchestrate.yaml`; stop before story work if discovery shows a database migration, route, persistent model, or raw Jira credential path is needed. (FR-010, DESIGN-REQ-008)

**Checkpoint**: Foundation ready; story test and implementation work can begin.

---

## Phase 3: Story - Stop Blocked Jira Orchestration

**Summary**: As a Jira Orchestrate operator, I want the workflow to stop before implementation when the target Jira issue is blocked by an unresolved issue, so dependent work does not start before its prerequisite is complete.

**Independent Test**: Run the Jira Orchestrate preset expansion and startup seeding checks for unresolved blocker, resolved blocker, no blocker, and missing blocker-status semantics expressed in the blocker preflight instructions.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010; SC-001, SC-002, SC-003, SC-004, SC-005; DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008

**Test Plan**:

- Unit: catalog expansion step order, step count, blocker-preflight instructions, placeholder rendering, preserved In Progress and Code Review behavior, and guardrails against raw credential/scraping instructions.
- Integration: startup seed synchronization persists the blocker preflight in the global Jira Orchestrate template before MoonSpec implementation steps and preserves final PR/Code Review gates.

### Unit Tests (write first)

- [X] T004 Update failing Jira Orchestrate expansion assertions in `tests/unit/api/test_task_step_templates_service.py` for the new blocker preflight step order and step count. (FR-001, FR-002, FR-007, FR-009, SC-001, SC-004, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T005 Add failing Jira Orchestrate expansion assertions in `tests/unit/api/test_task_step_templates_service.py` that the blocker preflight instructions include the selected issue key, trusted Jira fetch, blocker-link inspection, Done-only continuation, missing-status fail-closed behavior, and no raw credential or scraping path. (FR-003, FR-004, FR-005, FR-006, FR-010, SC-002, SC-003, SC-005, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008)
- [X] T006 Run `pytest tests/unit/api/test_task_step_templates_service.py -q` and confirm T004-T005 fail for the expected missing blocker preflight behavior before implementation.

### Integration Tests (write first)

- [X] T007 Update failing startup seed assertions in `tests/integration/test_startup_task_template_seeding.py` so the persisted global `jira-orchestrate` template includes the blocker preflight after In Progress and before MoonSpec lifecycle steps. (FR-001, FR-002, FR-007, FR-009, SC-001, SC-004, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T008 Run `pytest tests/integration/test_startup_task_template_seeding.py -q` and confirm T007 fails for the expected missing seeded blocker preflight before implementation.

### Implementation

- [X] T009 Add the Jira blocker preflight step to `api_service/data/task_step_templates/jira-orchestrate.yaml` after `Move Jira issue to In Progress` and before `Load Jira preset brief`, with trusted Jira fetch, blocker-link inspection, Done-only continuation, fail-closed status handling, and operator-readable blocked output instructions. (FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008)
- [X] T010 Preserve existing Jira Orchestrate lifecycle wording in `api_service/data/task_step_templates/jira-orchestrate.yaml` for In Progress transition, Jira preset brief loading, MoonSpec stages, pull request handoff, and Code Review transition. (FR-007, FR-008, FR-009, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003)
- [X] T011 Run `pytest tests/unit/api/test_task_step_templates_service.py -q` and update only the intentional Jira Orchestrate expectations until the focused unit suite passes. (FR-001-FR-010)
- [X] T012 Run `pytest tests/integration/test_startup_task_template_seeding.py -q` and update only the intentional seeded Jira Orchestrate expectations until the focused integration suite passes. (FR-001-FR-010)

### Story Validation

- [X] T013 Validate the expanded `jira-orchestrate` preset from `tests/unit/api/test_task_step_templates_service.py` covers unresolved blocker, resolved blocker, no blocker, missing blocker-status, non-blocker-link, In Progress preservation, and Code Review preservation semantics from `specs/202-jira-orchestrate-blocker-preflight/spec.md`. (Acceptance Scenarios 1-5, Edge Cases, SC-001-SC-005)

**Checkpoint**: The story is fully covered by red-first unit and integration tests, implemented in the seeded preset, and independently testable through focused commands.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T014 [P] Review `specs/202-jira-orchestrate-blocker-preflight/quickstart.md` and update it only if final commands or environment blockers differ from the implemented validation path. (SC-005)
- [X] T015 [P] Review `specs/202-jira-orchestrate-blocker-preflight/contracts/jira-orchestrate-blocker-preflight.md` against the implemented preset wording and update it only if the contract needs clarification. (DESIGN-REQ-004, DESIGN-REQ-007)
- [X] T016 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full required unit verification or record the exact environment blocker in `specs/202-jira-orchestrate-blocker-preflight/verification.md`. (SC-005)
- [X] T017 Run `./tools/test_integration.sh` when Docker is available, or record the exact Docker socket blocker and focused integration evidence in `specs/202-jira-orchestrate-blocker-preflight/verification.md`. (SC-005)
- [X] T018 Run `/speckit.verify` against `specs/202-jira-orchestrate-blocker-preflight/spec.md` after implementation and tests pass, then record the final verdict and evidence in `specs/202-jira-orchestrate-blocker-preflight/verification.md`. (FR-001-FR-010, SC-001-SC-005, DESIGN-REQ-001-DESIGN-REQ-008)

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup completion and blocks story work.
- Story (Phase 3): depends on Foundational completion.
- Polish (Phase 4): depends on story implementation and focused tests passing.

### Within The Story

- T004-T005 must be written before T006.
- T007 must be written before T008.
- T006 and T008 must confirm red-first failure before T009.
- T009-T010 implement production preset changes only after red-first confirmation.
- T011-T013 validate the completed story after implementation.
- T016-T018 run only after focused story validation passes or blockers are recorded.

### Parallel Opportunities

- T004 and T007 can be drafted in parallel because they touch different test files, but both must fail before T009 begins.
- T014 and T015 can run in parallel because they touch different specification artifacts after implementation stabilizes.

---

## Parallel Example

```text
Task: "Update failing unit expansion assertions in tests/unit/api/test_task_step_templates_service.py"
Task: "Update failing startup seed assertions in tests/integration/test_startup_task_template_seeding.py"
```

---

## Implementation Strategy

1. Confirm active feature context and existing Jira Orchestrate surfaces.
2. Write unit and integration tests for the blocker preflight and confirm red-first failures.
3. Add the blocker preflight step to the seeded Jira Orchestrate YAML.
4. Rerun focused unit and startup seed integration tests until they pass.
5. Validate quickstart, run full unit and integration verification as available, and finish with `/speckit.verify`.

---

## Notes

- This task list covers exactly one story: Stop Blocked Jira Orchestration.
- No broad design breakdown or additional stories are included.
- No raw Jira credentials, browser scraping, or hardcoded Jira transition IDs are in scope.
- Preserve Jira issue MM-398 in implementation notes, verification output, commit text, and pull request metadata.
