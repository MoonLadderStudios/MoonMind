---

description: "Task list template for feature implementation"
---

# Tasks: [FEATURE NAME]

**Input**: Design documents from `/specs/[###-feature-name]/`
**Prerequisites**: plan.md (required), spec.md (required for the story), research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root
- **Web app**: `backend/src/`, `frontend/src/`
- **Mobile**: `api/src/`, `ios/src/` or `android/src/`
- Paths shown below assume single project - adjust based on plan.md structure

<!--
  ============================================================================
  IMPORTANT: The tasks below are SAMPLE TASKS for illustration purposes only.

  The /speckit.tasks command MUST replace these with actual tasks based on:
  - The single user story from spec.md
  - Feature requirements from plan.md
  - Entities from data-model.md
  - Endpoints from contracts/

  Tasks MUST be organized so the story can be:
  - Implemented with a test-first workflow
  - Tested independently
  - Delivered as a complete vertical slice

  DO NOT keep these sample tasks in the generated tasks.md file.
  ============================================================================
-->

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project structure per implementation plan
- [ ] T002 Initialize [language] project with [framework] dependencies
- [ ] T003 [P] Configure linting and formatting tools

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before story implementation can begin

**⚠️ CRITICAL**: No story implementation work can begin until this phase is complete

Examples of foundational tasks (include only what the story truly depends on):

- [ ] T004 Setup database schema and migrations framework
- [ ] T005 [P] Implement authentication/authorization framework
- [ ] T006 [P] Setup API routing and middleware structure
- [ ] T007 Create base models/entities that the story depends on
- [ ] T008 Configure error handling and logging infrastructure
- [ ] T009 Setup environment configuration management

**Checkpoint**: Foundation ready - story test and implementation work can now begin

---

## Phase 3: Story - [Brief Title]

**Summary**: [As a < WHO >, I want < WHAT > so that < WHY >]

**Independent Test**: [How to verify this story works on its own]

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [ ] T010 [P] Add failing unit test for [domain behavior] in tests/unit/test_[name].py
- [ ] T011 [P] Add failing unit test for [service or edge case] in tests/unit/test_[name].py

### Integration Tests (write first) ⚠️

- [ ] T012 [P] Add failing integration test for [user journey] in tests/integration/test_[name].py
- [ ] T013 [P] Add failing integration test for [system interaction] in tests/integration/test_[name].py

### Implementation

- [ ] T014 [P] Create or update [Entity1] in src/models/[entity1].py
- [ ] T015 [P] Create or update [Entity2] in src/models/[entity2].py
- [ ] T016 Implement [Service] in src/services/[service].py (depends on T014, T015)
- [ ] T017 Implement [endpoint/feature] in src/[location]/[file].py
- [ ] T018 Add validation and error handling in src/[location]/[file].py
- [ ] T019 Add logging/observability for the story flow
- [ ] T020 Run unit and integration test suites, fix failures, and verify the story passes end-to-end

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that strengthen the completed story without changing its core scope

- [ ] TXXX [P] Documentation updates in docs/
- [ ] TXXX Code cleanup and refactoring
- [ ] TXXX Performance optimization for the story path
- [ ] TXXX [P] Expand unit test edge-case coverage in tests/unit/
- [ ] TXXX [P] Expand integration coverage for operational scenarios in tests/integration/
- [ ] TXXX Security hardening
- [ ] TXXX Run quickstart.md validation
- [ ] TXXX Run `/speckit.verify` to validate the final implementation against the original feature request

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
- Models before services
- Services before endpoints
- Core implementation before integration
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
3. Write unit tests for the story and confirm they fail
4. Write integration tests for the story and confirm they fail
5. Implement the story until all new tests pass
6. Validate the story independently
7. Complete Phase 4: Polish and final verification with `/speckit.verify`

---

## Notes

- [P] tasks = different files, no dependencies
- The task list should cover one story only
- The story should be independently completable and testable
- Verify unit and integration tests fail before implementing
- Run `/speckit.verify` after implementation to check the final result against the original request
- Commit after each task or logical group
- Stop at the checkpoint to validate the story independently
- Avoid: vague tasks, optional testing, same file conflicts, or hidden scope beyond the story
