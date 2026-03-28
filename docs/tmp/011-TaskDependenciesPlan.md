# Task Dependencies Implementation Plan

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-03-27  
Related: `docs/Tasks/TaskDependencies.md`, `docs/Api/ExecutionsApiContract.md`, `docs/UI/MissionControlArchitecture.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`

## Phase 0 - Spec Alignment

- Rewrite `docs/Tasks/TaskDependencies.md` to align with the current Temporal architecture.
- Update terminology to use `/api/executions`, `workflowId`, `taskId == workflowId`, and `initialParameters.task.dependsOn`.
- Add a current implementation snapshot covering what already exists and what is still missing.
- Clarify v1 scope as create-time only, `MoonMind.Run` only, no edit support, and no cross-type dependencies.

## Phase 1 - Submit Contract And Validation

- Extend task-shaped submit normalization in `api_service/api/routers/executions.py` to read `payload.task.dependsOn`.
- Validate `dependsOn` shape as an array of strings, trim empties, deduplicate, enforce the 10-item limit, and reject invalid types.
- Persist normalized dependency IDs into `initial_parameters["task"]["dependsOn"]`.
- Add create-time validation so each ID resolves to an existing `MoonMind.Run` execution and does not create self-dependency.
- Implement cycle detection across transitive `dependsOn` chains with bounded traversal (limit 20 hops).
- Return clear validation errors for missing targets, unsupported workflow types, cycles, and limit violations.

## Phase 2 - `MoonMind.Run` Dependency Gate

- Add dependency parsing helpers in `moonmind/workflows/temporal/workflows/run.py`.
- Inspect `initialParameters.task.dependsOn` before `planning`.
- When dependencies exist, set `STATE_WAITING_ON_DEPENDENCIES`, update metadata with normalized dependency IDs, and record wait timing.
- Wait on dependencies using Temporal external workflow handles.
- Preserve cancel and pause behavior while waiting without mutating prerequisite runs.
- Fail the dependent run with a dependency-specific reason when a prerequisite fails, is canceled, or is terminated.
- Transition cleanly into `planning` once all prerequisites succeed.

## Phase 3 - Finish Summary And Read Model Metadata

- Extend `reports/run_summary.json` with dependency outcome data.
- Include dependency metadata in workflow memo or equivalent read-model metadata needed for detail views.
- Ensure execution serialization can surface dependency metadata cleanly for the detail page.

## Phase 4 - Mission Control Create And Detail UX

- Add a Dependencies section to `/tasks/new`.
- Use execution search or list APIs to power a dependency picker for existing `MoonMind.Run` executions.
- Enforce client-side validation for dependency count and duplicate entries.
- Verify `waiting_on_dependencies` presentation in the task list.
- Add a Dependencies panel to task detail with prerequisite links and current statuses.
- Quick-view in task list shows titles of blocking tasks.
- Add a lightweight downstream dependents view only if backend reverse lookup is feasible without blocking v1.

## Phase 5 - Hardening And Rollout

- Add integration coverage for chained execution, multi-dependency fan-in, failure propagation, and restart or replay safety.
- Verify Continue-As-New behavior preserves dependency context.
- Confirm list and detail pages remain performant with dependency metadata present.
- Document operator guidance for dependency limits, failure semantics, and known v1 non-goals.
