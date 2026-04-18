# Tasks: Jira Breakdown and Orchestrate Skill

**Input**: Design artifacts from `specs/207-jira-breakdown-orchestrate-skill/`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/jira-breakdown-orchestrate.md`, `quickstart.md`  
**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py tests/unit/workflows/temporal/test_story_output_tools.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_temporal_service.py`  
**Integration Test Command**: `./tools/test_integration.sh`  

## Source Traceability

- Original Jira issue: MM-404.
- Canonical input: `docs/tmp/jira-orchestration-inputs/MM-404-moonspec-orchestration-input.md`.
- Active spec: `specs/207-jira-breakdown-orchestrate-skill/spec.md`.
- Requirement status summary: 7 missing, 8 partial, 2 implemented-verified, 2 implemented-unverified across functional requirements and success criteria in `plan.md`.
- Code-and-test work: FR-001 through FR-011, FR-014, SC-001 through SC-006.
- Verification-only or traceability work: FR-012, FR-013, FR-015, SC-007.

## Story: Jira Breakdown to Ordered Orchestration

**Summary**: As a MoonMind operator, I want one Jira Breakdown and Orchestrate skill to break down a broad Jira issue, create one Jira Orchestrate task for each generated story, and order those tasks by dependency.

**Independent Test**: Run the skill with a Jira issue that breaks down into at least three stories, then verify that it completes the normal breakdown, creates exactly one Jira Orchestrate task per generated story, and records dependencies so task 2 waits for task 1 and task 3 waits for task 2.

**Independent Story Validation**: T019-T021 validate the story after implementation by running targeted unit coverage, startup seeding integration coverage, and a stubbed three-story orchestration check.

**Traceability IDs**: FR-001 through FR-015, SC-001 through SC-007, acceptance scenarios 1-6, edge cases in `spec.md`.

## Phase 1: Setup

- [X] T001 Confirm active MM-404 feature context in `.specify/feature.json` and `specs/207-jira-breakdown-orchestrate-skill/spec.md`
- [X] T002 Review existing Jira Breakdown, Jira Orchestrate, story output, and task dependency surfaces in `api_service/data/task_step_templates/jira-breakdown.yaml`, `api_service/data/task_step_templates/jira-orchestrate.yaml`, `moonmind/workflows/temporal/story_output_tools.py`, and `docs/Tasks/TaskDependencies.md`

## Phase 2: Foundational Tests

- [X] T003 [P] Add failing unit tests for the new composite seeded preset and expansion contract in `tests/unit/api/test_task_step_templates_service.py` covering FR-001, FR-002, FR-003, FR-011, FR-012, FR-013, FR-014, SC-006
- [X] T004 [P] Add failing unit tests for downstream Jira Orchestrate task creation from three, one, and zero ordered issue mappings in `tests/unit/workflows/temporal/test_story_output_tools.py` covering FR-004, FR-005, FR-006, FR-008, FR-009, SC-001, SC-003, SC-004
- [X] T005 [P] Add failing unit tests for dependency wiring, stable idempotency keys, and partial downstream creation outcomes in `tests/unit/workflows/temporal/test_story_output_tools.py` covering FR-007, FR-010, SC-002, SC-005
- [X] T006 [P] Add failing unit tests for runtime registration and selected skill dispatch for `story.create_jira_orchestrate_tasks` in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` covering FR-001, FR-005
- [X] T007 [P] Add failing service-boundary tests for task dependency validation and create-time `dependsOn` preservation used by downstream tasks in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-007, SC-002
- [X] T008 [P] Add failing startup seeding integration assertions for the composite preset in `tests/integration/test_startup_task_template_seeding.py` covering FR-001, FR-003, FR-011, SC-006
- [X] T009 Confirm red-first failure for targeted unit and integration tests before production code in `specs/207-jira-breakdown-orchestrate-skill/tasks.md`

## Phase 3: Story Implementation

### Unit Test Plan

- Seeded preset tests prove the composite skill is discoverable, runs normal Jira Breakdown first, references trusted Jira surfaces, and does not replace existing presets.
- Story output tests prove ordered issue mappings create downstream Jira Orchestrate tasks, preserve source traceability, handle zero and one story, wire dependencies, use stable idempotency, and report partial outcomes.
- Runtime registration tests prove the new deterministic tool is available through the existing story output tool handler path.
- Service tests prove dependency validation remains create-time and workflow-ID based.

### Integration Test Plan

- Startup seeding integration verifies the new global composite preset is persisted with the expected steps and trusted-tool instructions.
- Hermetic integration runner verifies required `integration_ci` coverage when Docker is available.

- [X] T010 Add the global `jira-breakdown-orchestrate` seed template with source input, Jira target inputs, runtime/publish inputs, normal Jira Breakdown steps, and downstream task creation step in `api_service/data/task_step_templates/jira-breakdown-orchestrate.yaml` covering FR-001, FR-002, FR-003, FR-011, FR-012, FR-013, FR-014
- [X] T011 Implement `story.create_jira_orchestrate_tasks` registration and exported handler alongside existing story output tools in `moonmind/workflows/temporal/story_output_tools.py` covering FR-001, FR-005, FR-006
- [X] T012 Implement ordered issue mapping normalization, missing issue-key handling, one-story handling, zero-story outcome handling, and MM-404 traceability propagation in `moonmind/workflows/temporal/story_output_tools.py` covering FR-004, FR-006, FR-008, FR-009, FR-015, SC-003, SC-004, SC-007
- [X] T013 Implement downstream Jira Orchestrate task payload construction with Jira issue key, runtime mode, publish mode, repository, source story metadata, and original brief reference in `moonmind/workflows/temporal/story_output_tools.py` covering FR-005, FR-006, FR-014, SC-006
- [X] T014 Implement sequential downstream task creation with stable per-story idempotency keys and `dependsOn` pointing to the previous created workflow ID in `moonmind/workflows/temporal/story_output_tools.py` covering FR-007, SC-001, SC-002
- [X] T015 Implement structured orchestration result reporting for completed, partial, no-downstream-task, skipped-story, dependency, and failure outcomes in `moonmind/workflows/temporal/story_output_tools.py` covering FR-010, SC-005
- [X] T016 Wire any required runtime execution-creation context or factory injection for the new story output tool in `moonmind/workflows/temporal/worker_runtime.py` covering FR-005, FR-007, FR-011
- [X] T017 Update seeded template catalog expectations for the new composite preset while preserving existing Jira Breakdown and Jira Orchestrate assertions in `tests/unit/api/test_task_step_templates_service.py` covering FR-001, FR-012, FR-013
- [X] T018 Update startup seed integration expectations for the new composite preset while preserving existing seeded template assertions in `tests/integration/test_startup_task_template_seeding.py` covering FR-001, FR-003, FR-011
- [X] T019 Run targeted unit tests for the story and fix only MM-404-related failures in `tests/unit/api/test_task_step_templates_service.py`, `tests/unit/workflows/temporal/test_story_output_tools.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, and `tests/unit/workflows/temporal/test_temporal_service.py`
- [X] T020 Run startup seeding integration coverage or record Docker/runtime blocker in `specs/207-jira-breakdown-orchestrate-skill/quickstart.md`
- [X] T021 Validate the independent story by exercising a stubbed three-story result and confirming three downstream tasks plus two dependency edges in `specs/207-jira-breakdown-orchestrate-skill/quickstart.md`

## Final Phase: Polish And Verification

- [X] T022 [P] Run traceability check for MM-404 across spec, plan, tasks, quickstart, and implementation files in `specs/207-jira-breakdown-orchestrate-skill/tasks.md`
- [X] T023 [P] Check that no raw Jira credential access or direct Jira shell mutation was introduced in `moonmind/workflows/temporal/story_output_tools.py` and `api_service/data/task_step_templates/jira-breakdown-orchestrate.yaml`
- [X] T024 Run the full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and record results in `specs/207-jira-breakdown-orchestrate-skill/quickstart.md`
- [X] T025 Run `./tools/test_integration.sh` when Docker is available, or record the Docker/socket blocker in `specs/207-jira-breakdown-orchestrate-skill/quickstart.md`
- [X] T026 Run `moonspec-verify` for MM-404 after implementation and tests pass, equivalent to the final `/speckit.verify` gate, then record the verification verdict and evidence in `specs/207-jira-breakdown-orchestrate-skill/verification.md`

## Dependencies And Execution Order

1. Complete setup tasks T001-T002.
2. Write tests T003-T008 before production changes.
3. Run T009 and confirm targeted tests fail for the expected missing behavior.
4. Implement T010-T018.
5. Run story validation T019-T021.
6. Finish with T022-T026.

## Parallel Opportunities

- T003-T008 can be drafted in parallel because they touch separate test files except T004 and T005, which both touch `tests/unit/workflows/temporal/test_story_output_tools.py` and must be coordinated.
- T022 and T023 can run in parallel after implementation because they are read-only checks.

## Implementation Strategy

Start by proving the new composite preset and downstream task creation behavior are missing. Implement the seeded preset and deterministic story output task creator after red-first confirmation. Reuse existing Jira issue creation output, existing task submission/dependency validation, and existing Jira Orchestrate behavior. Do not add new database tables or replace existing Jira Breakdown/Jira Orchestrate presets. Preserve MM-404 traceability through every artifact and final verification.
