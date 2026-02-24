# Tasks: Canonical Workflow Surface Naming

**Input**: Design documents from `/specs/040-spec-removal/`
**Prerequisites**: `plan.md` (required), `spec.md` (required for user stories), `research.md`, `data-model.md`, `contracts/`

**Tests**: This feature is docs/specs migration only for this slice; runtime validation is tracked in US4 (`T041`) and overall coverage checks remain explicit in US3.

**Organization**: Tasks are grouped by user story and are independently testable.

## Format: `[ID] [P?] [Story] Description`

- [ ]: Task checkbox
- `T###`: Sequential task id
- `[P]`: Parallelizable task
- `[US#]`: Story label required for user story phases only
- Must include concrete file paths

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish migration controls and explicit scope before edits.

- [ ] T001 [P] Publish the final canonical legacy-to-canonical mapping table and allowed exception policy in `docs/SpecRemovalPlan.md`.
- [ ] T002 [P] Add the full phase-0 through phase-3 target file list to `specs/040-spec-removal/plan.md` for execution traceability.
- [ ] T003 [P] Add a baseline token-discovery command reference in `specs/040-spec-removal/quickstart.md` that matches `docs/SpecRemovalPlan.md` scope.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare governance, scope guardrails, and execution traceability required by all stories.

- [ ] T004 [P] Update `specs/040-spec-removal/data-model.md` with explicit migration boundary entities, including `HistoricalReferenceException` and `VerificationFinding` constraints.
- [ ] T005 Update `specs/040-spec-removal/contracts/requirements-traceability.md` to map all `DOC-REQ-*` entries to concrete implementation/validation surfaces.
- [ ] T006 Add a completion checkpoint and residual-risk rubric to `specs/040-spec-removal/checklists/requirements.md` for this migration wave.

---

## Phase 3: User Story 1 - Canonical migration of surface vocabulary (Priority: P1) 🎯 MVP

**Goal**: Canonical workflow terms replace legacy `SPEC` surface wording across all allowed files.

**Independent Test**: Run the discovery check against listed files and verify `SPEC_WORKFLOW_*`, `SPEC_AUTOMATION`, `/api/spec-automation/*`, `SpecWorkflow*`, `spec_workflow*`, and `spec_workflows` are absent except explicitly documented exceptions.

### Tests for User Story 1 (Validation)

- [ ] T007 [US1] Add a pre/post token-lint command in `specs/040-spec-removal/quickstart.md` and `docs/SpecRemovalPlan.md` for docs-only verification.

### Implementation for User Story 1

- [ ] T008 [P] [US1] Update documentation vocabulary in `docs/CodexCliWorkers.md`, `docs/LiveTaskHandoff.md`, `docs/LlamaIndexManifestSystem.md`, `docs/MemoryArchitecture.md`, `docs/OrchestratorArchitecture.md`, `docs/SpecKitAutomation.md`, `docs/SpecKitAutomationInstructions.md`, `docs/TaskQueueSystem.md`, `docs/TasksJira.md`, `docs/TasksStepSystem.md`, and `docs/ops-runbook.md`.
- [ ] T009 [P] [US1] Migrate legacy terms in `specs/001-celery-chain-workflow/data-model.md`, `specs/001-celery-chain-workflow/plan.md`, `specs/001-celery-chain-workflow/quickstart.md`, `specs/001-celery-chain-workflow/research.md`, `specs/001-celery-chain-workflow/spec.md`, `specs/001-celery-chain-workflow/contracts/workflow.openapi.yaml`.
- [ ] T010 [P] [US1] Migrate legacy terms in `specs/002-document-speckit-automation/AGENTS.md`, `specs/002-document-speckit-automation/plan.md`, `specs/002-document-speckit-automation/quickstart.md`, `specs/002-document-speckit-automation/spec.md`, `specs/002-document-speckit-automation/contracts/workflow.openapi.yaml`.
- [ ] T011 [P] [US1] Migrate legacy terms in `specs/003-celery-oauth-volumes/quickstart.md` and `specs/003-celery-oauth-volumes/tasks.md`.
- [ ] T012 [P] [US1] Migrate legacy terms in `specs/005-orchestrator-architecture/data-model.md`, `specs/005-orchestrator-architecture/plan.md`, `specs/005-orchestrator-architecture/quickstart.md`, `specs/005-orchestrator-architecture/research.md`, `specs/005-orchestrator-architecture/spec.md`, `specs/005-orchestrator-architecture/contracts/orchestrator.openapi.yaml`.
- [ ] T013 [P] [US1] Migrate legacy terms in `specs/007-scalable-codex-worker/checklists/requirements.md`, `specs/007-scalable-codex-worker/data-model.md`, `specs/007-scalable-codex-worker/plan.md`, `specs/007-scalable-codex-worker/quickstart.md`, `specs/007-scalable-codex-worker/research.md`, `specs/007-scalable-codex-worker/spec.md`.
- [X] T014 [P] [US1] Migrate legacy terms in `specs/008-gemini-cli-worker/tasks.md`.
- [ ] T015 [US1] Migrate legacy terms in `specs/009-agent-queue-mvp/research.md` and `specs/009-agent-queue-mvp/plan.md` references where present.
- [ ] T016 [P] [US1] Migrate legacy terms in `specs/011-remote-worker-daemon/contracts/codex-worker-runtime-contract.md`, `specs/011-remote-worker-daemon/contracts/requirements-traceability.md`, `specs/011-remote-worker-daemon/data-model.md`, `specs/011-remote-worker-daemon/plan.md`, `specs/011-remote-worker-daemon/quickstart.md`, `specs/011-remote-worker-daemon/research.md`, `specs/011-remote-worker-daemon/spec.md`.
- [ ] T017 [P] [US1] Migrate legacy terms in `specs/015-skills-workflow/contracts/compose-fast-path.md`, `specs/015-skills-workflow/data-model.md`, `specs/015-skills-workflow/plan.md`, `specs/015-skills-workflow/research.md`, `specs/015-skills-workflow/spec.md`, and `specs/015-skills-workflow/tasks.md`.
- [ ] T018 [P] [US1] Migrate legacy terms in `specs/016-shared-agent-skills/contracts/shared-skills-workspace-contract.md` and `specs/016-shared-agent-skills/quickstart.md`.
- [ ] T019 [P] [US1] Migrate legacy terms in `specs/018-unified-cli-queue/contracts/worker-runtime-contract.md`, `specs/018-unified-cli-queue/contracts/requirements-traceability.md`, `specs/018-unified-cli-queue/plan.md`, `specs/018-unified-cli-queue/research.md`, `specs/018-unified-cli-queue/spec.md`, and `specs/018-unified-cli-queue/tasks.md`.
- [ ] T020 [P] [US1] Migrate legacy terms in `specs/031-manifest-phase0/plan.md` and `specs/031-manifest-phase0/tasks.md`.
- [ ] T021 [P] [US1] Migrate legacy terms in `specs/034-task-proposal-update/plan.md`, `specs/034-task-proposal-update/research.md`, and `specs/034-task-proposal-update/tasks.md`.
- [ ] T022 [P] [US1] Migrate legacy terms in `specs/034-worker-self-heal/quickstart.md` and `specs/034-worker-self-heal/research.md`.
- [ ] T023 [P] [US1] Migrate legacy terms in `specs/036-isolate-speckit-references/contracts/skill-adapter-contract.md`, `specs/036-isolate-speckit-references/contracts/workflow-runs-api.md`, `specs/036-isolate-speckit-references/data-model.md`, `specs/036-isolate-speckit-references/plan.md`, `specs/036-isolate-speckit-references/quickstart.md`, `specs/036-isolate-speckit-references/research.md`, `specs/036-isolate-speckit-references/spec.md`, and `specs/036-isolate-speckit-references/tasks.md`.
- [ ] T024 [P] [US1] Migrate legacy terms in `specs/037-tasks-image-phase1/data-model.md`, `specs/037-tasks-image-phase1/plan.md`, and `specs/037-tasks-image-phase1/tasks.md`.
- [ ] T025 [P] [US1] Migrate legacy terms in `specs/038-claude-runtime-gate/contracts/task_dashboard_config.md`, `specs/038-claude-runtime-gate/data-model.md`, `specs/038-claude-runtime-gate/plan.md`, `specs/038-claude-runtime-gate/research.md`, and `specs/038-claude-runtime-gate/tasks.md`.

---

## Phase 4: User Story 2 - Runtime surface alignment (Priority: P2)

**Goal**: Bring docs/spec references for runtime naming surfaces, API families, and artifacts into canonical form without changing runtime behavior.

**Independent Test**: Verify all canonical route, metric, and artifact references in runtime-facing sections across migrated files, while ensuring aliases are not present in active guidance.

### Implementation for User Story 2

- [ ] T026 [P] [US2] Update runtime-facing API route references in `docs/SpecKitAutomation.md`, `docs/ops-runbook.md`, `specs/002-document-speckit-automation/contracts/workflow.openapi.yaml`, `specs/001-celery-chain-workflow/contracts/workflow.openapi.yaml`, and `specs/005-orchestrator-architecture/contracts/orchestrator.openapi.yaml` from `/api/spec-automation/*` / `/api/workflows/speckit/*` to `/api/workflows/*`.
- [ ] T027 [P] [US2] Replace `SpecWorkflow*` schema and identifier names with `Workflow*` in `specs/001-celery-chain-workflow/contracts/workflow.openapi.yaml`, `specs/002-document-speckit-automation/contracts/workflow.openapi.yaml`, and `specs/005-orchestrator-architecture/contracts/orchestrator.openapi.yaml`.
- [ ] T028 [US2] Normalize `SPEC_WORKFLOW_*`/`spec_workflow*` and `moonmind.spec_workflow*` references to canonical forms in `docs/SpecKitAutomation.md`, `docs/MemoryArchitecture.md`, `docs/TaskQueueSystem.md`, `docs/TasksStepSystem.md`, `docs/ops-runbook.md`, `docs/OrchestratorArchitecture.md`, and `specs/005-orchestrator-architecture/plan.md`.
- [ ] T029 [US2] Update artifact path naming references from `var/artifacts/spec_workflows` to `var/artifacts/workflow_runs` or `var/artifacts/workflows` in `docs/SpecRemovalPlan.md`, `docs/SpecKitAutomation.md`, and `specs/001-celery-chain-workflow/spec.md`.

---

## Phase 5: User Story 3 - Verification and governance (Priority: P3)

**Goal**: Create an auditable completion record proving legacy-token removal is complete and bounded.

**Independent Test**: Produce and archive a baseline-vs-post run showing only approved historical references and an explicit residual follow-up list.

### Tests for User Story 3

- [ ] T030 [P] [US3] Implement verification pass command and expected outputs in `specs/040-spec-removal/quickstart.md` using the migration token list from `docs/SpecRemovalPlan.md`.
- [ ] T031 [P] [US3] Add explicit historical-reference exception capture in `docs/SpecRemovalPlan.md` and ensure all residual legacy occurrences are listed with rationale.

### Implementation for User Story 3

- [ ] T032 [US3] Update `specs/040-spec-removal/contracts/requirements-traceability.md` to include each `DOC-REQ-*` validation artifact path and verification command references.
- [ ] T033 [US3] Record plan outcomes and residuals in `specs/040-spec-removal/plan.md` and `specs/040-spec-removal/research.md`.
- [ ] T034 [US3] Add closure criteria and handoff checklist for unresolved legacy references in `specs/040-spec-removal/spec.md` and `specs/040-spec-removal/quickstart.md`.
- [ ] T035 [US3] Add a final compliance check pass summary in `specs/040-spec-removal/plan.md` with one-line proof per `DOC-REQ-*`.

### Step 9 Coverage Gate

- [ ] T042 [US1] Add explicit implementation coverage artifacts for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`, and `DOC-REQ-010` across docs/spec execution (`T008`–`T039`) and runtime follow-up (`T040`/`T041`) paths.
- [ ] T043 [US3] Add explicit validation checkpoints for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-006`, `DOC-REQ-007`, `DOC-REQ-008`, `DOC-REQ-009`, and `DOC-REQ-010` with evidence recorded in `specs/040-spec-removal/quickstart.md`, `specs/040-spec-removal/contracts/requirements-traceability.md`, and `specs/040-spec-removal/plan.md`.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Normalize all cross-cutting references and lock migration intent.

- [ ] T036 [P] Remove transition language (aliases, deprecated nomenclature notes) from active guidance in `docs/CodexCliWorkers.md`, `docs/SpecKitAutomationInstructions.md`, `docs/TaskQueueSystem.md`, and `docs/TasksStepSystem.md`.
- [ ] T037 Update `specs/040-spec-removal/research.md` with lessons learned and follow-up scope for any excluded runtime surfaces.
- [ ] T038 [P] Review and align all `checklists/requirements.md` files touched by this migration (`specs/040-spec-removal/checklists/requirements.md`) against the updated `DOC-REQ-*` map.
- [ ] T039 [P] Consolidate file-status markers and final completion checklist in `specs/040-spec-removal/plan.md` and `specs/040-spec-removal/spec.md`.

## Phase 7: Runtime Scope Remediation

- [ ] T040 [P] [US4] Add a runtime implementation task in `api_service/api/routers/workflows.py`, `api_service/api/routers/spec_automation.py`, and `services/orchestrator/entrypoint.sh` to track canonical naming alignment work required for runtime API migration evidence.
- [ ] T041 [P] [US4] Add regression coverage for workflow naming migration in `tests/test_workflow_renaming.py`.

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 (Setup) can start immediately.
- Phase 2 (Foundational) depends on Phase 1 completion.
- User Story phases (3–5) depend on Phases 1–2 completion.
- Phase 6 depends on all applicable user stories completing to keep release notes consistent.
- Phase 7 (US4 runtime follow-up) depends on US1/US2/US3 completion or equivalent governance acceptance.

### User Story Dependencies

- **US1 (P1)**: No prerequisite on US2/US3; independent once Phase 2 is complete.
- **US2 (P2)**: Can run in parallel with US3 after Phase 2 and foundational migration edits.
- **US3 (P3)**: Can start after US1 and US2 updates provide measurable output artifacts.
- **US4 (P4)**: Runtime follow-up and regression evidence can start after planning execution artifacts are in place for traceability and should produce runtime-safe proof before rollout.

## Parallel Opportunities

- Setup tasks T001–T003 are parallelizable once context is loaded.
- Foundational tasks T004–T006 can run in parallel and require only baseline documentation.
- US1 tasks T008–T025 can be parallelized by file owner/domain because each touches distinct paths.
- US2 tasks T026 and T027 can run in parallel with `T028` and `T029` only after the corresponding target files are blocked for conflict-free edits.
- US3 verification tasks T030 and T031 should run after US1/US2 edits are drafted.

## Parallel Example: User Story 1

```bash
# Launch independent edits for high-volume docs/spec groups
Task: T008 on docs files
Task: T010 on specs/001-celery-chain-workflow files
Task: T017 on specs/015-skills-workflow files
Task: T023 on specs/036-isolate-speckit-references files
```

## Implementation Strategy

### MVP First (User Story 1)

1. Complete Phases 1–2.
2. Complete Phase 3 (US1) only, including canonical pass across listed `docs/` and `specs/` surfaces.
3. Validate via T030 baseline checks.
4. Add follow-up governance via Phase 5 before handoff.

### Incremental Delivery

1. Deliver US1 for maximum immediate terminology consistency value.
2. Add US2 for operational/runtime-surface alignment in documentation and contracts.
3. Add US3 governance tasks to prove completion and control residual exceptions.
4. Finish polish in Phase 6.
5. Complete US4 runtime follow-up and regression evidence.

## Task Summary & Validation

- Total tasks: **43**
- Per-story counts: **US1 – 20**, **US2 – 4**, **US3 – 7**, **US4 – 2**
- Parallel opportunities identified: **Yes** (`T008`–`T025`, `T026`/`T027`/`T028`/`T029`, and `T030`/`T031`).
- Independent test criteria: Listed per story above and backed by verification commands in `specs/040-spec-removal/quickstart.md`.
- Suggested MVP scope: Complete through Phase 3 (US1).
- Checklist compliance: All tasks use `- [ ] T### [P?] [US?]` format with explicit paths.
