# Tasks: Canonical Workflow Surface Naming

**Input**: Design documents from `/specs/040-spec-removal/`
**Prerequisites**: `plan.md` (required), `spec.md` (required for user stories), `research.md`, `data-model.md`, `contracts/`

**Tests**: Validation tests are required by this feature (`FR-010`, `DOC-REQ-011`) and are included per story.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- [ ] `T###` task IDs are sequential and execution-ordered.
- [ ] `[P]` marks tasks that can run in parallel.
- [ ] `[US#]` labels story-phase tasks only.
- [ ] Every task includes explicit file paths.

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly present: `T004-T006`, `T017-T021`.
- Runtime validation tasks are explicitly present: `T015`, `T016`, `T022`, `T023`.
- `DOC-REQ-*` implementation + validation coverage is enforced by `T007` and `T025`, with mapping persisted in `specs/040-spec-removal/contracts/requirements-traceability.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Lock migration scope, token map, and execution baseline before edits.

- [X] T001 [P] Finalize the canonical token map and exception policy in `docs/SpecRemovalPlan.md` and `specs/040-spec-removal/contracts/workflow-naming-contract.md` (DOC-REQ-001, DOC-REQ-007).
- [X] T002 [P] Add deterministic baseline scan commands and output locations to `specs/040-spec-removal/quickstart.md` and `specs/040-spec-removal/plan.md` (DOC-REQ-008).
- [X] T003 [P] Create a reusable verification helper at `tools/verify_workflow_naming.sh` for docs/spec/runtime token scans and approved exception filtering (DOC-REQ-008, DOC-REQ-010).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Complete shared runtime foundations and traceability needed by all stories.

- [ ] T004 Update canonical workflow env/settings keys in `moonmind/config/settings.py`, `api_service/config.template.toml`, and `services/orchestrator/entrypoint.sh` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-011).
- [ ] T005 [P] Normalize schema/type naming from `Workflow*` to `Workflow*` in `moonmind/schemas/workflow_models.py` and `moonmind/workflows/agentkit_celery/models.py` (DOC-REQ-004, DOC-REQ-011).
- [ ] T006 [P] Canonicalize artifact root handling in `moonmind/workflows/agentkit_celery/storage.py` and `moonmind/workflows/agentkit_celery/tasks.py` with workflow path naming invariants (DOC-REQ-006, DOC-REQ-011).
- [X] T007 Update `specs/040-spec-removal/contracts/requirements-traceability.md` with one-to-one mapping for `DOC-REQ-001` through `DOC-REQ-011`, including planned runtime and validation surfaces.

**Checkpoint**: Foundational runtime/config contract is ready for story implementation.

---

## Phase 3: User Story 1 - Canonical migration of surface vocabulary (Priority: P1) 🎯 MVP

**Goal**: Replace legacy naming in planned docs/spec surfaces with canonical workflow terminology.

**Independent Test**: Run docs/spec scan and verify no unapproved matches for legacy tokens outside the historical appendix.

### Tests for User Story 1

- [X] T008 [US1] Add docs/spec verification command usage examples to `specs/040-spec-removal/quickstart.md` and `docs/SpecRemovalPlan.md`, including expected pass/fail criteria (DOC-REQ-008, DOC-REQ-010).
- [ ] T009 [US1] Add docs/spec naming regression coverage in `tests/unit/workflows/test_automation_env.py` and `tests/unit/api/test_automation.py` for canonical token assertions in exposed guidance surfaces (DOC-REQ-001, DOC-REQ-007).

### Implementation for User Story 1

- [ ] T010 [P] [US1] Migrate canonical terminology across operational docs in `docs/CodexCliWorkers.md`, `docs/LiveTaskHandoff.md`, `docs/MemoryArchitecture.md`, and `docs/OrchestratorArchitecture.md` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-007).
- [ ] T011 [P] [US1] Migrate canonical terminology across automation docs in `docs/AgentKitAutomation.md`, `docs/AgentKitAutomationInstructions.md`, `docs/TaskQueueSystem.md`, `docs/TasksStepSystem.md`, and `docs/ops-runbook.md` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-005, DOC-REQ-007).
- [ ] T012 [P] [US1] Apply canonical naming updates to `specs/001-celery-chain-workflow/spec.md`, `specs/001-celery-chain-workflow/plan.md`, `specs/001-celery-chain-workflow/research.md`, `specs/001-celery-chain-workflow/quickstart.md`, and `specs/001-celery-chain-workflow/data-model.md` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-005).
- [ ] T013 [P] [US1] Apply canonical naming updates to `specs/002-document-agentkit-automation/spec.md`, `specs/002-document-agentkit-automation/plan.md`, `specs/002-document-agentkit-automation/quickstart.md`, and `specs/002-document-agentkit-automation/tasks.md` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-007).
- [ ] T014 [P] [US1] Apply canonical naming updates to runtime-adjacent specs in `specs/003-celery-oauth-volumes/tasks.md`, `specs/005-orchestrator-architecture/spec.md`, `specs/005-orchestrator-architecture/plan.md`, and `specs/031-manifest-phase0/plan.md` (DOC-REQ-002, DOC-REQ-005, DOC-REQ-006).

**Checkpoint**: User Story 1 is independently complete with canonical docs/spec vocabulary.

---

## Phase 4: User Story 2 - Runtime surface alignment (Priority: P2)

**Goal**: Align production runtime surfaces with canonical workflow naming while preserving behavior.

**Independent Test**: Run API/config/metrics/artifact validation tests; confirm canonical names with no semantic regression.

### Tests for User Story 2

- [ ] T015 [P] [US2] Update route and contract regression tests in `tests/contract/test_workflow_api.py`, `tests/unit/api/test_automation.py`, and `tests/unit/api/routers/test_task_runs.py` for canonical `/api/workflows/*` behavior (DOC-REQ-003, DOC-REQ-004, DOC-REQ-011).
- [ ] T016 [P] [US2] Update runtime naming regression tests in `tests/unit/config/test_settings.py`, `tests/unit/workflows/test_automation_env.py`, and `tests/unit/workflows/test_storage.py` for env/settings/metric/artifact canonicalization (DOC-REQ-001, DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-011).

### Implementation for User Story 2

- [ ] T017 [US2] Canonicalize workflow route families in `api_service/api/routers/workflows.py`, `api_service/api/routers/automation.py`, and `api_service/main.py` (DOC-REQ-003, DOC-REQ-011).
- [ ] T018 [US2] Replace remaining legacy env and settings names in `moonmind/config/settings.py`, `api_service/auth_providers.py`, and `services/orchestrator/entrypoint.sh` with fail-fast handling for unsupported legacy inputs (DOC-REQ-001, DOC-REQ-002, DOC-REQ-007, DOC-REQ-011).
- [ ] T019 [US2] Canonicalize runtime schema identifiers in `moonmind/schemas/workflow_models.py`, `moonmind/workflows/agentkit_celery/models.py`, and `moonmind/workflows/agentkit_celery/serializers.py` (DOC-REQ-004, DOC-REQ-011).
- [ ] T020 [US2] Canonicalize metrics namespace usage in `moonmind/workflows/orchestrator/metrics.py` and `moonmind/agents/codex_worker/metrics.py` (DOC-REQ-005, DOC-REQ-011).
- [ ] T021 [US2] Canonicalize artifact directory naming and references in `moonmind/workflows/agentkit_celery/storage.py`, `moonmind/workflows/agentkit_celery/tasks.py`, and `services/orchestrator/entrypoint.sh` (DOC-REQ-006, DOC-REQ-011).

**Checkpoint**: User Story 2 is independently complete with runtime parity and validation coverage.

---

## Phase 5: User Story 3 - Verification and governance (Priority: P3)

**Goal**: Produce auditable evidence that legacy naming is removed or explicitly approved as historical exceptions.

**Independent Test**: Execute verification commands and confirm a complete residual report plus requirement-by-requirement evidence.

### Tests for User Story 3

- [ ] T022 [US3] Add verification test coverage for the scan helper in `tests/unit/workflows/test_tasks.py` and `tests/unit/workflows/orchestrator/test_tasks.py`, including approved-exception handling (DOC-REQ-008, DOC-REQ-010).
- [X] T023 [US3] Run full validation via `./tools/test_unit.sh` and `./tools/verify_workflow_naming.sh`, including explicit check that docs-only baseline context remains historical while runtime intent stays authoritative, then record outputs in `specs/040-spec-removal/quickstart.md` (DOC-REQ-008, DOC-REQ-009, DOC-REQ-011).

### Implementation for User Story 3

- [X] T024 [US3] Add a historical-reference appendix with explicit residual rationale in `docs/SpecRemovalPlan.md` and `specs/040-spec-removal/spec.md` (DOC-REQ-007, DOC-REQ-009, DOC-REQ-010).
- [X] T025 [US3] Update `specs/040-spec-removal/contracts/requirements-traceability.md` so each `DOC-REQ-*` has at least one implementation task reference and one validation task reference.
- [X] T026 [US3] Record migration verification report details (changed files, residual matches, follow-ups, sign-off owner) in `specs/040-spec-removal/plan.md` and `specs/040-spec-removal/research.md` (DOC-REQ-008, DOC-REQ-010).

**Checkpoint**: User Story 3 is independently complete with audit-ready verification artifacts.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final consistency sweep and release-readiness checks.

- [ ] T027 [P] Remove remaining operational aliasing language from `docs/AgentKitAutomation.md`, `docs/AgentKitAutomationInstructions.md`, `docs/TaskQueueSystem.md`, and `docs/ops-runbook.md` (DOC-REQ-007).
- [ ] T028 [P] Align contract wording and operation naming in `specs/001-celery-chain-workflow/contracts/workflow.openapi.yaml`, `specs/002-document-agentkit-automation/contracts/workflow.openapi.yaml`, and `specs/005-orchestrator-architecture/contracts/orchestrator.openapi.yaml` (DOC-REQ-003, DOC-REQ-004).
- [ ] T029 Execute final docs/spec/runtime scans with `./tools/verify_workflow_naming.sh` and capture final pass evidence in `specs/040-spec-removal/quickstart.md` and `specs/040-spec-removal/plan.md` (DOC-REQ-008, DOC-REQ-010).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 (Setup) has no dependencies.
- Phase 2 (Foundational) depends on Phase 1 and blocks story execution.
- Phases 3, 4, and 5 depend on Phase 2 completion.
- Phase 6 depends on completion of all user stories.

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2 and delivers MVP canonical vocabulary.
- **US2 (P2)**: Starts after Phase 2; depends on shared canonical map and extends to runtime parity.
- **US3 (P3)**: Starts after US1 and US2 produce migration edits to verify.

### Within Each User Story

- Validation tasks are completed before finalizing implementation sign-off.
- Runtime semantics-preservation tasks must complete before cross-cutting polish.
- Each story must satisfy its independent test criteria before moving forward.

## Parallel Opportunities

- T001-T003 can run in parallel.
- T005 and T006 can run in parallel after T004 begins.
- US1 implementation tasks T010-T014 can run in parallel by file groups.
- US2 test tasks T015-T016 can run in parallel, followed by implementation tasks T017-T021 by subsystem.
- US3 tasks T022 and T024-T026 can run in parallel once US1/US2 edits are available.

## Parallel Example: User Story 2

```bash
# Parallel test updates
Task: T015 tests/contract/test_workflow_api.py + tests/unit/api/test_automation.py
Task: T016 tests/unit/config/test_settings.py + tests/unit/workflows/test_storage.py

# Parallel runtime implementation by subsystem
Task: T017 api_service/api/routers/workflows.py + api_service/api/routers/automation.py
Task: T020 moonmind/workflows/orchestrator/metrics.py + moonmind/agents/codex_worker/metrics.py
Task: T021 moonmind/workflows/agentkit_celery/storage.py + services/orchestrator/entrypoint.sh
```

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phase 1 and Phase 2.
2. Complete US1 (Phase 3) canonical docs/spec migration.
3. Validate US1 independently using T008-T009 before starting runtime parity.

### Incremental Delivery

1. Deliver US1 for canonical documentation/spec vocabulary.
2. Deliver US2 for production runtime naming parity and regression tests.
3. Deliver US3 for verification evidence, governance, and residual handling.
4. Complete Phase 6 cross-cutting polish and final scan evidence.

## Task Summary & Validation

- Total tasks: **29**
- Task count by user story: **US1: 7**, **US2: 7**, **US3: 5**
- Non-story tasks (Setup/Foundational/Polish): **10**
- Parallel opportunities identified: **Yes** (Setup, US1 file groups, US2 subsystem split, US3 report assembly)
- Independent test criteria: Defined in each user story phase and tied to validation tasks
- Suggested MVP scope: **Phase 1 + Phase 2 + Phase 3 (US1)**
- Format validation: All tasks follow `- [ ] T### [P?] [US?] Description with file path`
- Prompt B runtime gate: Runtime implementation + validation task coverage is explicitly present and traceable to `DOC-REQ-011`.
