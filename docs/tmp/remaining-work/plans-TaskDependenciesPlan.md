# Remaining work: `docs/plans/TaskDependenciesPlan.md`

**Source:** [`docs/plans/TaskDependenciesPlan.md`](../../plans/TaskDependenciesPlan.md)  
**Last synced:** 2026-03-24

## Open items

Phase 1 is complete. Remaining phases:

### Phase 2 — Workflow dependency logic (NOT STARTED)

- Add `_run_dependency_wait_stage` and wire between initializing and planning in `MoonMind.Run`.
- External workflow handles for `dependsOn`, cancel/pause/CAN safety, finish summary + memo updates.
- Tests: happy path, dependency failure, cancel during wait, pause during wait, empty `dependsOn`, workflow boundary shape.

### Phase 3 — API validation & payload (NOT STARTED)

- `dependsOn` on task create schema → `initialParameters`.
- Existence, workflow-type, limit (10), self-dependency, cycle detection (20 hops) validation.

### Phase 4 — Frontend (NOT STARTED)

- `WAITING_ON_DEPENDENCIES` badge, tooltips, creation form dependencies, detail panels (prerequisites + dependents).

See source plan tables for file-level deliverable IDs.
