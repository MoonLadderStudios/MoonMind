# Tasks: Inject Attachment Context Into Runtimes

**Input**: Design documents from `/specs/200-inject-attachment-context-into-runtimes/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style worker boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-372 story so runtime context injection remains independently testable.

**Source Traceability**: Tasks cover FR-001 through FR-012, acceptance scenarios 1-5, SC-001 through SC-006, and DESIGN-REQ-013, DESIGN-REQ-014, and DESIGN-REQ-020.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py tests/unit/agents/codex_worker/test_attachment_materialization.py`
- Integration tests: `./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py tests/unit/agents/codex_worker/test_attachment_materialization.py` (worker prepare/instruction boundary coverage in the required unit suite; no Docker-backed integration command is required because no workflow/activity or external service boundary changes)
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Verify target files and preserve MM-372 traceability.

- [X] T001 Verify existing worker instruction composition coverage in `tests/unit/agents/codex_worker/test_worker.py` and target implementation surface in `moonmind/agents/codex_worker/worker.py` (FR-001, DESIGN-REQ-013)
- [X] T002 Confirm `.specify/feature.json` points to `specs/200-inject-attachment-context-into-runtimes` and MM-372 is preserved in `specs/200-inject-attachment-context-into-runtimes/spec.md` (FR-012, SC-006)

---

## Phase 2: Foundational

**Purpose**: Establish prepared artifact parsing and target filtering before story behavior.

- [X] T003 Define worker helper contract in `moonmind/agents/codex_worker/worker.py` for reading prepared attachment manifest entries, reading optional vision context index entries, selecting objective/current-step entries, and rendering prompt-safe attachment text (FR-001-FR-011)

**Checkpoint**: Helper surface identified; story tests and implementation can begin.

---

## Phase 3: Story - Inject Target-Scoped Attachment Context

**Summary**: As a runtime adapter, I need text-first, planning, and multimodal runtime paths to receive only the attachment context appropriate to the current execution target.

**Independent Test**: Compose a runtime step instruction from a prepared workspace containing objective and multiple step attachment entries, then verify the injected block appears before `WORKSPACE`, includes objective and current-step context, excludes non-current step context, and never embeds raw bytes or data URLs.

**Traceability**: FR-001-FR-012; SC-001-SC-006; acceptance scenarios 1-5; DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020

**Test Plan**:

- Unit: prompt ordering, manifest path inclusion, objective/current-step filtering, generated context path matching, compact planning inventory, raw-byte/data-url guardrails, and absent-manifest behavior.
- Integration: worker prepare/instruction boundary coverage proves prepared manifest and generated context index artifacts flow into runtime instruction composition without crossing external service boundaries.

### Unit Tests (write first)

- [X] T004 [P] Add failing unit test for `INPUT ATTACHMENTS` ordering and objective/current-step manifest/context inclusion in `tests/unit/agents/codex_worker/test_worker.py` (FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SC-001, SC-002, DESIGN-REQ-013)
- [X] T005 [P] Add failing unit test proving non-current step workspace paths and context paths are omitted from step instructions in `tests/unit/agents/codex_worker/test_worker.py` (FR-007, SC-003, DESIGN-REQ-014)
- [X] T006 [P] Add failing unit test for compact planning attachment inventory in `tests/unit/agents/codex_worker/test_worker.py` (FR-008, SC-004, DESIGN-REQ-014)
- [X] T007 [P] Add failing unit test for absent manifest and raw-byte/data-URL guardrails in `tests/unit/agents/codex_worker/test_worker.py` (FR-010, FR-011, SC-005, DESIGN-REQ-020)

### Integration Tests (write first)

- [X] T008 Add failing integration-style worker boundary test proving prepared `.moonmind/attachments_manifest.json` and `.moonmind/vision/image_context_index.json` entries flow into runtime instruction composition while preserving target boundaries in `tests/unit/agents/codex_worker/test_worker.py` (acceptance scenarios 1-4, FR-001-FR-009, DESIGN-REQ-013, DESIGN-REQ-014)

### Red-First Confirmation

- [X] T009 Run `./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py tests/unit/agents/codex_worker/test_attachment_materialization.py` and confirm the new tests fail for missing injection behavior before production changes (T004-T008)

### Implementation

- [X] T010 Implement prepared attachment manifest and vision index readers in `moonmind/agents/codex_worker/worker.py` (FR-002, FR-004)
- [X] T011 Implement objective/current-step filtering and compact planning inventory helpers in `moonmind/agents/codex_worker/worker.py` (FR-005, FR-006, FR-007, FR-008, DESIGN-REQ-014)
- [X] T012 Implement prompt-safe `INPUT ATTACHMENTS` rendering and inject it before `WORKSPACE` in `moonmind/agents/codex_worker/worker.py` (FR-001, FR-003, FR-010, FR-011, DESIGN-REQ-013, DESIGN-REQ-020)
- [X] T013 Preserve multimodal adapter metadata semantics by keeping generated helper output metadata-only and source-ref preserving in `moonmind/agents/codex_worker/worker.py` (FR-009, DESIGN-REQ-020)

### Story Validation

- [X] T014 Run focused unit command and fix failures until the story passes: `./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py tests/unit/agents/codex_worker/test_attachment_materialization.py` (SC-001-SC-005)
- [X] T015 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification or document the exact blocker (FR-001-FR-012)

**Checkpoint**: The MM-372 story is fully functional, covered by focused worker tests, and testable independently.

---

## Phase 4: Polish & Verification

- [X] T016 [P] Review `specs/200-inject-attachment-context-into-runtimes/quickstart.md` against implemented commands and update only if command evidence changes (SC-006)
- [X] T017 Create `specs/200-inject-attachment-context-into-runtimes/verification.md` with implementation evidence, test results, MM-372 traceability, and `/moonspec-verify` verdict (FR-012, SC-006)
- [X] T018 Run final `/moonspec-verify` equivalent against `specs/200-inject-attachment-context-into-runtimes/spec.md` and preserve MM-372 in verification output (FR-012)

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): No dependencies.
- Foundational (Phase 2): Depends on Setup completion.
- Story (Phase 3): Depends on Foundational completion.
- Polish & Verification (Phase 4): Depends on story validation.

### Within The Story

- Unit tests T004-T007 and integration-style boundary test T008 must be written before implementation.
- Red-first confirmation T009 must happen before production implementation tasks T010-T013.
- Manifest/index readers T010 precede filtering/rendering T011-T012.
- Story validation T014-T015 follows implementation.

### Parallel Opportunities

- T004-T008 cover related behaviors in one test file, so apply sequentially to avoid edit conflicts.
- T016 can run after story tests pass.

---

## Implementation Strategy

1. Add tests describing target-scoped prompt injection in the existing worker test file.
2. Confirm focused tests fail before production changes.
3. Implement small helper functions in `moonmind/agents/codex_worker/worker.py`.
4. Inject the rendered block before `WORKSPACE` in step instruction composition.
5. Run focused unit validation and then full unit validation.
6. Run final MoonSpec verification and record evidence.
