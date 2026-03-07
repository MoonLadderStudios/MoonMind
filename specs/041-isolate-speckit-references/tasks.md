# Tasks: Isolate Spec Kit References and Skill-First Runtime

**Input**: Design documents from `/specs/036-isolate-speckit-references/`  
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/, quickstart.md

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Establish shared adapter/dependency decision helpers (FR-001, FR-003) in `moonmind/workflows/skills/registry.py` and `moonmind/workflows/skills/contracts.py` to support explicit adapter resolution and dependency checks.
- [X] T002 [P] Add migration-facing documentation updates for canonical workflow route naming and legacy alias behavior (FR-009) in `specs/036-isolate-speckit-references/contracts/workflow-runs-api.md` and `README.md`.

---

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T003 Implement fail-fast adapter enforcement (FR-001, FR-004) in `moonmind/workflows/skills/runner.py` so unsupported skills error before execution instead of silently using direct mode.
- [X] T004 Update resolver compatibility behavior (FR-001) in `moonmind/workflows/skills/resolver.py` to keep skill-source resolution explicit and deterministic for selected skills.
- [X] T005 [P] Add unit coverage for adapter enforcement and resolver behavior (FR-010) in `tests/unit/workflows/test_skills_runner.py` and `tests/unit/workflows/test_skills_resolver.py`.

**Checkpoint**: Core skill execution semantics are explicit and test-backed.

---

## Phase 3: User Story 1 - Run Skill Workflows Without Installed Speckit Dependency (Priority: P1) 🎯 MVP

**Goal**: Non-speckit workflows execute successfully without global Speckit installation requirements.

**Independent Test**: Configure a non-speckit selected skill and verify workflow stage execution and worker preflight skip Speckit verification while preserving failure for speckit-selected paths.

- [X] T006 [US1] Scope Speckit verification to speckit-required stages (FR-002, FR-003) in `moonmind/workflows/speckit_celery/tasks.py` by gating `_log_spec_kit_cli_availability()` on adapter requirements.
- [X] T007 [US1] Scope worker preflight Speckit verification (FR-002, FR-003) in `moonmind/agents/codex_worker/cli.py` to only run when selected skills require Speckit.
- [X] T008 [P] [US1] Scope startup Speckit CLI checks (FR-002, FR-003) in `celery_worker/speckit_worker.py` and `celery_worker/gemini_worker.py` to speckit-required configurations.
- [X] T009 [US1] Add/adjust preflight tests for scoped Speckit checks (FR-010) in `tests/unit/agents/codex_worker/test_cli.py`.

**Checkpoint**: Non-speckit runtime paths are decoupled from Speckit executable requirements.

---

## Phase 4: User Story 2 - Use Neutral Workflow Naming With Compatibility Coverage (Priority: P2)

**Goal**: Canonical neutral workflow APIs/config names are available while SPEC-prefixed surfaces remain backward-compatible with deprecation signaling.

**Independent Test**: Hit canonical and legacy workflow endpoints for the same action; verify equivalent behavior and deprecation headers/logging for legacy paths.

- [X] T010 [US2] Introduce canonical workflow API routes with legacy alias compatibility and deprecation instrumentation while reusing existing persistence/repository paths (FR-005, FR-006, FR-007, FR-011) in `api_service/api/routers/workflows.py`.
- [X] T011 [P] [US2] Add canonical environment/config aliases while preserving SPEC-prefixed compatibility (FR-005, FR-006, FR-008) in `moonmind/config/settings.py` and `api_service/config.template.toml`.
- [X] T012 [US2] Align queue defaults and migration comments (FR-008, FR-009) in `.env-template` and `README.md`.
- [X] T013 [US2] Extend workflow API contract tests for canonical + legacy route behavior on unchanged persisted workflow records (FR-007, FR-010, FR-011) in `tests/contract/test_workflow_api.py`.

**Checkpoint**: Canonical naming is active and backward compatibility is preserved.

---

## Phase 5: User Story 3 - Fail Fast for Unsupported Skills Instead of Silent Fallbacks (Priority: P3)

**Goal**: Unsupported skills deterministically fail with actionable adapter errors and no hidden direct fallback.

**Independent Test**: Request an unregistered skill for a stage and verify deterministic adapter-missing failure payload/logs with no direct execution.

- [X] T014 [US3] Add actionable unsupported-skill error messaging and payload fields (FR-004, FR-010) in `moonmind/workflows/skills/runner.py` and `moonmind/workflows/speckit_celery/tasks.py`.
- [X] T015 [US3] Add/adjust stage execution tests for unsupported-skill failures and fallback rules (FR-010) in `tests/unit/workflows/test_skills_runner.py`.

**Checkpoint**: Unsupported-skill behavior is explicit, deterministic, and test-validated.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T016 [P] Update migration guidance docs for canonical workflow routes and deprecation headers (FR-009) in `docs/SpecKitAutomation.md`.
- [X] T017 Run targeted unit/contract validation for modified suites (FR-010) via `./tools/test_unit.sh`.
- [X] T018 Run runtime scope validation against implementation diff (FR-010) with `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 is required before story work.
- US1 depends on foundational adapter semantics from Phase 2.
- US2 and US3 both depend on Phase 2 and can proceed in parallel once US1 core changes land.
- Polish tasks run after all story phases complete.

## Parallel Opportunities

- T002 can run in parallel with T001.
- T005 can run in parallel with T003/T004 once interfaces stabilize.
- T008 can run in parallel with T006/T007.
- T011 can run in parallel with T010.
- T016 can run after API behavior is finalized while tests execute.

## Implementation Strategy

1. Deliver explicit adapter semantics first (Phases 1-2).
2. Deliver US1 decoupling as MVP.
3. Add canonical naming compatibility (US2).
4. Finalize explicit unsupported-skill failure behavior (US3).
5. Validate with tests and scope gate before handoff.
