# Tasks: Preserve Slash Command Fidelity Across Edit, Rerun, Details, and Audit

**Input**: Design documents from `/work/agent_jobs/mm:e1afde4a-fc92-48d9-811d-6ca6df9c1b32/repo/specs/357-preserve-slash-command-fidelity/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/slash-command-fidelity.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: `Audit Historical Slash Command Meaning`.

**Source Traceability**: MM-687 and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-013, SCN-001 through SCN-006, SC-001 through SC-007, and DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018.

**Requirement Status Summary**: `missing`: FR-006, FR-009, FR-012, SCN-002, SCN-005, SC-002, SC-005, DESIGN-REQ-015. `partial`: FR-001, FR-002, FR-004, FR-005, FR-007, FR-008, FR-010, FR-011, SCN-003, SCN-004, SCN-006, SC-003, SC-004, SC-006, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-014, DESIGN-REQ-018. `implemented_unverified`: FR-003, FR-013, SCN-001, SC-001, SC-007.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Focused frontend unit tests: `./tools/test_unit.sh --ui-args <path>`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete work.
- Every task includes concrete file paths and requirement, scenario, success, or source IDs when applicable.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing feature artifacts and test harness are ready before story work.

- [X] T001 Verify active feature pointer references `specs/357-preserve-slash-command-fidelity` in `.specify/feature.json` for MM-687 traceability FR-013 SC-007
- [X] T002 [P] Confirm frontend test dependencies are available for `./tools/test_unit.sh --ui-args frontend/src/lib/temporalTaskEditing.test.ts` using `package-lock.json`
- [X] T003 [P] Confirm Python unit and integration test runners are available through `./tools/test_unit.sh` and `./tools/test_integration.sh`
- [X] T004 [P] Review `specs/357-preserve-slash-command-fidelity/contracts/slash-command-fidelity.md` against `specs/357-preserve-slash-command-fidelity/spec.md` before test authoring FR-013 DESIGN-REQ-002 DESIGN-REQ-015

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared fixtures and traceability before unit and integration tests are written.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T005 Create shared slash-command historical snapshot fixtures in `frontend/src/lib/temporalTaskEditing.test.ts` covering task-level and step-level `runtimeCommand` metadata FR-001 FR-002 DESIGN-REQ-002
- [X] T006 [P] Create shared Task Detail slash-command fixture data in `frontend/src/entrypoints/task-detail.test.tsx` covering original instructions, runtime, render mode, status, and catalog versions FR-008 FR-009 DESIGN-REQ-015
- [X] T007 [P] Create shared Python runtime command snapshot/event fixtures in `tests/unit/workflows/tasks/test_task_contract.py` covering detected, rendered, pass-through, and missing-metadata cases FR-001 FR-010 DESIGN-REQ-002 DESIGN-REQ-018
- [X] T008 [P] Create integration fixture plan in `tests/integration/api/test_runtime_command_historical_fidelity.py` for artifact-backed task input snapshots, rerun source data, and observability events FR-005 FR-010 FR-012

**Checkpoint**: Shared fixtures and test files are ready; story test authoring may begin.

---

## Phase 3: Story - Audit Historical Slash Command Meaning

**Summary**: As an operator reviewing, editing, or rerunning previous work, I want MoonMind to preserve and display both the original authored instructions and the runtime command interpretation captured at submission so historical tasks remain auditable even when runtime capabilities or hints later change.

**Independent Test**: Create or fixture a slash-command task, preserve its authoritative task input snapshot, then validate edit mode, exact rerun, edit-for-rerun, task details, and audit output against the preserved authored instructions and command metadata after current runtime capability or hint assumptions change.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018

**Unit Test Plan**:

- Frontend draft reconstruction tests for edit mode, exact rerun, edit-for-rerun, absent metadata, and version-drift warnings.
- Task Detail rendering tests for original instructions and runtime command interpretation.
- Python unit tests for authoritative snapshot metadata preservation and command audit event sanitization.

**Integration Test Plan**:

- Hermetic API/workflow-boundary test proving artifact-backed execution data can reconstruct historical instructions, runtime command metadata, rerun provenance, and non-secret observability events.

### Unit Tests (write first)

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T009 [P] Add failing unit tests in `frontend/src/lib/temporalTaskEditing.test.ts` for edit-mode restoration of authored instructions and `runtimeCommand` metadata from snapshots FR-001 FR-002 FR-003 FR-004 SCN-001 SC-001 DESIGN-REQ-002 DESIGN-REQ-003
- [X] T010 Add failing unit tests in `frontend/src/lib/temporalTaskEditing.test.ts` for historical snapshots without `runtimeCommand` staying preview-only and preserving raw instructions FR-004 SCN-002 SC-002 DESIGN-REQ-003
- [X] T011 [P] Add failing unit tests in `frontend/src/entrypoints/task-create.test.tsx` for exact rerun preserving `runtimeCommand`, `runtimeCapabilityVersion`, and `hintCatalogVersion` FR-005 SCN-003 SC-003 DESIGN-REQ-014
- [X] T012 Add failing unit tests in `frontend/src/entrypoints/task-create.test.tsx` for edit-for-rerun showing current version-drift warnings without mutating source-run command metadata FR-006 FR-007 SCN-004 SC-004 DESIGN-REQ-014
- [X] T013 [P] Add failing unit tests in `frontend/src/entrypoints/task-detail.test.tsx` for displaying original authored instructions and missing-metadata state for slash-command tasks FR-008 SCN-005 SC-005 DESIGN-REQ-015
- [X] T014 Add failing unit tests in `frontend/src/entrypoints/task-detail.test.tsx` for displaying runtime command interpretation including command, runtime, render mode, status, and catalog versions FR-009 SCN-005 SC-005 DESIGN-REQ-015
- [X] T015 [P] Add failing Python unit tests in `tests/unit/workflows/tasks/test_task_contract.py` for durable task input snapshot preservation of task-level and step-level runtime command metadata FR-001 FR-002 DESIGN-REQ-002
- [X] T016 [P] Add failing Python unit tests in `tests/unit/workflows/temporal/runtime/test_runtime_command_audit_events.py` for `runtime_command.detected`, `runtime_command.rendered`, and `runtime_command.passthrough` event construction and secret-safe sanitization FR-010 FR-011 SCN-006 SC-006 DESIGN-REQ-018
- [X] T017 Run `./tools/test_unit.sh --ui-args frontend/src/lib/temporalTaskEditing.test.ts frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx` and targeted `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/runtime/test_runtime_command_audit_events.py` to confirm T009-T016 fail for the expected missing behavior

### Integration Tests (write first)

- [X] T018 Add failing hermetic `integration_ci` integration test in `tests/integration/api/test_runtime_command_historical_fidelity.py` for artifact-backed edit/detail reconstruction of original instructions and runtime command metadata FR-001 FR-002 FR-003 FR-008 FR-009 SCN-001 SCN-005 DESIGN-REQ-002 DESIGN-REQ-015
- [X] T019 Add failing hermetic `integration_ci` integration test in `tests/integration/api/test_runtime_command_historical_fidelity.py` for exact rerun and edit-for-rerun preserving source-run metadata while surfacing current warnings FR-005 FR-006 FR-007 SCN-003 SCN-004 DESIGN-REQ-014
- [X] T020 Add failing hermetic `integration_ci` integration test in `tests/integration/api/test_runtime_command_historical_fidelity.py` for detected/rendered/pass-through audit events excluding secrets and exposing operator-readable command evidence FR-010 FR-011 FR-012 SCN-006 SC-006 DESIGN-REQ-018
- [X] T021 Run focused integration coverage for `tests/integration/api/test_runtime_command_historical_fidelity.py` with `./tools/test_integration.sh` or the documented `integration_ci` equivalent to confirm T018-T020 fail for the expected missing behavior

### Conditional Verification Fallbacks

- [X] T022 If T009 or T015 unexpectedly pass without code changes, record existing evidence for FR-003 SCN-001 SC-001 in `specs/357-preserve-slash-command-fidelity/research.md`; otherwise keep implementation tasks T024-T027 active
- [X] T023 If traceability checks for FR-013 SC-007 fail, update `specs/357-preserve-slash-command-fidelity/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/slash-command-fidelity.md`, `quickstart.md`, and `tasks.md` to preserve MM-687 and the original Jira preset brief

### Implementation

- [X] T024 Update snapshot and draft reconstruction logic in `frontend/src/lib/temporalTaskEditing.ts` so edit mode and rerun drafts restore historical authored instructions and `runtimeCommand` metadata while preserving absent-metadata raw instructions FR-001 FR-002 FR-003 FR-004 SCN-001 SCN-002 DESIGN-REQ-002 DESIGN-REQ-003
- [X] T025 Update rerun submit behavior in `frontend/src/entrypoints/task-create.tsx` so exact rerun preserves source runtime command metadata and edit-for-rerun warning recomputation cannot mutate source-run evidence FR-005 FR-006 FR-007 SCN-003 SCN-004 DESIGN-REQ-014
- [X] T026 Update backend rerun/source metadata handling in `moonmind/workflows/temporal/service.py` only if T011/T019 prove source runtime command metadata or catalog versions are dropped across exact rerun FR-005 FR-006 SCN-003 DESIGN-REQ-014
- [X] T027 Update authoritative task input snapshot behavior in `moonmind/workflows/tasks/task_contract.py` only if T015 proves task-level or step-level `runtimeCommand` fields are incomplete or dropped FR-001 FR-002 DESIGN-REQ-002
- [X] T028 Update Task Detail API/view model mapping in `api_service/api/routers/executions.py` or `api_service/api/routers/task_dashboard_view_model.py` to expose original instructions and runtime command interpretation for detail views FR-008 FR-009 SCN-005 DESIGN-REQ-015
- [X] T029 Update Task Detail UI in `frontend/src/entrypoints/task-detail.tsx` to show original authored instructions alongside command, runtime, render mode, status, and version details when available FR-008 FR-009 SCN-005 SC-005 DESIGN-REQ-015
- [X] T030 Add runtime command version-drift warning model and display behavior in `frontend/src/lib/temporalTaskEditing.ts` and `frontend/src/entrypoints/task-create.tsx` for edit-for-rerun/current-preview warnings FR-006 FR-007 SCN-004 SC-004 DESIGN-REQ-014
- [X] T031 Implement runtime command audit event construction and sanitization in `moonmind/workflows/temporal/runtime/launcher.py` or a new helper under `moonmind/workflows/temporal/runtime/` for detected, rendered, and pass-through cases FR-010 FR-011 SCN-006 DESIGN-REQ-018
- [X] T032 Wire runtime command audit events through existing observability or control-event surfaces in `api_service/api/routers/task_runs.py` and `moonmind/workflows/temporal/runtime/launcher.py` without adding new persistent tables FR-010 FR-011 FR-012 SCN-006 SC-006 DESIGN-REQ-018
- [X] T033 Ensure UI display and audit serialization treat command names, args, instruction bodies, and diagnostics as untrusted text in `frontend/src/entrypoints/task-detail.tsx`, `frontend/src/entrypoints/task-create.tsx`, and `moonmind/workflows/temporal/runtime/launcher.py` FR-011 DESIGN-REQ-018

### Story Validation

- [X] T034 Run focused frontend unit tests with `./tools/test_unit.sh --ui-args frontend/src/lib/temporalTaskEditing.test.ts frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx` and fix failures in `frontend/src/lib/temporalTaskEditing.ts`, `frontend/src/entrypoints/task-create.tsx`, and `frontend/src/entrypoints/task-detail.tsx`
- [X] T035 Run focused Python unit tests with `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/runtime/test_runtime_command_audit_events.py` and fix failures in `moonmind/workflows/tasks/task_contract.py` and `moonmind/workflows/temporal/runtime/launcher.py`
- [X] T036 Run focused integration coverage for `tests/integration/api/test_runtime_command_historical_fidelity.py` with `./tools/test_integration.sh` or the documented integration_ci equivalent and fix failures in API/workflow boundaries
- [X] T037 Verify the single story end to end against `specs/357-preserve-slash-command-fidelity/quickstart.md`, including edit mode, exact rerun, edit-for-rerun, task details, and audit event checks FR-012 SC-001 SC-002 SC-003 SC-004 SC-005 SC-006

**Checkpoint**: The MM-687 story is fully functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding scope.

- [X] T038 [P] Review `specs/357-preserve-slash-command-fidelity/contracts/slash-command-fidelity.md` against implemented UI/API/audit behavior and update only if implementation reveals contract drift FR-013 SC-007
- [X] T039 Add or refine edge-case tests for escaped literal slash text, malformed command metadata, opaque unknown commands, and missing historical metadata in `frontend/src/entrypoints/task-create.test.tsx`, `frontend/src/entrypoints/task-detail.test.tsx`, and `tests/unit/workflows/temporal/runtime/test_runtime_command_audit_events.py` FR-004 FR-010 FR-011 DESIGN-REQ-018
- [X] T040 [P] Confirm no secret-like values are emitted by new audit/detail payloads by reviewing tests in `tests/unit/workflows/temporal/runtime/test_runtime_command_audit_events.py` and `tests/integration/api/test_runtime_command_historical_fidelity.py` FR-011 DESIGN-REQ-018
- [X] T041 Run full unit suite with `./tools/test_unit.sh` after focused tests pass
- [X] T042 Run hermetic integration suite with `./tools/test_integration.sh` after unit tests pass, or document the managed-environment blocker in `specs/357-preserve-slash-command-fidelity/quickstart.md` if Docker is unavailable
- [X] T043 Verify MM-687 and the original Jira preset brief remain preserved in `specs/357-preserve-slash-command-fidelity/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/slash-command-fidelity.md`, `quickstart.md`, `tasks.md`, implementation notes, commit text, and pull request metadata FR-013 SC-007
- [ ] T044 Run `/speckit.verify` against `specs/357-preserve-slash-command-fidelity/spec.md` after implementation and required tests pass, validating MM-687, the original preset brief, source design mappings, requirement coverage, and test evidence

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion; blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on story implementation and focused validation passing.

### Within The Story

- T009-T016 unit tests must be written before implementation tasks T024-T033.
- T018-T020 integration tests must be written before implementation tasks T024-T033.
- T017 and T021 are red-first confirmation gates and must complete before production code changes.
- T022-T023 determine conditional fallback handling for implemented-unverified rows before implementation proceeds.
- T024-T027 cover snapshot/edit/rerun foundations before detail and audit wiring.
- T028-T030 cover operator UI surfaces after data reconstruction is available.
- T031-T033 cover audit/observability and security guardrails.
- T034-T037 validate the complete story before polish.

### Parallel Opportunities

- T002-T004 can run in parallel.
- T006-T008 can run in parallel after T005.
- T009-T016 can run in parallel because they touch distinct test files.
- T018-T020 are sequential because they edit the same integration file.
- T024 and T027 can run in parallel if T027 is needed and edits remain isolated to `moonmind/workflows/tasks/task_contract.py`.
- T028/T029 and T031/T032 should be sequenced within their API/UI or runtime/audit boundaries to avoid conflicting assumptions.
- T038-T040 can run in parallel after story validation.

---

## Parallel Example: Story Test Authoring

```bash
Task: "Add failing draft reconstruction tests in frontend/src/lib/temporalTaskEditing.test.ts"
Task: "Add failing Task Detail display tests in frontend/src/entrypoints/task-detail.test.tsx"
Task: "Add failing audit event tests in tests/unit/workflows/temporal/runtime/test_runtime_command_audit_events.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Setup and Foundational tasks.
2. Write unit tests T009-T016 and confirm red-first failure with T017.
3. Write integration tests T018-T020 and confirm red-first failure with T021.
4. Evaluate conditional fallback tasks T022-T023.
5. Implement snapshot/edit/rerun preservation first, then Task Detail display, then audit events and sanitization.
6. Run focused unit and integration validations T034-T037.
7. Complete polish, full test commands, traceability verification, and `/speckit.verify`.

### Status Handling

- `missing` and `partial` rows receive red-first tests plus implementation tasks.
- `implemented_unverified` rows receive verification tests first and conditional fallback implementation only if those tests fail.
- No rows are marked `implemented_verified`; final validation keeps all MM-687 evidence traceable.

---

## Notes

- This task list covers one story only: `Audit Historical Slash Command Meaning`.
- Do not create new persistent storage unless implementation proves existing artifact/control-event surfaces cannot satisfy the contract; if that happens, update `plan.md` before implementing storage.
- Do not mutate historical source-run evidence to display current preview warnings.
- Preserve `MM-687` and the original Jira preset brief in all downstream evidence.
- Stop after tasks if running under the task-generation step; implementation belongs to `/speckit.implement`.
