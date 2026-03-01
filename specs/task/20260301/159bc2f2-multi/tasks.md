# Tasks: Task Presets Strategy Alignment

**Input**: Design artifacts from `/specs/024-task-presets/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), plus `research.md`, `data-model.md`, `contracts/`, `quickstart.md`
**Tests**: Run validation via `./tools/test_unit.sh` (never direct `pytest`) per repository policy.
**Organization**: Tasks are grouped by user story so each slice remains independently testable and deployable.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Align feature artifacts and baseline references before runtime edits.

- [X] T001 Update implementation inventory and structure notes in `specs/024-task-presets/plan.md` so listed source/test paths match the current repository layout.
- [X] T002 [P] Align requirement, scenario, and success-criteria language with MoonMind publish-stage strategy in `specs/024-task-presets/spec.md`.
- [X] T003 [P] Refresh decision/context docs in `specs/024-task-presets/research.md`, `specs/024-task-presets/data-model.md`, and `specs/024-task-presets/quickstart.md` for current runtime/docs mode behavior.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the canonical preset/contract baseline that user stories depend on.

- [X] T004 Update `specs/024-task-presets/contracts/task-step-templates.yaml` by reconciling endpoint surface and operations with `api_service/api/routers/task_step_templates.py` and `api_service/services/task_templates/catalog.py`.
- [X] T005 Ensure `api_service/data/task_step_templates/speckit-orchestrate.yaml` keeps mode-aware validation placeholders (including `validate-implementation-scope.sh --check tasks --mode {{ inputs.orchestration_mode }}`) so runtime/docs behavior remains explicit.

**Checkpoint**: Spec artifacts and shared preset baseline are consistent, enabling story implementation.

---

## Phase 3: User Story 1 - Runtime orchestration preset follows MoonMind publish strategy (Priority: P1) 🎯 MVP

**Goal**: Runtime expansion of `speckit-orchestrate` prohibits direct publish actions and returns a final report for wrapper publish handling.

**Independent Test**: Expand the seeded preset and verify final instructions explicitly say runtime must not commit/push/open PRs and must return handoff report details.

### Tests for User Story 1

- [X] T006 [P] [US1] Extend regression coverage in `tests/unit/api/test_task_template_seed_alignment.py` to assert final-step instruction language blocks runtime commit/push/PR actions and preserves publish-stage handoff wording.

### Implementation for User Story 1

- [X] T007 [US1] Update final-step instructions in `api_service/data/task_step_templates/speckit-orchestrate.yaml` so runtime execution returns a report and defers commit/PR behavior to MoonMind publish stage.
- [X] T008 [US1] Keep `api_service/data/task_step_templates/speckit-orchestrate.yaml` required capabilities runtime-neutral by ensuring no GitHub-only capability requirements are introduced in the final-step skill configuration.

**Checkpoint**: Seeded runtime instructions match publish-stage ownership and are regression-protected.

---

## Phase 4: User Story 2 - Existing deployments receive preset behavior updates safely (Priority: P1)

**Goal**: Existing environments refresh stored `speckit-orchestrate` template/version payloads from the latest YAML seed through an idempotent migration.

**Independent Test**: Run migration with pre-seeded records and verify `required_capabilities` and `steps` are refreshed; run against missing seed/rows and verify clean no-op behavior.

### Tests for User Story 2

- [X] T009 [P] [US2] Expand `tests/unit/api/test_task_template_seed_alignment.py` with seed-shape assertions that protect migration-required fields (`slug`, `scope`, `version`, `requiredCapabilities`, `steps`) from regression drift.

### Implementation for User Story 2

- [X] T010 [US2] Implement/refresh idempotent data-alignment logic in `api_service/migrations/versions/202603010001_align_speckit_orchestrate_publish_stage.py` to load the YAML seed and safely no-op when seed file or target rows are missing.
- [X] T011 [US2] Update `api_service/migrations/versions/202603010001_align_speckit_orchestrate_publish_stage.py` to synchronize `task_step_templates.required_capabilities` and `task_step_template_versions.required_capabilities`, `steps`, and `seed_source` from the seed document.

**Checkpoint**: Migration upgrades existing deployments to the current preset behavior without brittle/manual SQL patches.

---

## Phase 5: User Story 3 - Spec artifact set reflects current implemented architecture (Priority: P2)

**Goal**: `specs/024-task-presets` documents match current migration IDs, runtime surfaces, and validation/test paths used in the repo.

**Independent Test**: Cross-check every referenced implementation and test path in `spec.md`, `plan.md`, `tasks.md`, and contract docs against existing files in repository root.

### Implementation for User Story 3

- [X] T012 [US3] Update architecture/path references in `specs/024-task-presets/spec.md` and `specs/024-task-presets/plan.md` to match current runtime modules, migration file, and test locations.
- [X] T013 [P] [US3] Refresh verification flow and artifact expectations in `specs/024-task-presets/quickstart.md` and `specs/024-task-presets/contracts/task-step-templates.yaml` so operator checks reflect current API + preset behavior.
- [X] T014 [P] [US3] Align traceability details in `specs/024-task-presets/research.md` and `specs/024-task-presets/data-model.md` with implemented seed-source-of-truth and migration invariants.

**Checkpoint**: Feature artifacts are an accurate operational reference for the implemented system.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Run required validation and enforce runtime implementation gates.

- [X] T015 [P] Run `./tools/test_unit.sh` from repo root and confirm preset-alignment regression tests pass alongside the broader unit suite.
- [X] T016 Execute `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` after task regeneration to confirm runtime task + validation task coverage.
- [X] T017 Run `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` after implementation edits to verify runtime/test diff scope before handoff.

---

## Dependencies & Execution Order

1. Phase 1 (Setup) must complete first to align artifacts and references.
2. Phase 2 (Foundational) depends on Phase 1 and blocks all story work.
3. Phase 3 (US1) depends on Phase 2 and defines MVP runtime behavior.
4. Phase 4 (US2) depends on Phase 3 because migration must align with the finalized seed contract.
5. Phase 5 (US3) depends on Phases 3-4 so docs capture final implemented behavior.
6. Phase 6 (Polish) runs last after implementation changes are complete.

## User Story Dependencies

- **US1 (P1)**: No user-story dependency once foundational work is done.
- **US2 (P1)**: Depends on US1 seed contract finalization.
- **US3 (P2)**: Depends on US1 and US2 outcomes to document the shipped state accurately.

## Parallel Opportunities

- Phase 1: T002 and T003 can run in parallel.
- Phase 3: T006 can run in parallel with T007 once Phase 2 is complete.
- Phase 5: T013 and T014 can run in parallel after T012 anchors core references.
- Phase 6: T015 can run in parallel with pre-check preparation for T016/T017, but T017 must run after implementation changes are present.

## Parallel Example: User Story 1

```bash
# Parallelizable US1 tasks after foundational completion:
Task: "T006 [US1] Extend regression coverage in tests/unit/api/test_task_template_seed_alignment.py"
Task: "T007 [US1] Update final-step publish handoff instructions in api_service/data/task_step_templates/speckit-orchestrate.yaml"
```

## Parallel Example: User Story 2

```bash
# Run seed-shape regression assertions while migration logic is being finalized:
Task: "T009 [US2] Add migration-required seed-shape assertions in tests/unit/api/test_task_template_seed_alignment.py"
Task: "T010 [US2] Implement idempotent alignment migration in api_service/migrations/versions/202603010001_align_speckit_orchestrate_publish_stage.py"
```

## Parallel Example: User Story 3

```bash
# Documentation alignment tasks can be split after core spec/plan references are updated:
Task: "T013 [US3] Refresh quickstart + API contract docs"
Task: "T014 [US3] Align research + data model traceability notes"
```

## Implementation Strategy

1. Complete Phases 1-2 to establish accurate artifacts and shared baseline.
2. Deliver MVP by finishing US1 (Phase 3) and validating runtime publish-stage-safe behavior.
3. Implement US2 migration alignment for existing deployments.
4. Update US3 artifact references after runtime behavior is finalized.
5. Run required validation commands in Phase 6 before handoff.
