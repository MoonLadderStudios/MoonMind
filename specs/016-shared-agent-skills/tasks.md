# Tasks: Unified Agent Skills Directory

**Input**: Design documents from `/specs/016-shared-agent-skills/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Align feature artifacts and create implementation scaffolding.

- [X] T001 Verify `specs/016-shared-agent-skills/` artifacts are internally consistent and implementation-ready.
- [X] T002 Create module scaffolding for skills runtime at `moonmind/workflows/skills/resolver.py`, `moonmind/workflows/skills/materializer.py`, and `moonmind/workflows/skills/workspace_links.py`.
- [X] T003 [P] Add unit test scaffolding in `tests/unit/workflows/test_skills_resolver.py`, `tests/unit/workflows/test_skills_materializer.py`, and `tests/unit/workflows/test_workspace_links.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Introduce resolver/materializer configuration and shared runtime contracts.

- [X] T004 Extend skills runtime settings in `moonmind/config/settings.py` for cache root, workspace root, registry source, local mirror source, and signature verification toggles.
- [X] T005 Implement run skill selection precedence logic in `moonmind/workflows/skills/resolver.py` (job overrides -> queue profile -> global defaults).
- [X] T006 Implement workspace symlink invariants in `moonmind/workflows/skills/workspace_links.py` for `skills_active`, `.agents/skills`, and `.gemini/skills`.
- [X] T007 Implement artifact verification and immutable cache materialization in `moonmind/workflows/skills/materializer.py`.
- [X] T008 Integrate resolver/materializer execution into workflow stage context setup in `moonmind/workflows/speckit_celery/tasks.py`.
- [X] T009 Extend `moonmind/workflows/speckit_celery/workspace.py` run paths with shared-skills directories used by resolver/materializer.

**Checkpoint**: Shared skills runtime can resolve and materialize a run workspace before stage execution.

---

## Phase 3: User Story 1 - Shared Skills Discovery for Both CLIs (Priority: P1) 🎯 MVP

**Goal**: Both Codex and Gemini discover the same active skill set from one runtime directory.

**Independent Test**: Materialize one run workspace and verify both adapter symlinks resolve to the same `skills_active` path.

### Tests for User Story 1

- [X] T010 [P] [US1] Add resolver policy tests for default selection and override precedence in `tests/unit/workflows/test_skills_resolver.py`.
- [X] T011 [P] [US1] Add workspace link invariant tests for `.agents/skills` and `.gemini/skills` in `tests/unit/workflows/test_workspace_links.py`.
- [X] T012 [P] [US1] Add materializer cache/link tests for successful activation paths in `tests/unit/workflows/test_skills_materializer.py`.

### Implementation for User Story 1

- [X] T013 [US1] Export new resolver/materializer/workspace-link helpers via `moonmind/workflows/skills/__init__.py`.
- [X] T014 [US1] Persist run-level materialization metadata in stage context and task payloads in `moonmind/workflows/speckit_celery/tasks.py`.

**Checkpoint**: Run context contains validated shared skills workspace metadata consumed by both agent adapters.

---

## Phase 4: User Story 2 - Per-Run Skill Selection Without Global Mutation (Priority: P1)

**Goal**: Run-specific skill availability is selected via policy/context and does not mutate global CLI config files.

**Independent Test**: Execute resolver/materializer twice with different run selections and verify isolated active sets and unchanged global config state.

### Tests for User Story 2

- [X] T015 [P] [US2] Add tests for duplicate-skill rejection and missing skill source failures in `tests/unit/workflows/test_skills_resolver.py`.
- [X] T016 [P] [US2] Add tests that materializer does not write global CLI settings and isolates run directories in `tests/unit/workflows/test_skills_materializer.py`.
- [X] T017 [P] [US2] Add task-level integration tests covering shared skills context propagation in `tests/unit/workflows/test_tasks.py`.

### Implementation for User Story 2

- [X] T018 [US2] Wire per-run selection inputs from task context (`skill_selection` and `skill_sources`) in `moonmind/workflows/speckit_celery/tasks.py`.
- [X] T019 [US2] Add run workspace skill path fields to `moonmind/workflows/speckit_celery/workspace.py` and ensure deterministic path creation.

**Checkpoint**: Per-run skill selection is deterministic and isolated from user-global Codex/Gemini state.

---

## Phase 5: User Story 3 - Trusted Skill Supply and Runtime Guardrails (Priority: P2)

**Goal**: Skill activation is blocked on integrity or metadata violations and produces actionable diagnostics.

**Independent Test**: Feed invalid skill metadata/hash and duplicate names, then verify fail-fast materialization errors.

### Tests for User Story 3

- [X] T020 [P] [US3] Add integrity validation failure tests (missing `SKILL.md`, hash mismatch) in `tests/unit/workflows/test_skills_materializer.py`.
- [X] T021 [P] [US3] Add worker startup checks for shared skills mirror readiness in `tests/unit/workflows/test_worker_entrypoints.py`.

### Implementation for User Story 3

- [X] T022 [US3] Add optional startup validation for shared skills mirror roots in `celery_worker/speckit_worker.py` and `celery_worker/gemini_worker.py`.
- [X] T023 [US3] Add materialization error codes/messages and structured telemetry fields in `moonmind/workflows/skills/materializer.py` and `moonmind/workflows/speckit_celery/tasks.py`.

**Checkpoint**: Invalid artifacts fail before CLI execution and diagnostics are visible in workflow metadata.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Complete documentation alignment, validation, and closure.

- [X] T024 [P] Update shared skills guidance in `README.md`, `AGENTS.md`, and `specs/016-shared-agent-skills/quickstart.md` for `.agents/skills` + `.gemini/skills`.
- [X] T025 [P] Align existing legacy assumptions referencing `.codex/skills` in `specs/001-celery-chain-workflow/spec.md`, `specs/002-document-speckit-automation/spec.md`, and `specs/015-skills-workflow/spec.md`.
- [X] T026 Run unit validation via `./tools/test_unit.sh`.
- [X] T027 Run manual implementation scope validation against tasks and git diff (repository does not include `.specify/scripts/bash/validate-implementation-scope.sh`).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phases 3/4/5 -> Phase 6.
- User stories depend on foundational tasks T004-T009.

### User Story Dependencies

- US1 is the MVP and starts immediately after foundational work.
- US2 depends on US1 resolver/materializer behavior and context propagation.
- US3 depends on resolver/materializer validation paths from US1/US2.

### Parallel Opportunities

- T003 can run in parallel with T002.
- T010-T012 are parallelizable.
- T015-T017 are parallelizable.
- T020-T021 are parallelizable.
- T024-T025 are parallelizable.

---

## Implementation Strategy

### MVP First (US1)

1. Complete setup and foundational resolver/materializer integration.
2. Deliver shared `skills_active` with dual adapter symlinks.
3. Validate with focused unit tests before broader hardening.

### Incremental Delivery

1. Add per-run selection and context propagation (US2).
2. Add integrity and startup guardrails (US3).
3. Finalize docs and run unit validation.

### Runtime Scope Commitments

- Production runtime code changes will be made under `moonmind/workflows/`, `moonmind/config/`, and `celery_worker/`.
- Validation includes updated unit coverage and execution via `./tools/test_unit.sh`.
