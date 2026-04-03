# Task Dependencies Implementation Plan

Status: In Progress  
Owners: MoonMind Engineering  
Last Updated: 2026-04-01  
Related: `docs/Tasks/TaskDependencies.md`, `docs/Api/ExecutionsApiContract.md`, `docs/UI/MissionControlArchitecture.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`

## Phase 0 - Spec Alignment ✅ COMPLETE

- [x] Rewrite `docs/Tasks/TaskDependencies.md` to align with the current Temporal architecture.
- [x] Update terminology to use `/api/executions`, `workflowId`, `taskId == workflowId`, and `initialParameters.task.dependsOn`.
- [x] Add a current implementation snapshot covering what already exists and what is still missing.
- [x] Clarify v1 scope as create-time only, `MoonMind.Run` only, no edit support, and no cross-type dependencies.

All Phase 0 requirements verified complete as of 2026-03-29 (spec `116-task-dep-phase0`).

## Phase 1 - Submit Contract And Validation ✅ COMPLETE

- [x] Extend task-shaped submit normalization in `api_service/api/routers/executions.py` to read `payload.task.dependsOn`.
- [x] Validate `dependsOn` shape as an array of strings, trim empties, deduplicate, enforce the 10-item limit, and reject invalid types.
- [x] Persist normalized dependency IDs into `initial_parameters["task"]["dependsOn"]`.
- [x] Add create-time validation so each ID resolves to an existing `MoonMind.Run` execution and does not create self-dependency.
- [x] Implement cycle detection across transitive `dependsOn` chains with bounded traversal (depth 10, node limit 50).
- [x] Return clear validation errors for missing targets, unsupported workflow types, cycles, and limit violations.
- [x] Add missing `test_create_execution_rejects_self_dependency` unit test (FR-008 coverage).

All Phase 1 requirements verified complete as of 2026-03-29 (spec `117-task-dep-phase1`).

## Phase 2 - `MoonMind.Run` Dependency Gate ✅ COMPLETE

- [x] Parse `initialParameters.task.dependsOn` in `MoonMindRunWorkflow.run()` after `_initialize_from_payload()` and before entering `STATE_PLANNING`.
  - Insertion point: `run.py` between the current `STATE_INITIALIZING` and `STATE_PLANNING` transitions.
- [x] Set `mm_state` to `waiting_on_dependencies` and update memo/search attributes with dependency IDs when `dependsOn` is present and non-empty.
- [x] Wait on each prerequisite using Temporal external workflow handles.
  ```python
  handles = [
      workflow.get_external_workflow_handle(dep_id)
      for dep_id in depends_on
  ]
  await asyncio.gather(*(handle.result() for handle in handles))
  ```
- [x] Wrap the dependency wait in an interruptible cancellation boundary so cancellation of the dependent run interrupts the wait cleanly.
  - Implementation note: the current `temporalio.workflow` SDK in this repo does not expose a `CancellationScope` API, so the workflow uses explicit task cancellation around the dependency wait to preserve the intended semantics.
- [x] Guard the dependency gate with `workflow.patched("dependency-gate-v1")` for replay safety.
- [x] Preserve current behavior by proceeding directly to `planning` when `dependsOn` is absent or empty.
- [x] Fail the dependent run with a dependency-specific message when a prerequisite fails, is canceled, or is terminated.
- [x] Preserve cancel semantics during the dependency wait so canceling the dependent run cancels only that run, not the prerequisites.
- [x] Check `self._paused` after the dependency wait resolves, consistent with the existing pause gate pattern.

All Phase 2 requirements verified complete as of 2026-04-01 (spec `123-task-dep-phase2`).

## Phase 3 - Finish Summary And Read Model Metadata ✅ COMPLETE

- [x] Extend `reports/run_summary.json` with dependency outcome data:
  - [x] declared dependency IDs
  - [x] whether a dependency wait occurred
  - [x] dependency wait duration
  - [x] resolution outcome (success vs dependency failure)
  - [x] failed dependency ID when applicable
- [x] Include dependency presence metadata in workflow memo (for list/detail surfaces).
- [x] Ensure execution serialization can surface dependency metadata cleanly for the detail page.

All Phase 3 requirements verified complete as of 2026-03-29.

## Phase 4 - Mission Control Create And Detail UX

- [ ] Add a Dependencies section to `/tasks/new`.
- [ ] Use execution search or list APIs to power a dependency picker for existing `MoonMind.Run` executions.
- [ ] Enforce client-side validation for dependency count and duplicate entries.
- [ ] Verify `waiting_on_dependencies` presentation in the task list.
- [ ] Add a Dependencies panel to task detail with prerequisite links and current statuses.
- [ ] Show titles of blocking tasks in the task list quick view.
- [ ] Add a lightweight downstream dependents view if backend reverse lookup is feasible without blocking v1.

## Phase 5 - Hardening And Rollout

- [ ] Add integration coverage for chained execution.
- [ ] Add integration coverage for multi-dependency fan-in.
- [ ] Add integration coverage for failure propagation.
- [ ] Add integration coverage for restart or replay safety.
- [ ] Verify Continue-As-New behavior preserves dependency context.
- [ ] Confirm list and detail pages remain performant with dependency metadata present.
- [ ] Document operator guidance for dependency limits, failure semantics, and known v1 non-goals.
