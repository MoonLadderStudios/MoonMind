---
description: "Task list template for single-story MoonSpec implementation"
---

# Tasks: [FEATURE NAME]

**Input**: Design documents from `/specs/[###-feature-name]/`
**Prerequisites**: plan.md (required), spec.md (required for the story), research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Each task MUST reference the relevant stable claim IDs from `spec.md` (`CLAIM-*`, `DESIGN-REQ-*`, `DOC-REQ-*`), plus `FR-*`, acceptance scenario, or success criterion IDs when applicable. If no stable claim applies, the task MUST state `No source claim applies: <reason>`.

**Source Packet**: Carry forward `spec.md` source document path, viewpoint, owning surface, stable claim IDs, and temporary-adapter role. Preserve source issue traceability such as `MM-933` and `MM-927` when present.

**Test Commands**:

- Unit tests: `[UNIT TEST COMMAND]`
- Integration tests: `[INTEGRATION TEST COMMAND]`
- Final verification: `/moonspec.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or stable source claim IDs when the task implements or validates behavior
- Include `No source claim applies: <reason>` when a setup, validation, or polish task has no applicable source claim

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root
- **Web app**: `backend/src/`, `frontend/src/`
- **Mobile**: `api/src/`, `ios/src/` or `android/src/`
- Paths shown below assume single project - adjust based on plan.md structure

<!--
  ============================================================================
  IMPORTANT: The tasks below are SAMPLE TASKS for illustration purposes only.

  The /moonspec.tasks command MUST replace these with actual tasks based on:
  - The single user story from spec.md
  - The original request/design preserved in spec.md Input
  - Stable source claim mappings such as CLAIM-*, DESIGN-REQ-* or DOC-REQ-*
  - Feature requirements from plan.md
  - Entities from data-model.md
  - Endpoints from contracts/

  Tasks MUST be organized so the story can be:
  - Implemented with a test-first workflow
  - Covered by both unit tests and integration tests
  - Traced back to source requirements
  - Tested independently
  - Delivered as a complete vertical slice

  DO NOT keep these sample tasks in the generated tasks.md file.
  ============================================================================
-->

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project structure in src/ and tests/ per implementation plan (No source claim applies: setup task)
- [ ] T002 Initialize [language] project with [framework] dependencies in [config file] (No source claim applies: setup task)
- [ ] T003 [P] Configure linting and formatting tools in [config file] (No source claim applies: setup task)
- [ ] T004 [P] Configure unit test tooling for `[UNIT TEST COMMAND]` in [test config file] (No source claim applies: setup task)
- [ ] T005 [P] Configure integration test tooling for `[INTEGRATION TEST COMMAND]` in [test config file] (No source claim applies: setup task)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before story implementation can begin

**⚠️ CRITICAL**: No story implementation work can begin until this phase is complete

Examples of foundational tasks (include only what the story truly depends on):

- [ ] T006 Setup database schema and migrations framework in [schema/migrations path] (No source claim applies: foundational infrastructure task)
- [ ] T007 [P] Implement authentication/authorization framework in src/[auth path] (No source claim applies: foundational infrastructure task)
- [ ] T008 [P] Setup API routing and middleware structure in src/[routing path] (No source claim applies: foundational infrastructure task)
- [ ] T009 Create base models/entities that the story depends on in src/models/ (No source claim applies: foundational infrastructure task)
- [ ] T010 Configure error handling and logging infrastructure in src/[infrastructure path] (No source claim applies: foundational infrastructure task)
- [ ] T011 Setup environment configuration management in [config path] (No source claim applies: foundational infrastructure task)
- [ ] T012 Configure integration test fixtures, service containers, or emulators needed by SCN-* in tests/integration/ (No source claim applies: foundational infrastructure task)

**Checkpoint**: Foundation ready - story test and implementation work can now begin

---

## Phase 3: Story - [Brief Title]

**Summary**: [As a < WHO >, I want < WHAT > so that < WHY >]

**Independent Test**: [How to verify this story works on its own]

**Traceability**: [FR-001, FR-002, SCN-001, SC-001, CLAIM-001, DESIGN-REQ-001]

**Source Packet**:

- Source document: [docs/path.md or original request only]
- Viewpoint: [viewpoint or none]
- Owning surface: [surface or none]
- Stable claim IDs: [CLAIM-001, DESIGN-REQ-001, DOC-REQ-001, or none with reason]

**Test Plan**:

- Unit: [domain rules, validation, edge cases, failure modes]
- Integration: [acceptance scenarios, contracts, persistence, external interfaces, workflows]

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [ ] T013 [P] Add failing unit test for [domain behavior] covering CLAIM-001 / FR-001 in tests/unit/test_[name].py
- [ ] T014 [P] Add failing unit test for [edge case] covering CLAIM-002 / FR-002 in tests/unit/test_[name].py
- [ ] T015 Run `[UNIT TEST COMMAND]` for tests/unit/test_[name].py to confirm T013-T014 fail for the expected reason

### Integration Tests (write first) ⚠️

- [ ] T016 [P] Add failing integration test for [user journey] covering CLAIM-001 / SCN-001 in tests/integration/test_[name].py
- [ ] T017 [P] Add failing integration test for [system interaction or contract] covering DESIGN-REQ-001 in tests/integration/test_[name].py
- [ ] T018 Run `[INTEGRATION TEST COMMAND]` for tests/integration/test_[name].py to confirm T016-T017 fail for the expected reason

### Implementation

- [ ] T019 [P] Create or update [Entity1] for CLAIM-001 / FR-001 in src/models/[entity1].py
- [ ] T020 [P] Create or update [Entity2] for CLAIM-002 / FR-002 in src/models/[entity2].py
- [ ] T021 Implement [Service] for CLAIM-001 / CLAIM-002 / FR-001 / FR-002 in src/services/[service].py (depends on T019, T020)
- [ ] T022 Implement [endpoint/feature/UI/CLI] for CLAIM-001 / SCN-001 in src/[location]/[file].py
- [ ] T023 Add validation, error handling, and non-goal guardrails for CLAIM-003 / FR-003 in src/[location]/[file].py
- [ ] T024 Wire integration boundary for DESIGN-REQ-001 in src/[location]/[file].py
- [ ] T025 Add logging/observability for the story flow in src/[location]/[file].py (No source claim applies: operational support task)
- [ ] T026 Run `[UNIT TEST COMMAND]` and `[INTEGRATION TEST COMMAND]` for tests/unit/ and tests/integration/, fix failures, and verify the story passes end-to-end (No source claim applies: validation task)

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that strengthen the completed story without changing its core scope

- [ ] TXXX [P] Documentation updates in docs/ (No source claim applies: optional polish task)
- [ ] TXXX Code cleanup and refactoring (No source claim applies: optional polish task)
- [ ] TXXX Performance optimization for the story path covering CLAIM-001 in src/[location]/[file].py
- [ ] TXXX [P] Expand unit test edge-case coverage for CLAIM-002 in tests/unit/
- [ ] TXXX [P] Expand integration coverage for operational scenarios covering DESIGN-REQ-001 in tests/integration/
- [ ] TXXX Security hardening for CLAIM-003 in src/[location]/[file].py
- [ ] TXXX Run quickstart.md validation (No source claim applies: validation task)
- [ ] TXXX Run `/moonspec.verify` to validate the final implementation against the original feature request and Source Packet claims (No source claim applies: validation task)
- [ ] TXXX Run `moonspec-doc-reconcile` for the canonical source document when `spec.md` names a canonical source under docs/ (No source claim applies: documentation reconciliation task)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing

### Within The Story

- Unit tests MUST be written and FAIL before implementation
- Integration tests MUST be written and FAIL before implementation
- Red-first confirmation tasks MUST complete before production code tasks
- Models before services
- Services before endpoints
- Contracts and public boundaries before integration wiring
- Core implementation before full story validation
- Story complete before polish work

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, unit and integration test authoring can start in parallel
- Test tasks marked [P] can run in parallel when they touch different files
- Model tasks marked [P] can run in parallel
- Polish tasks marked [P] can run in parallel

---

## Parallel Example: Story Phase

```bash
# Launch story test authoring together:
Task: "Add failing unit test for [domain behavior] in tests/unit/test_[name].py"
Task: "Add failing integration test for [user journey] in tests/integration/test_[name].py"

# Launch model work together:
Task: "Create [Entity1] model in src/models/[entity1].py"
Task: "Create [Entity2] model in src/models/[entity2].py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks story work)
3. Build the story traceability inventory from `spec.md`
4. Write unit tests for the story and confirm they fail
5. Write integration tests for the story and confirm they fail
6. Implement the story until all new tests pass
7. Validate the story independently with unit, integration, and quickstart checks
8. Complete Phase 4: Polish and final verification with `/moonspec.verify`

---

## Notes

- [P] tasks = different files, no dependencies
- The task list should cover one story only
- The story should be independently completable and testable
- Each in-scope requirement, scenario, success criterion, and stable source claim mapping must have task coverage
- Each task must map to stable claim IDs or state `No source claim applies: <reason>`
- Verify unit and integration tests fail before implementing
- Run `/moonspec.verify` after implementation to check the final result against the original request
- Commit after each task or logical group
- Stop at the checkpoint to validate the story independently
- Avoid: vague tasks, optional testing, same file conflicts, multi-story phases, or hidden scope beyond the story
