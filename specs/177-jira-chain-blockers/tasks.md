# Tasks: Jira Chain Blockers

**Input**: Design documents from `specs/177-jira-chain-blockers/` 
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Tasks reference `FR-*`, acceptance scenarios, success criteria, and MM-339 where applicable.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/test_story_output_tools.py tests/unit/integrations/test_jira_tool_service.py tests/unit/api/test_task_step_templates_service.py`
- Integration tests: `./tools/test_integration.sh` if implementation touches integration-ci boundaries beyond the planned unit-test surface
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active feature artifacts and implementation surfaces before test-first work.

- [X] T001 Confirm active feature context points to `specs/177-jira-chain-blockers` in `.specify/feature.json`
- [X] T002 [P] Review existing Jira story-output tests in `tests/unit/workflows/temporal/test_story_output_tools.py` for issue creation, reuse, and fallback coverage
- [X] T003 [P] Review trusted Jira service tests in `tests/unit/integrations/test_jira_tool_service.py` for request model, policy, and sanitized result patterns
- [X] T004 [P] Review seeded preset expansion tests in `tests/unit/api/test_task_step_templates_service.py` for Jira Breakdown expectations

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define the dependency-link contracts and validation points that block story implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T005 Add failing trusted Jira model/service tests for issue-link request validation, self-link rejection, allowed action policy, allowed project policy, compact sanitized result, and Jira REST request shape covering FR-006, FR-012, FR-013 in `tests/unit/integrations/test_jira_tool_service.py`
- [X] T006 Add failing story-output tests for dependency-mode parsing and unsupported mode fallback/failure before Jira mutation covering FR-001, FR-012 in `tests/unit/workflows/temporal/test_story_output_tools.py`
- [X] T007 Run `./tools/test_unit.sh tests/unit/integrations/test_jira_tool_service.py tests/unit/workflows/temporal/test_story_output_tools.py` and confirm T005-T006 fail for missing dependency-link support

**Checkpoint**: Foundation tests prove the missing trusted Jira link boundary and dependency-mode validation.

---

## Phase 3: Story - Ordered Jira Story Dependency Chain

**Summary**: As a MoonMind operator exporting a Jira Breakdown result, I want MoonMind to create Jira issues and optionally link them in ordered blocker-chain mode so generated implementation stories carry explicit Jira dependencies.

**Independent Test**: Run the Jira Breakdown export path and structured story-output path with ordered three-story input in `linear_blocker_chain` and `none` modes, then verify issue mappings, link results, partial-failure reporting, and retry/reuse behavior.

**Traceability**: FR-001 through FR-013, SC-001 through SC-006, acceptance scenarios 1-6, MM-339

**Test Plan**:

- Unit: dependency-mode parsing, story ordering, issue/link mapping, partial link failure, retry/reuse, trusted Jira request validation, policy checks, sanitized results
- Integration: seeded preset expansion and agent-skill/deterministic path contract alignment through existing catalog/runtime-unit boundaries

### Unit Tests (write first)

- [X] T008 [P] Add failing story-output unit test for `linear_blocker_chain` three-story success with two adjacent blocker link results covering FR-002, FR-003, FR-004, FR-007, SC-001 in `tests/unit/workflows/temporal/test_story_output_tools.py`
- [X] T009 [P] Add failing story-output unit test for dependency mode `none` creating issues without link requests covering FR-005, SC-002 in `tests/unit/workflows/temporal/test_story_output_tools.py`
- [X] T010 [P] Add failing story-output unit test for partial link failure preserving created issue keys and reporting incomplete chain covering FR-008, SC-003 in `tests/unit/workflows/temporal/test_story_output_tools.py`
- [X] T011 [P] Add failing story-output unit test for retry/reuse of existing issues and existing links without duplicate creation covering FR-009, SC-004 in `tests/unit/workflows/temporal/test_story_output_tools.py`
- [X] T012 [P] Add failing story-output unit test for missing Jira target configuration with dependency mode preserving local-only handoffs fallback covering FR-010 and acceptance scenario 5 in `tests/unit/workflows/temporal/test_story_output_tools.py`

### Integration Tests (write first)

- [X] T013 [P] Add failing preset expansion unit test for Jira Breakdown dependency mode input and rendered Jira creation instructions covering FR-001, FR-011, SC-005 in `tests/unit/api/test_task_step_templates_service.py`
- [X] T014 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_story_output_tools.py tests/unit/integrations/test_jira_tool_service.py tests/unit/api/test_task_step_templates_service.py` and confirm T008-T013 fail for expected missing behavior

### Implementation

- [X] T015 Add Jira issue-link request model and action registration for the trusted tool surface covering FR-006, FR-012 in `moonmind/integrations/jira/models.py`
- [X] T016 Implement trusted issue-link creation and duplicate/existing link classification in `moonmind/integrations/jira/tool.py` using existing policy checks and compact sanitized return values covering FR-006, FR-009, FR-013
- [X] T017 Implement dependency-mode parsing, ordered story mapping, link-request planning, partial link failure reporting, fallback preservation, and result fields covering FR-001 through FR-010 and FR-012 in `moonmind/workflows/temporal/story_output_tools.py`
- [X] T018 Update Jira Breakdown seed inputs and instructions for dependency mode covering FR-001, FR-011 in `api_service/data/task_step_templates/jira-breakdown.yaml`
- [X] T019 Update `jira-issue-creator` instructions to consume dependency mode and use trusted Jira link creation covering FR-006, FR-011 in `.agents/skills/jira-issue-creator/SKILL.md`
- [X] T020 Update `moonspec-breakdown` instructions to preserve ordered story IDs and dependencies for Jira export covering FR-002, FR-011 in `.agents/skills/moonspec-breakdown/SKILL.md`
- [X] T021 Run targeted unit command and fix failures: `./tools/test_unit.sh tests/unit/workflows/temporal/test_story_output_tools.py tests/unit/integrations/test_jira_tool_service.py tests/unit/api/test_task_step_templates_service.py`

**Checkpoint**: The story is fully functional, covered by unit tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without expanding scope.

- [X] T022 [P] Review output field names against `specs/177-jira-chain-blockers/contracts/story-output-contract.md` and update contracts only if implementation requires clearer naming
- [X] T023 [P] Review trusted Jira contract against `specs/177-jira-chain-blockers/contracts/jira-dependency-links.md` and update it only to match implemented request/result names
- [X] T024 Run `./tools/test_unit.sh` for full unit verification and record result
- [X] T025 Run `./tools/test_integration.sh` if any integration-ci boundary changed; otherwise document why it was not required. Result: not required because the implementation changes Jira service/model behavior, deterministic story-output handling, preset expansion, and agent skill instructions without changing compose-backed services, persistence, or Temporal workflow payload shapes.
- [X] T026 Run `/moonspec-verify` to validate final implementation against MM-339 and `specs/177-jira-chain-blockers/spec.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion; blocks story implementation.
- **Story (Phase 3)**: Depends on Foundational tests failing for expected missing behavior.
- **Polish (Phase 4)**: Depends on story implementation and targeted tests passing.

### Within The Story

- T008-T013 must be written before implementation tasks T015-T020.
- T014 must confirm red-first failure before T015-T020.
- T015-T016 establish trusted Jira link support before T017 can call it.
- T017 must land before preset/skill updates can be fully validated by T021.
- T021 must pass before final full-suite and verification tasks.

### Parallel Opportunities

- T002-T004 can run in parallel after T001.
- T008-T013 can be authored in parallel after T007 because they touch distinct test concerns.
- T018-T020 can be updated in parallel after T017 behavior is clear because they touch separate files.
- T022-T023 can run in parallel after T021.

## Implementation Strategy

1. Complete setup review and red-first foundational tests.
2. Add story-output, Jira service, and preset failing tests.
3. Implement the trusted Jira link model/service action.
4. Extend `story.create_jira_issues` to plan and report dependency links after issue mapping.
5. Update the seeded preset and agent skill instructions to request and preserve dependency mode.
6. Run targeted unit validation, then full unit validation.
7. Run final Moon Spec verification.
