# Tasks: Generate Target-Aware Vision Context Artifacts

**Input**: Design documents from `/specs/197-generate-target-aware-vision-context/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: DESIGN-REQ-012 maps to FR-001 through FR-010, SC-001 through SC-005, and acceptance scenarios 1 through 5.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/moonmind/vision/test_service.py`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/vision/test_context_artifacts.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing service/test structure for the story.

- [X] T001 Confirm existing vision service structure in `moonmind/vision/service.py` and unit test location in `tests/unit/moonmind/vision/test_service.py`
- [X] T002 Create MoonSpec feature artifacts in `specs/197-generate-target-aware-vision-context/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new global infrastructure is required; the story builds on the existing vision service and settings.

- [X] T003 Confirm existing `VisionConfig` exposes runtime enable/provider/model/OCR controls in `moonmind/vision/settings.py` for FR-006
- [X] T004 Confirm existing `AttachmentContextInput` preserves source attachment refs and local paths in `moonmind/vision/service.py` for FR-005

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Generate Target-Aware Vision Context Artifacts

**Summary**: As a text-first runtime user, I need deterministic image-derived context artifacts that remain traceable to source image refs and preserve objective versus step target meaning.

**Independent Test**: Generate context for objective and step image targets into a temporary workspace and verify Markdown files plus index entries preserve paths, statuses, target bindings, and source attachment refs.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-012

**Test Plan**:

- Unit: target path mapping, disabled/provider-unavailable statuses, source traceability, stable step ref sanitization, index payload determinism.
- Integration: filesystem output paths for objective and step targets plus index JSON under `.moonmind/vision/`.

### Unit Tests (write first) ⚠️

- [X] T005 [P] Add failing unit test for objective and step target index generation covering FR-001, FR-002, FR-003, FR-004, SC-001, SC-002, DESIGN-REQ-012 in `tests/unit/moonmind/vision/test_service.py`
- [X] T006 [P] Add failing unit test for disabled generation status and source traceability covering FR-005, FR-006, FR-007, SC-003, SC-004 in `tests/unit/moonmind/vision/test_service.py`
- [X] T007 [P] Add failing unit test for same-filename target separation and step ref path safety covering FR-009, FR-010, SC-005 in `tests/unit/moonmind/vision/test_service.py`
- [X] T008 Run `./tools/test_unit.sh tests/unit/moonmind/vision/test_service.py` to confirm T005-T007 fail for the expected reason

### Integration Tests (write first) ⚠️

- [X] T009 [P] Add failing filesystem integration test for generated Markdown and index artifacts covering acceptance scenarios 1, 2, 4, and 5 in `tests/integration/vision/test_context_artifacts.py`
- [X] T010 Run `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/vision/test_context_artifacts.py -q` to confirm T009 fails for the expected reason

### Implementation

- [X] T011 Add target-aware dataclasses and path/index helpers for FR-001, FR-004, FR-009, FR-010 in `moonmind/vision/service.py`
- [X] T012 Implement per-target Markdown rendering and artifact writing for FR-002, FR-003, FR-005, FR-008 in `moonmind/vision/service.py`
- [X] T013 Export target-aware vision context types for runtime callers in `moonmind/vision/__init__.py`
- [X] T014 Run focused unit and integration commands, fix failures, and complete story validation for FR-001 through FR-010 in `specs/197-generate-target-aware-vision-context/quickstart.md`

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen completed story without changing core scope.

- [X] T015 Run quickstart validation commands from `specs/197-generate-target-aware-vision-context/quickstart.md`
- [X] T016 Run `/moonspec-verify` read-only verification against MM-371 source requirements and implemented evidence

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on focused tests passing

### Within The Story

- Unit tests are written and confirmed failing before production code.
- Integration tests are written and confirmed failing before production code.
- Target dataclasses and path helpers precede artifact writing.
- Exports follow production implementation.
- Quickstart and verification follow passing focused tests.

### Parallel Opportunities

- T005, T006, T007 can be authored in parallel within one unit test file if edits are coordinated.
- T009 can be authored independently from unit test edits.

---

## Implementation Strategy

1. Complete setup and foundational confirmation.
2. Write failing unit and integration tests for target-aware generation.
3. Implement target-aware service models, deterministic paths, index payload, and file writing.
4. Export the new service types.
5. Run focused unit and integration validation.
6. Run final unit verification where feasible.
7. Perform `/moonspec-verify` coverage check against MM-371 and DESIGN-REQ-012.
