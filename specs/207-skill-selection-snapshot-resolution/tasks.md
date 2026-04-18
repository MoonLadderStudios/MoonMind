# Tasks: Skill Selection and Snapshot Resolution

**Input**: `specs/207-skill-selection-snapshot-resolution/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/skill-snapshot-resolution.md`, `quickstart.md`
**Prerequisites**: `docs/tmp/jira-orchestration-inputs/MM-406-moonspec-orchestration-input.md`; existing agent-skill resolver/activity/materializer services
**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py tests/unit/services/test_skill_resolution.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py tests/unit/services/test_skill_materialization.py`
**Integration Test Command**: `./tools/test_integration.sh` when Docker is available
**Source Traceability**: FR-001 through FR-012; acceptance scenarios 1-5; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-019; Jira issue MM-406.

## Phase 1: Setup

- [X] T001 Confirm active feature locator in `.specify/feature.json` points to `specs/207-skill-selection-snapshot-resolution` and MM-406 source input exists in `docs/tmp/jira-orchestration-inputs/MM-406-moonspec-orchestration-input.md` (FR-012, SC-006)
- [X] T002 Inspect existing selector, resolver, activity, materializer, and AgentRun request code in `moonmind/workflows/tasks/task_contract.py`, `moonmind/services/skill_resolution.py`, `moonmind/workflows/agent_skills/agent_skills_activities.py`, `moonmind/services/skill_materialization.py`, and `moonmind/workflows/temporal/workflows/run.py` (FR-001-FR-011)

## Phase 2: Foundational

- [X] T003 Add or confirm test helper fixtures for effective skill selector assertions in `tests/unit/workflows/tasks/test_task_contract.py` (FR-001, FR-002, DESIGN-REQ-008)
- [X] T004 Add or confirm workflow-boundary test helpers for observing resolved skillset refs before AgentRun launch in `tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py` (FR-006, FR-010, DESIGN-REQ-019)

## Phase 3: Story - Resolve Skill Snapshots Before Runtime Launch

**Story Summary**: Resolve task-wide and step-specific skill intent into an immutable, artifact-backed snapshot before runtime launch.
**Independent Test**: Validate selector merge behavior, pre-launch resolution, compact `resolvedSkillsetRef` propagation, fail-fast pinned errors, and snapshot reuse without requiring a full external provider run.
**Traceability**: FR-001-FR-012; acceptance scenarios 1-5; SC-001-SC-006; DESIGN-REQ-006-DESIGN-REQ-010; DESIGN-REQ-019.

### Unit Test Plan

- Selector merge unit tests in `tests/unit/workflows/tasks/test_task_contract.py`.
- Resolver/activity regression tests in `tests/unit/services/test_skill_resolution.py` and `tests/unit/workflows/agent_skills/test_agent_skills_activities.py`.

### Integration / Boundary Test Plan

- Workflow-boundary tests in `tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py`.
- Existing Temporal integration snapshot tests remain supporting evidence where Docker/time-skipping is available.

### Tests First

- [X] T005 Add failing unit tests for effective task/step skill selector merging in `tests/unit/workflows/tasks/test_task_contract.py`, covering inherited includes, step exclusions, materialization override, and non-mutating task intent (FR-001, FR-002, SC-001, DESIGN-REQ-008)
- [X] T006 Add failing workflow-boundary test that task/step skill intent triggers pre-launch skill resolution and passes compact `resolvedSkillsetRef` into the AgentRun request in `tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py` (FR-001, FR-006, FR-010, SC-003, SC-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-009, DESIGN-REQ-019)
- [X] T007 Add failing workflow-boundary test that a pinned resolution failure stops before AgentRun launch in `tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py` (FR-003, FR-005, SC-002, DESIGN-REQ-010)
- [X] T008 Add failing workflow-boundary or unit test proving retry/rerun uses the original resolved skillset ref unless explicit re-resolution is requested in `tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py` or existing Temporal integration tests (FR-009, SC-004, DESIGN-REQ-010, DESIGN-REQ-019)
- [X] T009 Run focused red-first command `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py` and confirm new tests fail for the expected missing behavior (FR-001, FR-002, FR-006, FR-009, FR-010)

### Implementation

- [X] T010 Implement effective task/step skill selector merge helper in `moonmind/workflows/tasks/task_contract.py` without mutating task-level intent (FR-001, FR-002, DESIGN-REQ-008)
- [X] T011 Wire runtime preparation in `moonmind/workflows/temporal/workflows/run.py` to resolve the effective selector through the agent-skill activity/service boundary before AgentRun launch (FR-001, FR-003, FR-005, FR-006, FR-008, DESIGN-REQ-006, DESIGN-REQ-009, DESIGN-REQ-010)
- [X] T012 Thread the returned compact resolved snapshot ref into the AgentRun request in `moonmind/workflows/temporal/workflows/run.py` without embedding large skill content in workflow payloads (FR-006, FR-007, FR-010, DESIGN-REQ-007, DESIGN-REQ-019)
- [X] T013 Preserve existing source policy, artifact persistence, and `.agents/skills_active` materialization behavior in `moonmind/services/skill_resolution.py`, `moonmind/workflows/agent_skills/agent_skills_activities.py`, and `moonmind/services/skill_materialization.py` while adapting call sites if needed (FR-004, FR-007, FR-008, FR-011)
- [X] T014 Ensure resolution failures surface as pre-launch validation failures with actionable context and no raw skill body or secret material in `moonmind/workflows/temporal/workflows/run.py` (FR-005, DESIGN-REQ-010)

### Story Validation

- [X] T015 Run focused tests `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py tests/unit/services/test_skill_resolution.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py tests/unit/services/test_skill_materialization.py` and confirm pass (FR-001-FR-011, SC-001-SC-005)
- [X] T016 Run traceability check `rg -n "MM-406|DESIGN-REQ-006|DESIGN-REQ-007|DESIGN-REQ-008|DESIGN-REQ-009|DESIGN-REQ-010|DESIGN-REQ-019" specs/207-skill-selection-snapshot-resolution docs/tmp/jira-orchestration-inputs/MM-406-moonspec-orchestration-input.md` (FR-012, SC-006)

## Final Phase: Polish And Verification

- [X] T017 Run final unit verification `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or document the exact local blocker if unavailable (FR-001-FR-012)
- [X] T018 Run hermetic integration verification `./tools/test_integration.sh` when Docker is available, or document the exact Docker/runtime blocker (acceptance scenarios 1-5)
- [X] T019 Run `/moonspec-verify` equivalent against `specs/207-skill-selection-snapshot-resolution/spec.md` and write `specs/207-skill-selection-snapshot-resolution/verification.md` after implementation and tests pass (FR-001-FR-012, SC-001-SC-006)

## Dependencies and Execution Order

1. T001-T004 establish feature context and helpers.
2. T005-T009 create and confirm failing tests before production changes.
3. T010-T014 implement the missing selector merge and pre-launch resolution path.
4. T015-T016 validate focused behavior and traceability.
5. T017-T019 complete full verification.

## Parallel Examples

- T005 can run in parallel with T006 and T007 because they touch different test areas.
- T010 can run after T005, while T011 and T012 must wait for T006/T007 red-first coverage.
- T016 can run independently after artifacts exist.

## Implementation Strategy

Start with the smallest deterministic unit: effective selector merging. Then wire the workflow boundary so the runtime path consumes the existing resolver activity and passes only compact refs to AgentRun. Preserve already-verified source policy and materialization behavior; do not introduce a parallel skill source loader, new storage table, or workflow-history skill body payload.
