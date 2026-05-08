# Tasks: Resolve On-Demand Skill Requests

**Input**: `specs/317-resolve-on-demand-skill-requests/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/skills-on-demand-request-contract.md`, `quickstart.md`

**Prerequisites**: Existing `315` disabled controls and `316` query behavior remain intact.

**Unit Test Command**: `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`

**Integration Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -q --tb=short`

**Source Traceability**: The original MM-614 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-013, acceptance scenarios 1-5, edge cases, SC-001 through SC-006, and DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-013, and DESIGN-REQ-014.

## Phase 1: Setup

- [X] T001 Confirm active feature directory and prerequisite artifacts exist in `specs/317-resolve-on-demand-skill-requests/`
- [X] T002 Review current request/query schemas and tests in `moonmind/schemas/agent_skill_models.py` and `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`

## Phase 2: Foundational

- [X] T003 Identify existing resolver/materializer activity boundary in `moonmind/workflows/agent_skills/agent_skills_activities.py`
- [X] T004 Confirm disabled request behavior remains covered by existing tests in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` and `tests/integration/temporal/test_skills_on_demand_disabled.py`

## Phase 3: Governed On-Demand Skill Activation

**Story Summary**: Managed runtimes can request additional Skills through MoonMind and receive `no_change`, `activated`, or structured `denied` outcomes while preserving immutable active snapshot boundaries.

**Independent Test**: Enable Skills On Demand, supply an active snapshot, and verify no-change, allowed activation, invalid request denial, and resolver/materializer failure preservation through service and activity boundaries.

**Traceability IDs**: FR-001 through FR-013; SCN-001 through SCN-005; SC-001 through SC-006; DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-013, DESIGN-REQ-014.

### Unit Test Plan

- [X] T005 [P] Add failing unit tests for enabled request validation denials in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` (FR-002, FR-003, FR-004, FR-012, SCN-003, DESIGN-REQ-004, DESIGN-REQ-014)
- [X] T006 [P] Add failing unit test for already-active requested Skills returning `no_change` in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` (FR-006, FR-008, SCN-001, SC-001, DESIGN-REQ-005)
- [X] T007 [P] Add failing unit test for enabled allowed additions creating a derived snapshot result with lineage in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` (FR-005, FR-007, FR-009, FR-010, SCN-002, SCN-005, SC-002, SC-005, DESIGN-REQ-002, DESIGN-REQ-008, DESIGN-REQ-013)
- [X] T008 [P] Add failing unit tests for resolver-style failure mapping and compact serialization safety in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` (FR-004, FR-011, FR-012, SCN-004, SC-004, DESIGN-REQ-014)

### Integration Test Plan

- [X] T009 [P] Add failing activity-boundary activation test in `tests/integration/temporal/test_skills_on_demand_request_activation.py` (FR-001, FR-005, FR-007, FR-009, SCN-002, DESIGN-REQ-008)
- [X] T010 [P] Add failing activity-boundary materialization failure preservation test in `tests/integration/temporal/test_skills_on_demand_request_activation.py` (FR-004, FR-012, SCN-004, DESIGN-REQ-014)

### Red-First Confirmation

- [X] T011 Run focused unit tests and confirm new enabled request tests fail before implementation using `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`
- [X] T012 Run focused activity-boundary tests and confirm new activation tests fail before implementation using `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -q --tb=short`

### Implementation

- [X] T013 Update request-specific status, denial codes, result fields, materialization summary, and model exports in `moonmind/schemas/agent_skill_models.py` and `moonmind/schemas/__init__.py` (FR-008, FR-009, FR-012, DESIGN-REQ-005)
- [X] T014 Implement enabled request validation, duplicate handling, `no_change`, activated result construction, lineage metadata, and safe denial helpers in `moonmind/services/skills_on_demand.py` (FR-002, FR-003, FR-006, FR-008, FR-009, FR-010, FR-011, FR-012)
- [X] T015 Wire `agent_skill.request_on_demand` to combine active/requested selectors, call `AgentSkillResolver.resolve`, persist manifests when available, materialize derived snapshots, and map failures safely in `moonmind/workflows/agent_skills/agent_skills_activities.py` (FR-001, FR-004, FR-005, FR-007, FR-009, FR-012, DESIGN-REQ-008, DESIGN-REQ-014)
- [X] T016 Ensure activated results and derived `ResolvedSkillSet.source_trace` preserve compact parent/request lineage in `moonmind/services/skills_on_demand.py` and `moonmind/workflows/agent_skills/agent_skills_activities.py` (FR-010, FR-011, DESIGN-REQ-013)

### Story Validation

- [X] T017 Run focused unit tests with `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`
- [X] T018 Run focused activity-boundary tests with `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -q --tb=short`
- [X] T019 Confirm `MM-614` and the canonical Jira preset brief remain preserved in `specs/317-resolve-on-demand-skill-requests/spec.md`, `plan.md`, and `tasks.md` (FR-013, SC-006)

## Final Phase: Polish And Verification

- [X] T020 Review generated artifacts for source-design drift and update only feature-local files if implementation reveals a mismatch
- [X] T021 Run final unit suite with `./tools/test_unit.sh`
- [ ] T022 Run hermetic integration suite with `./tools/test_integration.sh` when Docker is available
- [X] T023 Run `/speckit.verify` for `specs/317-resolve-on-demand-skill-requests/spec.md` after implementation and tests pass

## Verification Evidence

- Red-first unit evidence: `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` failed before implementation with four enabled-request failures for validation, `no_change`, activated result construction, and denial helper coverage.
- Red-first activity-boundary evidence: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -q --tb=short` failed before implementation with two enabled-request activity failures.
- Focused unit evidence after implementation: `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` passed, including 16 Python tests plus frontend pass-through from the unit runner.
- Focused activity-boundary evidence after implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -q --tb=short` passed with 2 tests.
- Focused disabled-plus-activation integration evidence: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_disabled.py tests/integration/temporal/test_skills_on_demand_request_activation.py -q --tb=short` passed with 3 tests.
- Final unit evidence: `./tools/test_unit.sh` passed with 4513 Python tests, 16 subtests, and frontend Vitest 20 files / 324 tests passing.
- Hermetic integration blocker: `./tools/test_integration.sh` could not run in this managed environment because Docker returned `403 Forbidden` while building the pytest image.
- Verification preflight caveat: `.gemini/skills` is an active managed skill projection symlink in this workspace and was already modified before this implementation work; it was not changed for MM-614.

## Dependencies And Execution Order

- T001-T004 must complete before story tests.
- T005-T010 must be written before T011-T012 red-first confirmation.
- T013-T016 depend on red-first confirmation.
- T017-T019 validate the story after implementation.
- T020-T023 are final verification tasks.

## Parallel Examples

- T005, T006, T007, and T008 can be drafted in parallel because they touch distinct test scenarios in the same file but should be merged carefully.
- T009 and T010 can be drafted together before activity implementation.

## Implementation Strategy

1. Preserve disabled and query behavior.
2. Expand request-specific contracts without changing query result semantics.
3. Keep pure validation/result shaping in `SkillsOnDemandService`.
4. Keep resolver, artifact, and materialization effects at the activity boundary.
5. Mark tasks complete only after the corresponding test or implementation is done.
