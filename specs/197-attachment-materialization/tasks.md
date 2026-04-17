# Tasks: Materialize Attachment Manifest and Workspace Files

**Input**: Design documents from `/specs/197-attachment-materialization/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style worker boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-370 story so prepare-time materialization remains independently testable.

**Source Traceability**: Tasks cover FR-001 through FR-012, acceptance scenarios 1-5, edge cases, SC-001 through SC-007, and DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-011.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py`
- Integration tests: `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Verify target files and testing location.

- [X] T001 Verify existing worker prepare stage in `moonmind/agents/codex_worker/worker.py` and create focused test file `tests/unit/agents/codex_worker/test_attachment_materialization.py` if absent (FR-001, DESIGN-REQ-004)
- [X] T002 Confirm `.specify/feature.json` points to `specs/197-attachment-materialization` and MM-370 is preserved in `specs/197-attachment-materialization/spec.md` (FR-012, SC-007)

---

## Phase 2: Foundational

**Purpose**: Establish the prepare materialization helper contract before story behavior.

- [X] T003 Define the attachment materialization helper surface in `moonmind/agents/codex_worker/worker.py` for collecting objective/step refs, deriving stable step refs, sanitizing filenames, computing workspace paths, downloading bytes, and writing manifest entries (FR-001-FR-010)

**Checkpoint**: Helper surface identified; story tests and implementation can begin.

---

## Phase 3: Story - Materialize Attachment Inputs During Prepare

**Summary**: As a runtime executor, I need workflow prepare to deterministically download declared input attachments, write a canonical manifest, and place files in target-aware workspace paths before the relevant runtime or step executes.

**Independent Test**: Start a task-shaped execution payload containing objective-scoped and step-scoped input attachment refs, including a step without an explicit id, and verify prepare downloads all declared attachments, writes `.moonmind/attachments_manifest.json`, materializes files under deterministic target-aware workspace paths, and fails explicitly if any attachment cannot be materialized.

**Traceability**: FR-001-FR-012; SC-001-SC-007; acceptance scenarios 1-5; DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-011

**Test Plan**:

- Unit: ref collection, filename sanitization, stable step refs, deterministic workspace paths, manifest shape, and failure behavior.
- Integration-style boundary: `_run_prepare_stage` writes `.moonmind/attachments_manifest.json` and materialized files before returning `PreparedTaskWorkspace`.

### Unit Tests (write first) ⚠️

- [X] T004 [P] Add failing unit tests for objective and step ref collection plus canonical manifest fields in `tests/unit/agents/codex_worker/test_attachment_materialization.py` (FR-001, FR-002, FR-003, FR-004, SC-001, SC-002, DESIGN-REQ-002)
- [X] T005 [P] Add failing unit tests for deterministic workspace paths, unsafe filename sanitization, repeated filenames, and unrelated target ordering in `tests/unit/agents/codex_worker/test_attachment_materialization.py` (FR-005, FR-006, FR-007, FR-008, SC-004, SC-005, DESIGN-REQ-011)
- [X] T006 [P] Add failing unit tests for stable fallback step references when step ids are absent in `tests/unit/agents/codex_worker/test_attachment_materialization.py` (FR-009, SC-003, DESIGN-REQ-011)
- [X] T007 [P] Add failing unit test for explicit prepare failure on download failure or malformed refs in `tests/unit/agents/codex_worker/test_attachment_materialization.py` (FR-010, SC-006, acceptance scenario 5)

### Integration Tests (write first) ⚠️

- [X] T008 Add failing worker prepare boundary test proving `_run_prepare_stage` writes `.moonmind/attachments_manifest.json`, objective files, and step files before returning in `tests/unit/agents/codex_worker/test_attachment_materialization.py` (FR-001-FR-006, SC-001, SC-002, DESIGN-REQ-004)

### Red-First Confirmation ⚠️

- [X] T009 Run `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py` and confirm the new tests fail for missing materialization behavior before production changes (T004-T008)

### Implementation

- [X] T010 Implement attachment ref collection and stable target derivation in `moonmind/agents/codex_worker/worker.py` (FR-001, FR-004, FR-009, DESIGN-REQ-002)
- [X] T011 Implement filename sanitization and deterministic workspace path generation in `moonmind/agents/codex_worker/worker.py` (FR-005, FR-006, FR-007, FR-008, DESIGN-REQ-011)
- [X] T012 Implement artifact download and local file writing through the worker's trusted API client in `moonmind/agents/codex_worker/worker.py` (FR-001, FR-005, FR-006, DESIGN-REQ-004)
- [X] T013 Implement `.moonmind/attachments_manifest.json` writing with canonical manifest entries in `moonmind/agents/codex_worker/worker.py` (FR-002, FR-003, SC-001, SC-002)
- [X] T014 Implement explicit materialization failure handling and prepare-stage diagnostics in `moonmind/agents/codex_worker/worker.py` (FR-010, SC-006)
- [X] T015 Wire materialization into `_run_prepare_stage` before `task_context.json` completion and include manifest path metadata without embedding bytes in `moonmind/agents/codex_worker/worker.py` (FR-011, acceptance scenarios 1-4)

### Story Validation

- [X] T016 Run `./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py` and fix failures until the story passes (SC-001-SC-006)
- [X] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification or document the exact blocker (FR-001-FR-012)

**Checkpoint**: The MM-370 story is fully functional, covered by unit and worker boundary tests, and testable independently.

---

## Phase 4: Polish & Verification

- [X] T018 [P] Review `specs/197-attachment-materialization/quickstart.md` against the implemented commands and update only if command evidence changes (SC-007)
- [X] T019 Create `specs/197-attachment-materialization/verification.md` with implementation evidence, test results, MM-370 traceability, and `/moonspec-verify` verdict (FR-012, SC-007)
- [X] T020 Run final `/moonspec-verify` equivalent against `specs/197-attachment-materialization/spec.md` and preserve MM-370 in verification output (FR-012)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish & Verification (Phase 4)**: Depends on story validation.

### Within The Story

- Unit and boundary tests (T004-T008) must be written before implementation.
- Red-first confirmation (T009) must happen before production implementation tasks T010-T015.
- Ref collection and path helpers (T010-T011) precede download and manifest wiring (T012-T015).
- Story validation (T016-T017) follows implementation.

### Parallel Opportunities

- T004-T007 can be authored in parallel because they cover independent behaviors in one test file but should be applied sequentially to avoid edit conflicts.
- T018 can run in parallel with verification drafting only after story tests pass.

---

## Implementation Strategy

1. Add tests describing the target contract in the focused worker test file.
2. Confirm tests fail before production changes.
3. Implement small helper functions in `moonmind/agents/codex_worker/worker.py` and wire them into `_run_prepare_stage`.
4. Run focused tests until passing.
5. Run full unit verification, then document final verification evidence.
