# Implementation Plan: Task Details Edit and Rerun Actions

**Input**: Single-story feature specification from `specs/258-task-details-edit-rerun-actions/spec.md`

## Summary

Add an explicit `canEditForRerun` action capability for terminal task executions and update the React Task Details action bar to render **Edit task** and **Rerun** from the backend capability object.

## Technical Context

- Python 3.12 + Pydantic v2 for the executions API contract.
- TypeScript/React for Mission Control Task Details and Create page routing.
- Existing tests: `tests/unit/api/routers/test_executions.py`, `frontend/src/entrypoints/task-detail.test.tsx`, and `frontend/src/entrypoints/task-create.test.tsx`.
- No new persistent storage.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS - keeps behavior in dashboard/API orchestration.
- II One-Click Agent Deployment: PASS - no deployment prerequisite changes.
- III Avoid Vendor Lock-In: PASS - no provider-specific coupling.
- IV Own Your Data: PASS - uses existing task input snapshot refs.
- V Skills Are First-Class: PASS - no skill runtime changes.
- VI Replaceable Scaffolding: PASS - bounded UI/API contract change with tests.
- VII Runtime Configurability: PASS - respects existing task editing feature flag.
- VIII Modular Architecture: PASS - capability calculation remains at API boundary.
- IX Resilient by Default: PASS - terminal actions do not mutate failed executions in place.
- X Continuous Improvement: PASS - verification evidence is recorded.
- XI Spec-Driven Development: PASS - spec, plan, and tasks are present.
- XII Canonical Documentation: PASS - canonical docs remain desired-state only.
- XIII Pre-Release Compatibility: PASS - internal contract updated directly without aliases.

## Design

- Extend `ExecutionActionCapabilityModel` with `can_edit_for_rerun`.
- Add `can_edit_for_rerun` to terminal rerunnable states in `_build_action_capabilities`.
- Apply the same workflow type, feature flag, and original snapshot gates to `can_edit_for_rerun` as `can_rerun`.
- Update the Task Details action schema and rendering logic to use `canEditForRerun`.
- Add an edit-for-rerun href helper and route resolution support for `?rerunExecutionId=:id&mode=edit`.

## Test Strategy

- Unit: API capability calculation for failed and executing records.
- Frontend unit: Task Details action rendering and route helper behavior.
- Full unit runner: `./tools/test_unit.sh` for final verification when feasible.
