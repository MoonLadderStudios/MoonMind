# Tasks: Protect Image Access and Untrusted Content Boundaries

**Input**: Design documents from `/specs/203-protect-image-access/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-374 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-374, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-020.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/test_artifact_authorization.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/agents/codex_worker/test_worker.py`
- UI tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Integration tests: `pytest tests/integration/vision/test_context_artifacts.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing artifact, UI, worker, vision, and task-contract boundaries are the implementation surfaces.

- [X] T001 Confirm source traceability for MM-374 and DESIGN-REQ-016/DESIGN-REQ-017/DESIGN-REQ-020 in `specs/203-protect-image-access/spec.md` and `spec.md` (Input) (FR-013, SC-007)
- [X] T002 Inspect existing artifact authorization, task-detail download rendering, worker materialization, vision context, and task contract test locations in `tests/unit/workflows/temporal/test_artifact_authorization.py`, `frontend/src/entrypoints/task-detail.test.tsx`, `tests/unit/agents/codex_worker/test_worker.py`, `tests/integration/vision/test_context_artifacts.py`, and `tests/unit/workflows/tasks/test_task_contract.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish failing evidence for the one security hardening gap without changing application code first.

- [X] T003 [P] Add focused worker prompt test proving runtime attachment notices warn that extracted image text is not system, developer, or task instructions in `tests/unit/agents/codex_worker/test_worker.py` (FR-005, FR-006, SC-004, DESIGN-REQ-016)
- [X] T004 [P] Add focused vision context test proving hostile OCR text remains under an untrusted derived-data warning in `tests/integration/vision/test_context_artifacts.py` (FR-004, FR-006, SC-004, DESIGN-REQ-016)
- [X] T005 Run `./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py` and `pytest tests/integration/vision/test_context_artifacts.py -q` to confirm T003-T004 fail for the expected missing explicit warning

**Checkpoint**: Red tests prove the untrusted extracted-text instruction boundary needs hardening.

---

## Phase 3: Story - Secure Image Access Boundaries

**Summary**: As a security-conscious operator, I need image access and extracted text handling to enforce execution ownership, short-lived browser access, service-credential worker access, exact refs, and untrusted-content boundaries.

**Independent Test**: Exercise artifact preview/download authorization, browser-visible download links, worker attachment context, vision context with hostile OCR text, and task attachment ref validation.

**Traceability**: FR-001 through FR-013, SC-001 through SC-007, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-020

**Test Plan**:

- Unit: artifact authorization, task contract ref validation, worker prompt warnings and data URL omission.
- UI: task-detail target-aware image links prefer MoonMind endpoints over external URLs.
- Integration: vision context artifacts label OCR/caption content as untrusted.

### Unit Tests (write first)

- [X] T006 Confirm existing restricted artifact authorization coverage in `tests/unit/workflows/temporal/test_artifact_authorization.py` maps to FR-001 and SC-001
- [X] T007 Confirm existing task contract embedded image/data URL rejection coverage in `tests/unit/workflows/tasks/test_task_contract.py` maps to FR-010 and SC-005
- [X] T008 Confirm existing worker materialization and prompt data URL omission coverage in `tests/unit/agents/codex_worker/test_attachment_materialization.py` and `tests/unit/agents/codex_worker/test_worker.py` maps to FR-003, FR-008, FR-009, SC-003, SC-005, and SC-006

### UI Tests (write first)

- [X] T009 Confirm existing target-aware task image rendering coverage in `frontend/src/entrypoints/task-detail.test.tsx` proves MoonMind endpoint preference for task image downloads and maps to FR-002, FR-007, and SC-002

### Integration Tests (write first)

- [X] T010 Confirm or update vision integration coverage in `tests/integration/vision/test_context_artifacts.py` for untrusted image-derived text warnings (FR-004, FR-006, SC-004)

### Implementation

- [X] T011 Harden worker `INPUT ATTACHMENTS` safety notice in `moonmind/agents/codex_worker/worker.py` so extracted image text is never framed as system, developer, or task instructions unless explicitly authored (FR-005, FR-006, DESIGN-REQ-016)
- [X] T012 Harden vision markdown safety notice in `moonmind/vision/service.py` so OCR/caption text is explicitly untrusted and non-executable by default (FR-004, FR-006, DESIGN-REQ-016)
- [X] T013 Run focused unit and integration commands, fix failures, and confirm the story passes independently

**Checkpoint**: The story is fully functional, covered by unit/UI/integration evidence, and testable independently.

---

## Phase 4: Polish & Final Verification

**Purpose**: Validate traceability and complete MoonSpec verification.

- [X] T014 Run source traceability check: `rg -n "MM-374|DESIGN-REQ-016|DESIGN-REQ-017|DESIGN-REQ-020" specs/203-protect-image-access` (FR-013, SC-007)
- [X] T015 Run full unit suite with `./tools/test_unit.sh` or document exact blocker
- [X] T016 Run hermetic integration suite with `./tools/test_integration.sh` when Docker is available or document exact blocker
- [X] T017 Run `/moonspec-verify` equivalent and record final verification evidence for MM-374

---

## Dependencies & Execution Order

- Phase 1 must complete before test confirmation.
- T003 and T004 can be authored in parallel because they touch different files.
- T005 must run before T011-T012 implementation.
- T011 and T012 can be implemented independently after red tests exist.
- T013 must pass before final verification.
- T014-T017 complete the final MoonSpec evidence.

## Implementation Strategy

1. Preserve MM-374 and source design mappings in all artifacts.
2. Confirm existing authorization, UI endpoint, materialization, and ref-validation evidence.
3. Add red tests for the explicit extracted-text instruction boundary.
4. Harden the worker and vision safety notices.
5. Run focused validation, then broader validation where feasible.
6. Run final `/moonspec-verify` equivalent against the preserved MM-374 input.
