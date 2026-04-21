# Research: Mission Control Layout and Table Composition Patterns

## Decision: Make the task list the primary implementation target

Rationale: `docs/UI/MissionControlDesignSystem.md` names `/tasks/list` as the route that should use a control deck plus data slab structure and remain table-first on desktop. `frontend/src/entrypoints/tasks-list.tsx` already owns the relevant filters, utility controls, pagination, table, and mobile cards, so the story can be implemented without backend changes.

Alternatives considered: Redesigning manifests, schedules, settings, or detail pages was rejected as broader than the supplied single-story brief. Those pages can consume the shared `DataTable` improvements without a route redesign.

## Decision: Use semantic CSS classes as the composition contract

Rationale: Mission Control already relies on stable semantic classes such as `panel`, `toolbar`, and `queue-table-wrapper`. Adding `panel--controls`, `panel--data`, `task-list-control-deck`, and `task-list-data-slab` keeps the pattern inspectable and testable without introducing a component framework migration.

Alternatives considered: Introducing a new React layout component was rejected because the current page is small and the first useful contract is the rendered surface structure.

## Decision: Keep table behavior and request semantics unchanged

Rationale: The story is about composition and table posture, not data fetching. Existing tests already cover request parameters, sorting, pagination, dependency summaries, runtime labels, mobile cards, and long ID wrapping. New tests should add coverage around surface structure and active filter chips while preserving existing assertions.

## Decision: Standardize `DataTable` markup on Mission Control classes

Rationale: `frontend/src/components/tables/DataTable.tsx` used standalone Tailwind table wrappers that did not express the shared matte data slab pattern. Switching its wrapper/table/empty-state classes to Mission Control classes lets existing consumers inherit the same table foundation.
