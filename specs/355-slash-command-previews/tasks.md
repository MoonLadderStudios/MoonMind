# Tasks: Provider-Neutral Slash Command Previews

**Input**: Design documents from `/work/agent_jobs/mm:9f8378c1-5596-4d43-875b-8387e0bedb86/repo/specs/355-slash-command-previews/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/runtime-command-preview.md](./contracts/runtime-command-preview.md), [quickstart.md](./quickstart.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around exactly one story: `Preview Slash Command Intent`.

**Source Traceability**: `MM-685` and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-011, SCN-001 through SCN-005, SC-001 through SC-006, and in-scope DESIGN-REQ-001 through DESIGN-REQ-010.

**Requirement Status Summary**: Code-and-test work is required for FR-001 through FR-010. FR-011 is implemented_unverified traceability and is covered by preservation checks plus final `/moonspec-verify`.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/temporalTaskEditing.test.ts`
- Python unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/workflows/tasks/test_task_contract.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete tasks.
- Each task names exact file paths and requirement, scenario, success criterion, or source-design IDs.
- This task list covers one story only.

## Phase 1: Setup

**Purpose**: Confirm active artifacts and test tooling before test-first work.

- [X] T001 Confirm `specs/355-slash-command-previews/spec.md`, `specs/355-slash-command-previews/plan.md`, `specs/355-slash-command-previews/research.md`, `specs/355-slash-command-previews/data-model.md`, `specs/355-slash-command-previews/contracts/runtime-command-preview.md`, and `specs/355-slash-command-previews/quickstart.md` are present and preserve `MM-685` for FR-011/SC-006.
- [X] T002 Confirm existing frontend and Python test tooling commands in `package.json`, `./tools/test_unit.sh`, and `./tools/test_integration.sh` match the commands listed in `specs/355-slash-command-previews/quickstart.md`.

---

## Phase 2: Foundational

**Purpose**: Establish shared assumptions before writing story tests. No production story implementation occurs in this phase.

- [X] T003 Inspect existing backend runtime command normalization in `moonmind/workflows/tasks/task_contract.py` and existing coverage in `tests/unit/workflows/tasks/test_task_contract.py` to align frontend preview semantics for DESIGN-REQ-001 through DESIGN-REQ-005.
- [X] T004 Inspect existing Create page runtime, objective, step, and edit-mode state paths in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/lib/temporalTaskEditing.ts` to identify exact insertion points for FR-001, FR-004, and FR-009.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Preview Slash Command Intent

**Summary**: As a user composing a task, I want the Create page to preview whether slash-leading instructions will run as a runtime command, literal text, or an unsupported runtime case so that I can submit task instructions intentionally.

**Independent Test**: Compose task-level and step-level instructions that begin with known, unknown, unsupported, escaped, whitespace-prefixed, inline, and malformed slash text; switch runtimes; and open edit mode with stored command metadata to verify preview state while authored instructions remain unchanged.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010

**Unit Test Plan**:

- Frontend unit tests cover objective and step preview rendering, known hints, unknown opaque pass-through, unsupported runtimes, escaped literals, whitespace-prefixed text, path-like malformed text, runtime switching, no text mutation, and edit-mode preview restoration.
- Python unit tests cover browser-safe boot payload metadata and keep backend normalization traceability intact.

**Integration Test Plan**:

- API integration coverage verifies the Create page boot payload exposes browser-safe runtime command preview capability and hint metadata.
- Browser or task-create integration coverage verifies the preview journey from rendered Create page controls without relying on submit-time backend normalization.

### Unit Tests (write first)

- [X] T005 Add failing frontend unit tests for objective and step known command previews covering FR-001, SCN-001, SC-001, DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-007 in `frontend/src/entrypoints/task-create.test.tsx`.
- [X] T006 Add failing frontend unit tests for unknown valid `/foo` pass-through previews covering FR-003, SCN-002, SC-002, DESIGN-REQ-003, DESIGN-REQ-005, and DESIGN-REQ-007 in `frontend/src/entrypoints/task-create.test.tsx`.
- [X] T007 Add failing frontend unit tests for unsupported runtime warnings and runtime switching text preservation covering FR-004, SCN-003, SC-003, SC-005, DESIGN-REQ-006, DESIGN-REQ-007, and DESIGN-REQ-008 in `frontend/src/entrypoints/task-create.test.tsx`.
- [X] T008 Add failing frontend unit tests for escaped literals, whitespace-prefixed slash text, inline slash text, and path-like malformed slash text covering FR-005, FR-006, FR-007, SCN-004, SC-004, DESIGN-REQ-004, and DESIGN-REQ-010 in `frontend/src/entrypoints/task-create.test.tsx`.
- [X] T009 [P] Add failing frontend unit tests for edit-mode runtime command metadata reconstruction covering FR-009, SCN-005, DESIGN-REQ-009, and SC-006 in `frontend/src/lib/temporalTaskEditing.test.ts`.
- [X] T010 [P] Add failing Python unit tests for browser-safe runtime command preview boot metadata covering FR-002, FR-008, and DESIGN-REQ-006 in `tests/unit/api/routers/test_task_dashboard_view_model.py`.

### Integration Tests (write first)

- [X] T011 [P] Add failing API integration test for dashboard boot payload runtime command preview metadata covering FR-002, SCN-001, SCN-002, DESIGN-REQ-006, and the contract in `specs/355-slash-command-previews/contracts/runtime-command-preview.md` in `tests/integration/api/test_task_runtime_command_preview_boot_payload.py`.
- [X] T012 [P] Add failing Create page browser integration test for visible objective and step preview behavior covering FR-001, FR-003, FR-004, FR-005, SCN-001, SCN-002, SCN-003, SCN-004, and DESIGN-REQ-007 in `tests/e2e/test_task_create_submit_browser.py`.

### Red-First Confirmation

- [X] T013 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/temporalTaskEditing.test.ts` and confirm T005-T009 fail for missing preview behavior before production code changes.
- [X] T014 Run `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/workflows/tasks/test_task_contract.py` and confirm T010 fails for missing boot metadata while existing backend runtime command tests still pass.
- [ ] T015 Run `./tools/test_integration.sh` or the narrowest available integration command for `tests/integration/api/test_task_runtime_command_preview_boot_payload.py` and `tests/e2e/test_task_create_submit_browser.py`, then confirm T011-T012 fail for missing preview/metadata behavior.

### Conditional Fallback For Implemented-Unverified Traceability

- [ ] T016 If traceability checks fail, update `specs/355-slash-command-previews/spec.md`, `specs/355-slash-command-previews/plan.md`, `specs/355-slash-command-previews/research.md`, `specs/355-slash-command-previews/data-model.md`, `specs/355-slash-command-previews/contracts/runtime-command-preview.md`, `specs/355-slash-command-previews/quickstart.md`, and `specs/355-slash-command-previews/tasks.md` to preserve `MM-685` and the original Jira preset brief for FR-011/SC-006.

### Implementation

- [X] T017 Export a browser-safe runtime command preview capability and hint catalog aligned with backend normalization semantics for FR-002, FR-003, FR-008, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-005, and DESIGN-REQ-006 in `moonmind/workflows/tasks/task_contract.py`.
- [X] T018 Add runtime command preview metadata to the dashboard boot payload for FR-002, FR-008, DESIGN-REQ-006, and the contract in `api_service/api/routers/task_dashboard_view_model.py`.
- [X] T019 Add TaskCreatePage dashboard config typing for runtime command preview metadata covering FR-002 and DESIGN-REQ-006 in `frontend/src/entrypoints/task-create.tsx`.
- [X] T020 Implement provider-neutral runtime command preview derivation for detected, unknown, unsupported, escaped, whitespace-prefixed, inline, and path-like malformed states covering FR-001, FR-003, FR-005, FR-006, FR-007, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-010 in `frontend/src/entrypoints/task-create.tsx`.
- [X] T021 Render objective instruction runtime command preview status, hint text, pass-through text, literal text intent, and unsupported-runtime warning for FR-001, FR-002, FR-003, FR-005, FR-007, SCN-001, SCN-002, SCN-003, SCN-004, and DESIGN-REQ-007 in `frontend/src/entrypoints/task-create.tsx`.
- [X] T022 Render step instruction runtime command previews for FR-001, FR-002, FR-003, FR-005, FR-007, SCN-001, SCN-002, SCN-003, SCN-004, and DESIGN-REQ-007 in `frontend/src/entrypoints/task-create.tsx`.
- [X] T023 Wire runtime change recomputation without mutating authored instructions for FR-004, FR-008, SC-003, SC-005, DESIGN-REQ-001, and DESIGN-REQ-008 in `frontend/src/entrypoints/task-create.tsx`.
- [X] T024 Extend Temporal edit/rerun draft types and reconstruction to carry objective and step `runtimeCommand` metadata for preview-only restoration covering FR-009, SCN-005, DESIGN-REQ-009, and SC-006 in `frontend/src/lib/temporalTaskEditing.ts`.
- [X] T025 Ensure task submission payloads remain authored-instruction based and do not submit preview-only provider markup for FR-008, FR-009, DESIGN-REQ-001, and DESIGN-REQ-003 in `frontend/src/entrypoints/task-create.tsx`.
- [X] T026 Add or adjust minimal Create page styling for preview status, warning, and literal states while preserving mobile layout for FR-001, FR-004, FR-005, and SC-005 in `frontend/src/styles/mission-control.css`.

### Story Validation

- [X] T027 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/lib/temporalTaskEditing.test.ts` and verify all frontend preview unit tests pass for FR-001 through FR-010.
- [X] T028 Run `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/workflows/tasks/test_task_contract.py` and verify Python boot metadata and backend normalization tests pass for FR-002, FR-008, DESIGN-REQ-006, and DESIGN-REQ-010.
- [ ] T029 Run `./tools/test_integration.sh` and verify integration coverage passes for SCN-001 through SCN-005 and the runtime command preview contract.
- [ ] T030 Manually execute `specs/355-slash-command-previews/quickstart.md` Create page checks and record evidence for SC-001 through SC-006 in `specs/355-slash-command-previews/verification.md`.

**Checkpoint**: The single story is fully functional, covered by unit and integration tests, and independently validated.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T031 Review `frontend/src/entrypoints/task-create.tsx` and `frontend/src/lib/temporalTaskEditing.ts` for unnecessary duplication and refactor only within the preview helpers for FR-008 and DESIGN-REQ-001.
- [X] T032 [P] Review `frontend/src/styles/mission-control.css` preview styles for text overflow, mobile layout, and accessible contrast covering SC-005.
- [X] T033 Confirm no Codex-specific or Claude-specific command markup was added to Create page preview behavior for FR-008, DESIGN-REQ-001, and DESIGN-REQ-003 in `frontend/src/entrypoints/task-create.tsx`.
- [X] T034 Run `./tools/test_unit.sh` for the full required unit suite after focused tests pass.
- [ ] T035 Run `/moonspec-verify` for `specs/355-slash-command-previews` after implementation and tests pass, preserving `MM-685`, original preset brief, test evidence, and requirement coverage for FR-011/SC-006.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish And Verification (Phase 4)**: Depends on story implementation and validation passing.

### Within The Story

- T005-T010 unit tests and T011-T012 integration tests must be written before production implementation.
- T013-T015 red-first confirmation must complete before T017-T026 implementation.
- T016 is conditional and only runs if traceability verification fails.
- T017-T018 boot metadata work should precede T019-T023 Create page consumption.
- T024 edit/rerun metadata work can proceed after T009 is written and before T027 validation.
- T027-T030 story validation must pass before Phase 4.
- T035 final `/moonspec-verify` runs only after implementation and tests pass.

### Parallel Opportunities

- T005-T010 can be authored in parallel where each task touches a different file; tasks touching `frontend/src/entrypoints/task-create.test.tsx` need coordination.
- T011 and T012 can be authored in parallel because they touch different integration files.
- T017 and T018 should be sequential because boot payload metadata consumes the exported catalog.
- T020-T023 all touch `frontend/src/entrypoints/task-create.tsx` and should be coordinated sequentially.
- T031-T033 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Launch independent test authoring:
Task: "T009 Add failing temporalTaskEditing metadata tests in frontend/src/lib/temporalTaskEditing.test.ts"
Task: "T010 Add failing boot metadata tests in tests/unit/api/routers/test_task_dashboard_view_model.py"
Task: "T011 Add failing API integration test in tests/integration/api/test_task_runtime_command_preview_boot_payload.py"

# Launch independent polish reviews:
Task: "T032 Review preview styles in frontend/src/styles/mission-control.css"
Task: "T033 Confirm provider-neutral preview guardrails in frontend/src/entrypoints/task-create.tsx"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Setup and Foundational inspection tasks.
2. Write the frontend, Python unit, and integration tests in T005-T012.
3. Run T013-T015 and confirm red-first failures for missing preview behavior and metadata.
4. Implement the browser-safe catalog and boot metadata in T017-T018.
5. Implement Create page preview derivation, rendering, runtime recomputation, edit metadata restoration, payload guardrails, and minimal styles in T019-T026.
6. Run focused unit and integration validation in T027-T029.
7. Execute quickstart/manual story validation in T030.
8. Complete polish and final full-suite verification in T031-T035.

### Requirement Status Handling

- `missing` and `partial` rows from `plan.md` receive red-first tests and implementation tasks: FR-001 through FR-010, SCN-001 through SCN-005, SC-001 through SC-005, DESIGN-REQ-001 through DESIGN-REQ-010.
- `implemented_unverified` traceability rows receive validation and a conditional fallback artifact-update task: FR-011 and SC-006.
- No in-scope requirement is treated as already verified by current evidence.

---

## Notes

- Do not generate implementation code before red-first confirmation.
- Keep Create page preview provider-neutral; runtime-specific execution rendering remains outside this story.
- Keep backend submit-time normalization authoritative.
- Do not add new persistent storage.
- Preserve `MM-685` in implementation notes, verification output, commit text, and pull request metadata.
