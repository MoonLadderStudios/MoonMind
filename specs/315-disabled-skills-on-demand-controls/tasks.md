# Tasks: Disabled Skills On Demand Controls

**Input**: Design documents from `/work/agent_jobs/mm:dbba0e4a-6d65-495d-935e-d128cd7379e3/repo/specs/315-disabled-skills-on-demand-controls/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/skills-on-demand-disabled-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around exactly one user story: Disabled Skills On Demand Control.

**Source Traceability**: MM-612 and the canonical Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-009, SCN-001 through SCN-005, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-011, DESIGN-REQ-012.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/config tests/unit/workflows/agent_skills tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and does not depend on incomplete work.
- Each task includes exact file paths and traceability IDs where applicable.
- This task list covers one story only and does not include multiple priority story phases.

## Phase 1: Setup

**Purpose**: Confirm the active feature and test targets before story work.

- [X] T001 Confirm `SPECIFY_FEATURE=315-disabled-skills-on-demand-controls .specify/scripts/bash/check-prerequisites.sh --json` reports `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md` for `specs/315-disabled-skills-on-demand-controls/`
- [X] T002 Review current Skill setting, activity, and runtime test layout in `tests/unit/config/`, `tests/unit/workflows/agent_skills/`, `tests/unit/workflows/temporal/test_activity_runtime.py`, and `tests/unit/workflows/temporal/test_agent_runtime_activities.py` before adding new tests
- [X] T003 Confirm MM-612 traceability remains present in `specs/315-disabled-skills-on-demand-controls/spec.md`, `specs/315-disabled-skills-on-demand-controls/plan.md`, and `specs/315-disabled-skills-on-demand-controls/tasks.md` (FR-009, SC-006)

---

## Phase 2: Foundational

**Purpose**: Establish shared test locations and the disabled contract shape before story implementation begins.

**CRITICAL**: No production implementation work begins until Phase 2 and red-first tasks in Phase 3 are complete.

- [X] T004 Create the missing unit test package path `tests/unit/config/` with an `__init__.py` if the path does not already exist (FR-001, FR-002, DESIGN-REQ-012)
- [X] T005 [P] Add disabled Skills On Demand contract fixtures or helpers in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` for the common `denied` / `feature_disabled` response shape (FR-003, FR-004, DESIGN-REQ-011)
- [X] T006 [P] Add integration test fixture scaffolding in `tests/integration/temporal/test_skills_on_demand_disabled.py` for a managed runtime with one initially selected Skill and Skills On Demand unset (SCN-001, SCN-003, SCN-005)

---

## Phase 3: Story - Disabled Skills On Demand Control

**Summary**: As a MoonMind operator, I want Skills On Demand controlled by a global disabled-by-default setting so managed agents cannot discover or request additional Skills unless the deployment intentionally enables the capability.

**Independent Test**: Configure a deployment with Skills On Demand unset or explicitly disabled, prepare or simulate a managed runtime with an initial active Skill snapshot, and verify on-demand query/request attempts are denied without catalog results or new snapshots while the original active Skill snapshot remains available.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-011, DESIGN-REQ-012

**Unit Test Plan**:

- Settings default and alias behavior in `tests/unit/config/test_skills_on_demand_settings.py`
- Disabled query/request response and no-side-effect behavior in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`
- Runtime activation hidden-command and disabled-message behavior in `tests/unit/workflows/temporal/test_activity_runtime.py` and `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- Initial selected Skill regression coverage in existing agent skill activity tests

**Integration Test Plan**:

- Hermetic managed-runtime boundary coverage in `tests/integration/temporal/test_skills_on_demand_disabled.py` when unit activity-boundary coverage cannot prove the full command flow
- The integration test must cover unset default behavior, query denial, request denial, zero results, zero derived snapshots, and initial active Skill availability

### Unit Tests (write first)

- [X] T007 [P] Add failing settings unit tests for default `false` behavior and the `MOONMIND_SKILLS_ON_DEMAND_ENABLED=false` alias in `tests/unit/config/test_skills_on_demand_settings.py` (FR-001, FR-002, SCN-001, SCN-002, SC-001, SC-002, DESIGN-REQ-012)
- [X] T008 [P] Add failing settings unit tests for the `WORKFLOW_SKILLS_ON_DEMAND_ENABLED=false` alias and deterministic same-control behavior in `tests/unit/config/test_skills_on_demand_settings.py` (FR-002, SCN-002, SC-002, DESIGN-REQ-012)
- [X] T009 [P] Add failing unit tests for disabled `moonmind.skills.query` returning `status=denied`, `code=feature_disabled`, and empty `results` in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` (FR-003, SCN-003, SC-001, SC-003, DESIGN-REQ-011)
- [X] T010 [P] Add failing unit tests for disabled `moonmind.skills.request` returning `status=denied`, `code=feature_disabled`, and no snapshot or activation fields in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` (FR-004, SCN-003, SC-004, DESIGN-REQ-011)
- [X] T011 [P] Add failing unit tests proving disabled query/request handling does not call `AgentSkillResolver.resolve`, artifact persistence, or `AgentSkillMaterializer.materialize` in `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` (FR-004, FR-008, SC-004, DESIGN-REQ-011)
- [X] T012 [P] Add failing runtime activation unit tests for hidden on-demand commands when command exposure is controllable in `tests/unit/workflows/temporal/test_activity_runtime.py` (FR-005, SCN-004, DESIGN-REQ-011)
- [X] T013 [P] Add failing runtime activation unit tests for disabled activation text when command exposure cannot be fully hidden in `tests/unit/workflows/temporal/test_agent_runtime_activities.py` (FR-006, SCN-004, DESIGN-REQ-011)
- [X] T014 [P] Add failing regression unit tests proving initial selected Skill resolution/materialization still returns an active snapshot while Skills On Demand is disabled in `tests/unit/workflows/agent_skills/test_agent_skills_activities.py` (FR-007, SCN-005, SC-005, DESIGN-REQ-001)
- [X] T015 Run `./tools/test_unit.sh tests/unit/config tests/unit/workflows/agent_skills tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_agent_runtime_activities.py` and confirm T007-T014 fail for missing Skills On Demand behavior, not unrelated infrastructure errors

### Integration Tests (write first)

- [X] T016 [P] Add failing hermetic integration coverage for unset default disabled query/request behavior in `tests/integration/temporal/test_skills_on_demand_disabled.py` (FR-001, FR-003, FR-004, SCN-001, SCN-003, SC-001, SC-003, SC-004)
- [X] T017 [P] Add failing hermetic integration coverage proving an initially selected active Skill remains available after disabled query/request attempts in `tests/integration/temporal/test_skills_on_demand_disabled.py` (FR-007, SCN-005, SC-005, DESIGN-REQ-001)
- [X] T018 [P] Mark `tests/integration/temporal/test_skills_on_demand_disabled.py` with `integration_ci` only if it uses hermetic local dependencies and fits the required integration suite; otherwise document why unit activity-boundary coverage owns this story in the test file comments (SCN-003, SCN-005)
- [X] T019 Run `./tools/test_integration.sh` or the focused integration command documented in `tests/integration/temporal/test_skills_on_demand_disabled.py` and confirm T016-T018 fail for missing Skills On Demand behavior, not environment setup

### Red-First Confirmation

- [X] T020 Record the red-first unit failure summary for T007-T015 in `specs/315-disabled-skills-on-demand-controls/tasks.md` or implementation notes before production changes (FR-001 through FR-008)
- [X] T021 Record the red-first integration or activity-boundary failure summary for T016-T019 in `specs/315-disabled-skills-on-demand-controls/tasks.md` or implementation notes before production changes (SCN-001, SCN-003, SCN-005)

### Implementation

- [X] T022 Add `skills_on_demand_enabled: bool` with default `False` and aliases `MOONMIND_SKILLS_ON_DEMAND_ENABLED` and `WORKFLOW_SKILLS_ON_DEMAND_ENABLED` in `moonmind/config/settings.py` (FR-001, FR-002, DESIGN-REQ-012)
- [X] T023 Add transient Pydantic models or typed helpers for disabled Skills On Demand query/request results in `moonmind/schemas/agent_skill_models.py` (FR-003, FR-004, DESIGN-REQ-011)
- [X] T024 Implement the disabled-first Skills On Demand query/request service boundary in `moonmind/services/skills_on_demand.py` without catalog lookup, resolver invocation, artifact persistence, materialization, or derived snapshot creation while disabled (FR-003, FR-004, FR-008, SC-003, SC-004, DESIGN-REQ-011)
- [X] T025 Wire disabled query/request activity or runtime command handlers through `moonmind/workflows/agent_skills/agent_skills_activities.py` using the service boundary from `moonmind/services/skills_on_demand.py` (FR-003, FR-004, DESIGN-REQ-011)
- [X] T026 Update activity registration and worker binding for any new on-demand activity names in `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workflows/temporal/activity_catalog.py`, and `moonmind/workflows/temporal/worker_runtime.py` if implementation adds Temporal activities (FR-003, FR-004, SCN-003)
- [X] T027 Update managed runtime activation preparation to hide on-demand commands when controllable in `moonmind/workflows/temporal/activity_runtime.py` (FR-005, SCN-004, DESIGN-REQ-011)
- [X] T028 Update managed runtime activation preparation to include disabled activation text when commands cannot be fully hidden in `moonmind/workflows/temporal/activity_runtime.py` or `moonmind/agents/codex_worker/worker.py`, following the contract in `specs/315-disabled-skills-on-demand-controls/contracts/skills-on-demand-disabled-contract.md` (FR-006, SCN-004)
- [X] T029 Preserve normal initial selected Skill resolution/materialization behavior in `moonmind/workflows/agent_skills/agent_skills_activities.py`, `moonmind/services/skill_resolution.py`, and `moonmind/services/skill_materialization.py`; only change these files if T014 or T017 exposes a regression (FR-007, DESIGN-REQ-001)
- [X] T030 Run `./tools/test_unit.sh tests/unit/config tests/unit/workflows/agent_skills tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_agent_runtime_activities.py` and fix implementation gaps until T007-T015 pass
- [ ] T031 Run `./tools/test_integration.sh` when `tests/integration/temporal/test_skills_on_demand_disabled.py` is marked `integration_ci`; otherwise run the documented activity-boundary substitute and fix implementation gaps until T016-T019 pass

### Conditional Fallback Tasks for Implemented-Unverified Rows

- [ ] T032 If T014 or T017 fails because initial Skill snapshots are affected, repair initial resolution/materialization compatibility in `moonmind/workflows/agent_skills/agent_skills_activities.py`, `moonmind/services/skill_resolution.py`, or `moonmind/services/skill_materialization.py` without changing disabled on-demand semantics (FR-007, SCN-005, DESIGN-REQ-001)
- [ ] T033 If policy tests show disabled on-demand bypasses existing Skill source policy assumptions, route any enabled-capable follow-up path through `SkillResolutionContext` and existing resolver policy in `moonmind/services/skills_on_demand.py` (FR-008)
- [ ] T034 If traceability drift is found, update `specs/315-disabled-skills-on-demand-controls/plan.md`, `specs/315-disabled-skills-on-demand-controls/research.md`, and `specs/315-disabled-skills-on-demand-controls/tasks.md` to preserve MM-612 and all source mappings (FR-009, SC-006)

### Story Validation

- [X] T035 Validate the story independently with `./tools/test_unit.sh tests/unit/config tests/unit/workflows/agent_skills tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_agent_runtime_activities.py` and record passing evidence for FR-001 through FR-008
- [X] T036 Validate the integration or activity-boundary story path with `./tools/test_integration.sh` or the documented focused substitute and record passing evidence for SCN-001, SCN-003, SCN-005, SC-001, SC-003, SC-004, and SC-005

**Checkpoint**: Disabled Skills On Demand control is implemented, covered by unit and integration/activity-boundary tests, traceable to MM-612, and testable independently.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden enabled-mode scope.

- [X] T037 [P] Review `docs/Steps/SkillsOnDemand.md` and update only if implementation reveals a desired-state wording mismatch; keep migration or rollout notes in `specs/315-disabled-skills-on-demand-controls/` (DESIGN-REQ-011, DESIGN-REQ-012)
- [X] T038 [P] Run `git diff --check` for whitespace and formatting across implementation, tests, and `specs/315-disabled-skills-on-demand-controls/`
- [X] T039 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for required full unit verification
- [ ] T040 Run `./tools/test_integration.sh` if the story added or changed `integration_ci` coverage; otherwise preserve the documented reason why focused activity-boundary coverage is sufficient
- [ ] T041 Run `/moonspec-verify` against `specs/315-disabled-skills-on-demand-controls/spec.md` after implementation and tests pass, and record final verification evidence preserving MM-612

---

## Dependencies And Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Phase 2; unit and integration tests must be written and confirmed red before implementation.
- **Polish And Verification (Phase 4)**: Depends on the story being implemented and focused tests passing.

### Within The Story

- T007-T014 must be written before T015.
- T016-T018 must be written before T019.
- T020-T021 must be completed before T022.
- T022-T029 are implementation tasks and must not begin until red-first confirmation is complete.
- T030-T031 validate implementation and may reveal whether T032-T034 are needed.
- T035-T036 complete story validation before Phase 4.
- T041 is the final MoonSpec gate and runs only after implementation and tests pass.

### Parallel Opportunities

- T005 and T006 can run in parallel after T004 if they touch separate files.
- T007-T014 can be authored in parallel where they touch distinct test files.
- T016-T018 can be authored in parallel in the integration test file only if edits are coordinated; otherwise complete them sequentially.
- T022 and T023 can start together after red-first confirmation because they touch different files, but T024 depends on both.
- T037 and T038 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Launch independent unit test authoring:
Task: "T007 Add settings alias tests in tests/unit/config/test_skills_on_demand_settings.py"
Task: "T009 Add disabled query contract tests in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py"
Task: "T013 Add disabled activation text tests in tests/unit/workflows/temporal/test_agent_runtime_activities.py"

# Launch independent implementation only after red-first confirmation:
Task: "T022 Add setting aliases in moonmind/config/settings.py"
Task: "T023 Add transient result models in moonmind/schemas/agent_skill_models.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and foundational tasks.
2. Write settings, contract, side-effect, runtime activation, and initial Skill regression unit tests.
3. Write integration or activity-boundary tests for the disabled managed-runtime query/request story.
4. Confirm tests fail for the expected missing behavior.
5. Add the global disabled-by-default setting and aliases.
6. Add the disabled query/request response models and service boundary.
7. Wire command/activity/runtime handlers without invoking resolution or materialization while disabled.
8. Update activation preparation for hidden commands or disabled text.
9. Run focused unit and integration/activity-boundary validation.
10. Run full unit verification, required integration verification when applicable, and final `/moonspec-verify`.

### Status Handling From plan.md

- Missing rows FR-001, FR-002, FR-003, FR-004, FR-006, DESIGN-REQ-011, DESIGN-REQ-012, SC-001, SC-002, SC-003, and SC-004 require tests and implementation.
- Partial rows FR-005 and SCN-004 require tests and implementation to make disabled activation behavior explicit.
- Implemented-unverified rows FR-007, FR-008, FR-009, DESIGN-REQ-001, SC-005, and SC-006 require verification tests and conditional fallback implementation only if those tests expose gaps.
- No rows are currently classified implemented_verified.

## Notes

- Do not implement enabled-mode Skill discovery, approval workflows, audit persistence, per-skill fetchability, or per-user permissions in this story.
- On-demand runtime commands are not Agent Skills and must not be added to `ResolvedSkillSet`.
- Disabled query/request handling must happen before Skill catalog lookup, Skill resolution, artifact persistence, or materialization.
- Preserve Jira issue key MM-612 in downstream artifacts, implementation notes, verification output, commit text, and pull request metadata.

## Implementation Evidence

- Red-first unit evidence before production changes: `./tools/test_unit.sh tests/unit/config/test_skills_on_demand_settings.py tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/workflows/temporal/test_agent_runtime_activities.py::test_agent_runtime_prepare_turn_instructions_warns_skills_on_demand_disabled tests/unit/workflows/temporal/test_activity_runtime.py::test_build_activity_bindings_resolves_agent_runtime_fleet` failed during collection with `ImportError: cannot import name 'SkillsOnDemandQueryRequest'` from `moonmind.schemas.agent_skill_models`.
- Red-first integration evidence before production changes: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_disabled.py -q --tb=short` failed during collection with `ImportError: cannot import name 'SkillsOnDemandRequest'` from `moonmind.schemas.agent_skill_models`.
- Focused unit validation after implementation: `./tools/test_unit.sh tests/unit/config tests/unit/workflows/agent_skills tests/unit/workflows/temporal/test_activity_runtime.py tests/unit/workflows/temporal/test_agent_runtime_activities.py` passed with 235 Python tests and the frontend Vitest suite passed.
- Focused integration validation after implementation: `MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_disabled.py -q --tb=short` passed with 1 test.
- Full unit verification after implementation: `./tools/test_unit.sh` passed with 4501 Python tests, 16 subtests, and the frontend Vitest suite passed.
- Full integration runner blocker: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_integration.sh` could not build the Docker Compose pytest image because Docker returned `403 Forbidden` from administrative rules after creating a local `.env` from `.env-template`.
