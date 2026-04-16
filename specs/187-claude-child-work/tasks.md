# Tasks: Claude Child Work

**Input**: Design documents from `specs/187-claude-child-work/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-347, FR-001 through FR-018, SC-001 through SC-006, and DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-028, DESIGN-REQ-030.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_child_work.py`
- Integration tests: `pytest tests/integration/schemas/test_claude_child_work_boundary.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing schema and test structure can host the story.

- [X] T001 Confirm active MM-347 artifacts in specs/187-claude-child-work/spec.md, specs/187-claude-child-work/plan.md, specs/187-claude-child-work/research.md, specs/187-claude-child-work/data-model.md, and specs/187-claude-child-work/contracts/claude-child-work.md (MM-347)
- [X] T002 Confirm schema boundary targets in moonmind/schemas/managed_session_models.py and moonmind/schemas/__init__.py for FR-001 through FR-018
- [X] T003 Confirm focused unit and integration commands from specs/187-claude-child-work/quickstart.md are runnable or have exact environment blockers recorded (SC-001, SC-005)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish red-first test files and imports before story implementation.

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [X] T004 Add failing unit tests for subagent child-context identity, parent-owned lifecycle, no peer-session collapse, and no promotion metadata in tests/unit/schemas/test_claude_child_work.py (FR-001, FR-002, FR-003, FR-004, FR-018, DESIGN-REQ-017, DESIGN-REQ-030)
- [X] T005 Add failing unit tests for team group, team member identities, group-aware teardown, team usage, and invalid team messages in tests/unit/schemas/test_claude_child_work.py (FR-006, FR-007, FR-008, FR-009, FR-010, FR-013, FR-014, DESIGN-REQ-018, DESIGN-REQ-019)
- [X] T006 Add failing unit tests for child-work event names, event identity requirements, unsupported child-work kinds, and compact metadata validation in tests/unit/schemas/test_claude_child_work.py (FR-011, FR-012, FR-015, FR-016, FR-017, DESIGN-REQ-020, DESIGN-REQ-028)
- [X] T007 [P] Add failing integration-style boundary test for the representative parent session, subagent, team group, teammate, peer message, usage, events, and teardown flow in tests/integration/schemas/test_claude_child_work_boundary.py (Acceptance Scenarios 1-6, SC-001 through SC-006)
- [X] T008 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_child_work.py` and record expected missing-export failures in specs/187-claude-child-work/tasks.md (T004-T006 red-first)
- [X] T009 Run `pytest tests/integration/schemas/test_claude_child_work_boundary.py -q` and record expected missing-export failures in specs/187-claude-child-work/tasks.md (T007 red-first)

**Checkpoint**: Red-first tests exist and fail for missing MM-347 schema behavior.

---

## Phase 3: Story - Claude Child Work

**Summary**: As a workflow designer, I want Claude subagents and agent teams represented as distinct child-work primitives so that parent-owned child contexts are not confused with peer sessions that communicate directly.

**Independent Test**: Spawn a subagent and an agent-team teammate from controlled fixtures, then assert that the subagent has no top-level peer session by default while teammates are separate managed session records in a session group with distinct usage and events.

**Traceability**: FR-001 through FR-018, SC-001 through SC-006, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-028, DESIGN-REQ-030.

**Test Plan**:

- Unit: model validation, topology validation, usage rollups, event validation, unsupported values, and scope guardrails.
- Integration: controlled fixture flow with parent session, subagent child context, session group, lead session, teammate session, peer message, events, usage summaries, and teardown.

### Unit Tests (write first)

- [X] T010 Finalize unit tests for `ClaudeChildContext` and `ClaudeChildWorkUsage` in tests/unit/schemas/test_claude_child_work.py (FR-001, FR-002, FR-003, FR-004, FR-005, FR-018)
- [X] T011 Finalize unit tests for `ClaudeSessionGroup`, `ClaudeTeamMemberSession`, `ClaudeTeamMessage`, and team membership validation in tests/unit/schemas/test_claude_child_work.py (FR-006, FR-007, FR-008, FR-009, FR-010, FR-013, FR-014)
- [X] T012 Finalize unit tests for `ClaudeChildWorkEvent`, event-name exports, required identifiers, and compact metadata validation in tests/unit/schemas/test_claude_child_work.py (FR-011, FR-012, FR-015, FR-016, FR-017)
- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_child_work.py` to confirm tests still fail before production code is added (red-first)

### Integration Tests (write first)

- [X] T014 [P] Finalize integration-style boundary test for `build_claude_child_work_fixture_flow` in tests/integration/schemas/test_claude_child_work_boundary.py (Acceptance Scenarios 1-6, SC-001 through SC-006)
- [X] T015 Run `pytest tests/integration/schemas/test_claude_child_work_boundary.py -q` to confirm tests still fail before production code is added (red-first)

### Implementation

- [X] T016 Add child-work literal types, event-name tuple, and usage model exports in moonmind/schemas/managed_session_models.py (FR-005, FR-010, FR-015, FR-016, FR-017)
- [X] T017 Add `ClaudeChildContext` validation in moonmind/schemas/managed_session_models.py (FR-001, FR-002, FR-003, FR-004, FR-018, DESIGN-REQ-017, DESIGN-REQ-030)
- [X] T018 Add `ClaudeSessionGroup`, `ClaudeTeamMemberSession`, `ClaudeTeamMessage`, and membership validation in moonmind/schemas/managed_session_models.py (FR-006, FR-007, FR-008, FR-009, FR-010, FR-013, FR-014, DESIGN-REQ-018, DESIGN-REQ-019)
- [X] T019 Add `ClaudeChildWorkEvent`, `ClaudeChildWorkFixtureFlow`, and deterministic fixture-flow helper in moonmind/schemas/managed_session_models.py (FR-011, FR-012, FR-015, FR-016, FR-017, DESIGN-REQ-020, DESIGN-REQ-028)
- [X] T020 Export new MM-347 schema names from moonmind/schemas/managed_session_models.py and moonmind/schemas/__init__.py (contract: specs/187-claude-child-work/contracts/claude-child-work.md)
- [X] T021 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_child_work.py` and fix failures in moonmind/schemas/managed_session_models.py or tests/unit/schemas/test_claude_child_work.py (FR-001 through FR-018)
- [X] T022 Run `pytest tests/integration/schemas/test_claude_child_work_boundary.py -q` and fix failures in moonmind/schemas/managed_session_models.py or tests/integration/schemas/test_claude_child_work_boundary.py (Acceptance Scenarios 1-6)

**Checkpoint**: The story is fully functional, covered by unit and integration-style tests, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T023 Update specs/187-claude-child-work/tasks.md with completed task markers and final test evidence (MM-347)
- [X] T024 Run quickstart validation commands from specs/187-claude-child-work/quickstart.md and record results in specs/187-claude-child-work/tasks.md (SC-001 through SC-006)
- [X] T025 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification or record exact blocker in specs/187-claude-child-work/tasks.md
- [X] T026 Run `./tools/test_integration.sh` for hermetic integration CI when Docker is available or record exact Docker blocker in specs/187-claude-child-work/tasks.md
- [X] T027 Run `/moonspec-verify` against specs/187-claude-child-work/spec.md after implementation and tests pass

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion and blocks production implementation
- **Story (Phase 3)**: Depends on red-first tests
- **Polish (Phase 4)**: Depends on story tests passing

### Within The Story

- Unit tests T010-T012 must be written and fail before T016-T020.
- Integration test T014 must be written and fail before T016-T020.
- Production code in T016-T020 must pass focused validation T021-T022 before full verification.
- Final `/moonspec-verify` task T027 runs after implementation and test evidence exists.

### Parallel Opportunities

- T004-T006 must be coordinated because they touch the same unit test file; T007 can be authored separately in the integration test file.
- T010-T012 must be coordinated because they touch the same unit test file; T014 can proceed independently in the integration test file.
- T014 can proceed independently from unit test refinement.
- No production implementation tasks are marked parallel because they all modify the shared schema module and exports.

---

## Implementation Strategy

1. Complete setup checks and red-first test files.
2. Confirm focused tests fail because MM-347 schema names are missing.
3. Implement schema contracts and helper functions in the existing managed-session schema boundary.
4. Export the contract surface from both schema modules.
5. Run focused unit and integration-style tests until green.
6. Run full unit and hermetic integration verification where the environment allows.
7. Run `/moonspec-verify` and use its report as the final completion gate.

## Test Evidence

- Red-first unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_child_work.py` failed before implementation with missing `CLAUDE_CHILD_WORK_EVENT_NAMES` export.
- Red-first integration-style: `pytest tests/integration/schemas/test_claude_child_work_boundary.py -q` failed before implementation with missing `build_claude_child_work_fixture_flow` export.
- Focused unit after implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_child_work.py` passed; Python child-work tests 14 passed and required UI tests 222 passed.
- Focused integration-style after implementation: `pytest tests/integration/schemas/test_claude_child_work_boundary.py -q` passed; 1 passed.
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed; Python unit suite 3350 passed, 1 xpassed, 16 subtests passed, and required UI tests 222 passed.
- Hermetic integration CI: Docker socket is not available in this managed container (`/var/run/docker.sock` missing), so `./tools/test_integration.sh` cannot run here.
- MoonSpec verify: manual verification against specs/187-claude-child-work/spec.md found the MM-347 story fully implemented with unit and integration-style evidence; hermetic integration CI remains environment-blocked by missing Docker socket.
