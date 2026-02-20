# Tasks: Task Presets Catalog

**Input**: Design documents from `/specs/024-task-presets/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Add `task_template_catalog` feature flag defaults to `api_service/config.template.toml` and plumbing in `api_service/config/__init__.py` to make the catalog togglable per environment.
- [ ] T002 Scaffold `api_service/services/task_templates/` package with placeholder `__init__.py`, `catalog.py`, and `save.py` modules plus unit-test directories under `tests/api_service/`.
- [ ] T003 [P] Create `api_service/data/task_step_templates/` seed directory and capture initial YAML presets aligning with docs/TaskPresetsSystem.md examples.

---

## Phase 2: Foundational (Blocking Prerequisites)

- [ ] T004 Define SQLAlchemy models in `api_service/db/models.py` for `TaskStepTemplate`, `TaskStepTemplateVersion`, `TaskStepTemplateFavorite`, and `TaskStepTemplateRecent` plus enums and helper methods.
- [ ] T005 Generate Alembic migration `api_service/migrations/versions/024_task_step_templates.py` to create new tables, indexes, and seed loader hook referencing YAML defaults.
- [ ] T006 Implement catalog repository helpers in `api_service/services/task_templates/catalog.py` for CRUD, version hydration, deterministic ID generation, and capability aggregation.
- [ ] T007 Implement secret scrub + placeholder detection utilities in `api_service/services/task_templates/save.py` leveraging `moonmind.utils.secrets` and exposing reusable errors.
- [ ] T008 Extend task payload compiler (`moonmind/workflows/tasks/payload.py`) to merge template-derived capabilities and append `appliedStepTemplates` metadata before enqueueing.
- [ ] T009 Add Pydantic schemas in `api_service/api/schemas/task_step_templates.py` representing list/detail/create/expand/save payloads used by routers and tests.
- [ ] T010 [P] Build RBAC helpers in `api_service/api/dependencies/task_templates.py` to enforce scope visibility, review requirements, and rate limits for personal/team/global templates.
- [ ] T011 [P] Introduce telemetry + audit emitters in `api_service/services/task_templates/catalog.py` (e.g., StatsD counters/events) for template listing, expansion, and saving activity.

---

## Phase 3: User Story 1 - Apply catalog template to a new task (Priority: P1) 🎯 MVP

**Goal**: Let task authors browse/preview/apply templates via API + UI while keeping worker payload contract unchanged.

**Independent Test**: Using dashboard UI, insert a preset into the steps editor, confirm deterministic IDs + metadata, submit task, and verify queue payload contains expanded steps and `appliedStepTemplates` entries.

### Implementation

- [ ] T012 [US1] Implement FastAPI router `api_service/api/routers/task_step_templates.py` with list/get/version/expand endpoints wired to catalog services and RBAC dependencies.
- [ ] T013 [US1] Add serializer adapters in `api_service/api/routers/task_step_templates.py` (or helper module) that convert ORM objects to schema responses, including derived favorites/recents metadata.
- [ ] T014 [US1] Update `api_service/api/routers/task_dashboard.py`/`task_dashboard_view_model.py` to expose new preset configuration (feature flags, favorites, recents) to the UI config payload.
- [ ] T015 [US1] Enhance `api_service/static/task_dashboard/dashboard.js` with preset drawer UI: browser w/ filters, preview modal, append vs replace, collapse groups, preview diff.
- [ ] T016 [US1] Add deterministic ID + metadata handling inside dashboard steps editor so inserted presets keep `stepId` values until user edits them.
- [ ] T017 [US1] Create backend unit/integration tests in `tests/api_service/test_task_step_templates.py` covering list/filter, expand substitution, deterministic ID generation, validation failures, and capability union behavior.
- [ ] T018 [US1] Add browser-focused tests (jest/jsdom or DOM harness) in `tests/task_dashboard/test_presets_ui.js` verifying append/replace/preview interactions and collapse state persistence.
- [ ] T019 [US1] Document catalog usage for UI/CLI in `docs/TaskPresetsSystem.md` (final polish once implementation stabilizes).

---

## Phase 4: User Story 2 - Save curated steps as a reusable template (Priority: P1)

**Goal**: Allow users to select existing steps, scrub secrets, parameterize strings, and save templates scoped to personal/team catalogs.

**Independent Test**: Select steps, run "Save as template", fill metadata, confirm server rejects secrets, template appears under Personal scope and can be applied immediately.

### Implementation

- [ ] T020 [US2] Extend `api_service/services/task_templates/save.py` with APIs to accept selected steps, scrub secrets, suggest inputs, and persist template + version records atomically.
- [ ] T021 [US2] Implement `POST /api/task-step-templates/save-from-task` endpoint, reusing schemas + RBAC, and emit audit logs with sanitized payload snapshots.
- [ ] T022 [US2] Add UI affordances in `dashboard.js` for multi-step selection, placeholder detection UI, secret warning surfaces, and save modal (scope, title, inputs, share toggle).
- [ ] T023 [US2] Persist recents/favorites metadata when saving templates (update `task_step_template_recents`/`favorites` tables and recalc caches).
- [ ] T024 [US2] Add backend tests in `tests/api_service/test_task_step_template_save.py` covering secret scrubbing enforcement, placeholder conversion, RBAC scope enforcement, and audit metadata.
- [ ] T025 [US2] Add UI tests verifying secret-highlighting and variable substitution flows using DOM harness.

---

## Phase 5: User Story 3 - Track template usage and enforce RBAC (Priority: P2)

**Goal**: Provide governance for scopes, review process, favorites, recents, and audit events.

**Independent Test**: Attempt to access personal/team templates from unauthorized accounts, ensure denial; review audit logs showing who applied or saved versions.

### Implementation

- [ ] T026 [US3] Implement RBAC policy enforcement in router dependencies to restrict listing/access/edit to rightful scopes, including admin-only global template creation.
- [ ] T027 [US3] Build reviewer workflow fields (`reviewed_by`, `reviewed_at`, `release_status`) in services + migration plus endpoints for approving new versions.
- [ ] T028 [US3] Add favorites/recents endpoints or query params to `task_step_templates.py` and persist usage in `task_step_template_favorites`/`recents` tables.
- [ ] T029 [US3] Surface governance state (review required, inactive) in UI badges/warnings to block accidental use; add CLI warnings via API responses.
- [ ] T030 [US3] Extend tests to include RBAC, favorites sorting, recents trimming, and reviewer flow coverage in `tests/api_service/test_task_step_templates.py`.

---

## Phase 6: User Story 4 - CLI/MCP users expand templates via API (Priority: P3)

**Goal**: Offer CLI + automation parity for listing + expanding templates with identical validation semantics.

**Independent Test**: Use CLI to call list + expand endpoints, merge steps into JSON payload, and submit tasks without UI involvement.

### Implementation

- [ ] T031 [US4] Publish CLI helper inside `moonmind/agents/cli/task_templates.py` (or existing CLI module) that wraps list/expand endpoints and merges steps client-side.
- [ ] T032 [US4] Add documentation + examples to `docs/TaskQueueSystem.md` describing CLI usage and API tokens for template catalog.
- [ ] T033 [US4] Create automated tests (could reuse CLI smoke tests) verifying CLI fallback to raw API and error propagation for validation failures.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T034 Add monitoring metrics + alerts (StatsD counters, Prometheus) for template creation/expansion/save failures in `api_service/services/task_templates/catalog.py` and Grafana dashboards.
- [ ] T035 [P] Backfill changelog + release notes in `docs/release-notes.md` summarizing template catalog launch + rollout phases.
- [ ] T036 [P] Write migration/backfill script in `scripts/backfill_template_recents.py` to populate recents for last 30 days of task history (optional but recommended for analytics).
- [ ] T037 [P] Update `docs/TaskPresetsSystem.md` with screenshots + final UX copy once UI changes merge.
- [ ] T038 Run `./tools/test_unit.sh` and targeted browser tests before handoff, capturing results in PR description.

---

## Dependencies & Execution Order

- Setup → Foundational → Stories/Polish. User Story 1 (apply) blocks later stories because it delivers API surface consumed by save/RBAC/CLI flows. User Story 2 depends on Foundational + Story 1 (needs API + UI primitives). User Story 3 depends on Story 1 + Foundational but can proceed parallel to Story 2 once RBAC utilities exist. User Story 4 depends on Story 1 endpoints.

## Parallel Execution Notes

- Marked [P] tasks can run concurrently (different files, no shared migrations).
- User Stories 2 and 3 can overlap after Story 1 endpoints/validators stabilize.
- Validation Task T038 must run after all code changes but before release.

## MVP Scope

- Completing Phases 1–3 delivers MVP: backend catalog storage + UI apply flow + tests + docs. Subsequent phases add save, governance, CLI parity, and polish.
