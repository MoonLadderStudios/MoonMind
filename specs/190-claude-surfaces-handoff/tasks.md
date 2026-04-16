# Tasks: Claude Surfaces Handoff

**Input**: Design documents from `specs/190-claude-surfaces-handoff/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-348, FR-001 through FR-019, SC-001 through SC-007, and DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-024, DESIGN-REQ-028.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_surfaces_handoff.py`
- Integration tests: `pytest tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

- [X] T001 Confirm MM-348 input and active artifacts in docs/tmp/jira-orchestration-inputs/MM-348-moonspec-orchestration-input.md and specs/190-claude-surfaces-handoff/spec.md (MM-348)
- [X] T002 Confirm existing Claude session schema boundary in moonmind/schemas/managed_session_models.py and moonmind/schemas/__init__.py (FR-001 through FR-019)
- [X] T003 Confirm focused unit and integration commands from specs/190-claude-surfaces-handoff/quickstart.md (SC-001 through SC-007)

## Phase 2: Foundational

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [X] T004 Add failing unit tests for primary/projection surface bindings and Remote Control preservation in tests/unit/schemas/test_claude_surfaces_handoff.py (FR-001 through FR-006, SC-001, SC-002, DESIGN-REQ-002, DESIGN-REQ-004)
- [X] T005 Add failing unit tests for disconnect/reconnect/detach, resume, cloud handoff seed refs, and execution security classification in tests/unit/schemas/test_claude_surfaces_handoff.py (FR-007 through FR-014, FR-018, SC-003, SC-004, SC-005, DESIGN-REQ-019, DESIGN-REQ-024, DESIGN-REQ-028)
- [X] T006 Add failing unit tests for normalized surface lifecycle events and invalid event/handoff payloads in tests/unit/schemas/test_claude_surfaces_handoff.py (FR-015 through FR-017, FR-019, DESIGN-REQ-020)
- [X] T007 [P] Add failing integration-style boundary test for attach, disconnect, reconnect, resume, and cloud handoff fixture flow in tests/integration/schemas/test_claude_surfaces_handoff_boundary.py (Acceptance Scenarios 1-6, SC-006)
- [X] T008 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_surfaces_handoff.py` and record expected missing-export failures in specs/190-claude-surfaces-handoff/tasks.md (T004-T006 red-first)
- [X] T009 Run `pytest tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q` and record expected missing-export failures in specs/190-claude-surfaces-handoff/tasks.md (T007 red-first)

## Phase 3: Story - Claude Surface Projection And Handoff

**Summary**: As a user moving between Claude surfaces, I want surface attachment and handoff semantics to preserve where Claude is actually executing so that local execution, Remote Control projection, and cloud execution remain auditable and distinct.

**Independent Test**: Attach local and Remote Control surfaces to a local Claude session, disconnect and reconnect a projection, resume on another local surface, perform a cloud handoff, and assert session identity, execution owner, projection mode, handoff lineage, seed artifact references, and normalized surface events.

**Traceability**: FR-001 through FR-019, SC-001 through SC-007, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-024, DESIGN-REQ-028.

### Tests

- [X] T010 Finalize unit tests for surface binding invariants in tests/unit/schemas/test_claude_surfaces_handoff.py (FR-001 through FR-006)
- [X] T011 Finalize unit tests for connection-state, resume, handoff, and classification invariants in tests/unit/schemas/test_claude_surfaces_handoff.py (FR-007 through FR-014, FR-018)
- [X] T012 Finalize unit tests for `ClaudeSurfaceLifecycleEvent` and event-name exports in tests/unit/schemas/test_claude_surfaces_handoff.py (FR-015 through FR-017, FR-019)
- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_surfaces_handoff.py` to confirm tests still fail before production code is added (red-first)
- [X] T014 [P] Finalize integration-style boundary test for `build_claude_surface_handoff_fixture_flow` in tests/integration/schemas/test_claude_surfaces_handoff_boundary.py (Acceptance Scenarios 1-6, SC-006)
- [X] T015 Run `pytest tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q` to confirm tests still fail before production code is added (red-first)

### Implementation

- [X] T016 Add surface capability, lifecycle event, and security mode literal types and exports in moonmind/schemas/managed_session_models.py (FR-002, FR-014 through FR-019)
- [X] T017 Extend `ClaudeSurfaceBinding` and `ClaudeManagedSession` with binding invariants and operations in moonmind/schemas/managed_session_models.py (FR-001 through FR-013)
- [X] T018 Add `ClaudeSurfaceLifecycleEvent`, security classifier, and deterministic fixture-flow helper in moonmind/schemas/managed_session_models.py (FR-014 through FR-019)
- [X] T019 Export new MM-348 schema names from moonmind/schemas/managed_session_models.py and moonmind/schemas/__init__.py (contract: specs/190-claude-surfaces-handoff/contracts/claude-surfaces-handoff.md)
- [X] T020 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_surfaces_handoff.py` and fix failures (FR-001 through FR-019)
- [X] T021 Run `pytest tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q` and fix failures (Acceptance Scenarios 1-6)

## Phase 4: Polish And Verification

- [X] T022 Update specs/190-claude-surfaces-handoff/tasks.md with completed task markers and final test evidence (MM-348)
- [X] T023 Run quickstart validation commands from specs/190-claude-surfaces-handoff/quickstart.md and record results (SC-001 through SC-007)
- [X] T024 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification or record exact blocker
- [X] T025 Run `./tools/test_integration.sh` for hermetic integration CI when Docker is available or record exact Docker blocker
- [X] T026 Run `/moonspec-verify` against specs/190-claude-surfaces-handoff/spec.md after implementation and tests pass

## Dependencies And Execution Order

- Setup precedes red-first tests.
- Red-first tests T004-T009 precede production implementation.
- Production implementation T016-T019 precedes focused green validation T020-T021.
- Full verification T024-T026 runs after focused validation.

## Implementation Strategy

1. Create failing unit and integration-style tests for the MM-348 contract.
2. Confirm failures are caused by missing exports or missing behavior.
3. Extend existing Claude managed-session schema contracts and exports.
4. Run focused tests until green.
5. Run full unit and integration verification where the environment allows.
6. Run `/moonspec-verify` and use its report as the final completion gate.

## Test Evidence

- Red-first unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_surfaces_handoff.py` failed before implementation with missing `CLAUDE_SURFACE_LIFECYCLE_EVENT_NAMES` export.
- Red-first integration-style: `pytest tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q` failed before implementation with missing `build_claude_surface_handoff_fixture_flow` export.
- Focused unit after implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_surfaces_handoff.py` passed; Python surface handoff tests 13 passed and required UI tests 223 passed.
- Focused integration-style after implementation: `pytest tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q` passed; 1 passed.
- Related Claude schema regression: `pytest tests/unit/schemas/test_claude_managed_session_models.py tests/unit/schemas/test_claude_child_work.py -q` passed; 57 passed.
- Related Claude integration-style regression: `pytest tests/integration/schemas/test_claude_managed_session_boundary.py tests/integration/schemas/test_claude_child_work_boundary.py tests/integration/schemas/test_claude_surfaces_handoff_boundary.py -q` passed; 3 passed.
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed; Python unit suite 3402 passed, 1 xpassed, 16 subtests passed, and required UI tests 223 passed.
- Hermetic integration CI: `./tools/test_integration.sh` could not run because Docker is unavailable in this managed container (`/var/run/docker.sock` missing: `connect: no such file or directory`).
- MoonSpec verify: manual verification against specs/190-claude-surfaces-handoff/spec.md found the MM-348 story fully implemented with unit and integration-style evidence; hermetic integration CI remains environment-blocked by missing Docker socket.
