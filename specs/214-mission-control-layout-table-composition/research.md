# Research: Mission Control Layout and Table Composition Patterns

## Decision: Make the task list the primary implementation target

Rationale: `docs/UI/MissionControlDesignSystem.md` names `/tasks/list` as the route that should use a control deck plus data slab structure and remain table-first on desktop. `frontend/src/entrypoints/tasks-list.tsx` already owns the relevant filters, utility controls, pagination, table, and mobile cards, so the story can be implemented without backend changes.

Alternatives considered: Redesigning manifests, schedules, settings, or detail pages was rejected as broader than the supplied single-story brief. Those pages can consume the shared `DataTable` improvements without a route redesign.

Test implications: Unit coverage comes from focused Vitest task-list tests. Integration-style coverage stays at the browser component boundary because the route behavior is exercised through rendered filters, request shape, routing links, and pagination without changing backend contracts.

## Decision: Use semantic CSS classes as the composition contract

Rationale: Mission Control already relies on stable semantic classes such as `panel`, `toolbar`, and `queue-table-wrapper`. Adding `panel--controls`, `panel--data`, `task-list-control-deck`, and `task-list-data-slab` keeps the pattern inspectable and testable without introducing a component framework migration.

Alternatives considered: Introducing a new React layout component was rejected because the current page is small and the first useful contract is the rendered surface structure.

Test implications: Unit tests assert the semantic class contract directly, and computed-style checks verify sticky table posture.

## Decision: Keep table behavior and request semantics unchanged

Rationale: The story is about composition and table posture, not data fetching. Existing tests already cover request parameters, sorting, pagination, dependency summaries, runtime labels, mobile cards, and long ID wrapping. New tests should add coverage around surface structure and active filter chips while preserving existing assertions.

Test implications: Unit tests cover the new UI structure while existing render tests continue to serve as integration-style guardrails for query/request behavior.

## Decision: Standardize `DataTable` markup on Mission Control classes

Rationale: `frontend/src/components/tables/DataTable.tsx` used standalone Tailwind table wrappers that did not express the shared matte data slab pattern. Switching its wrapper/table/empty-state classes to Mission Control classes lets existing consumers inherit the same table foundation.

Test implications: No backend or compose-backed integration test is required; build/type checks and existing component render coverage validate the shared class contract.

## Source Design Coverage

Decision: Treat DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, and DESIGN-REQ-019 from the trusted MM-426 preset brief as in-scope runtime requirements.

Evidence: `specs/214-mission-control-layout-table-composition/spec.md` preserves the source design coverage IDs; `frontend/src/entrypoints/tasks-list.tsx` renders the control deck, utility cluster, active chips, clear action, and data slab; `frontend/src/styles/mission-control.css` defines sticky table headers and dense table slab styles; `frontend/src/components/tables/DataTable.tsx` emits shared data-table classes.

Rationale: The Jira brief points at `docs/UI/MissionControlDesignSystem.md` as source requirements. The scoped story implements the `/tasks/list` layout/table composition portions while keeping masthead ownership outside the task-list route.

Alternatives considered: Regenerating broad design breakdown output was rejected because MM-426 is already a single-story preset brief. Adding a data model was rejected because the story adds no persisted data, schema, or state machine.

Test implications: Unit tests cover the rendered UI class contract and sticky header posture. Integration strategy remains the existing task-list browser component boundary because backend API contracts are unchanged.
