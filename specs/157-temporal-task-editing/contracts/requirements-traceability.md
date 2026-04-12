# Requirements Traceability: Temporal Task Editing Entry Points

## Coverage Matrix

| Source Requirement | Functional Requirement(s) | Planned Implementation Surface | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001 | `moonmind/schemas/temporal_models.py`, `api_service/api/routers/executions.py` expose/edit against `workflowId` execution detail. | API/router unit test verifies execution detail uses `workflowId` as the task editing identity. |
| DOC-REQ-002 | FR-002 | Capability builder in `api_service/api/routers/executions.py` limits Edit/Rerun support to `MoonMind.Run`. | Backend unit test covers unsupported workflow type with false edit/rerun capabilities and explicit reason. |
| DOC-REQ-003 | FR-008 | `frontend/src/lib/temporalTaskEditing.ts` defines create, edit, and rerun route helpers. | Frontend unit test verifies canonical route helper outputs and URL encoding. |
| DOC-REQ-004 | FR-011, FR-017 | Task-detail UI links use canonical helpers and no queue-era route or param; no fallback branch is introduced. | Frontend tests assert route outputs do not use `editJobId` or `/tasks/queue/new`; code search review checks queue-era strings are not introduced in primary flow. |
| DOC-REQ-005 | FR-005, FR-009, FR-010 | Backend action capability set exposes `canUpdateInputs` and `canRerun`; task-detail UI renders links only when the corresponding capability is true. | Backend contract tests verify capability fields; frontend visibility tests cover Edit/Rerun visible and omitted states. |
| DOC-REQ-006 | FR-005, FR-012 | Capability computation distinguishes active edit from terminal rerun; UI copy and visibility avoid terminal in-place edit implications. | Fixture tests cover active update capability and terminal rerun capability independently. |
| DOC-REQ-007 | FR-004, FR-014 | Execution detail serialization exposes input parameters, artifact refs, runtime/profile/model/repository/branch/publish/skill state. | API/router test verifies representative supported detail payload contains reconstruction fields. |
| DOC-REQ-008 | FR-006, FR-007 | Update request contract retains `UpdateInputs`, `RequestRerun`, `inputArtifactRef`, and `parametersPatch` for later submit phases. | Contract/schema tests or API unit tests verify update names are recognized and invalid update names fail. |
| DOC-REQ-009 | FR-011, FR-016 | Capability disabled reasons and unsupported-state copy distinguish unsupported workflow type, flag disabled, state ineligible, and missing data cases. | Backend tests cover disabled reasons; frontend tests cover omitted actions for unsupported/disabled states. |
| DOC-REQ-010 | FR-007 | Update contract and later submit payloads require new input artifact references for edited content rather than historical mutation. | Contract review verifies update payload uses new artifact refs; later submit tests must assert historical refs are not overwritten. |
| DOC-REQ-011 | FR-003, FR-013, FR-014, FR-015, FR-016, FR-018 | Runtime config flag, frontend mode/contract helpers, fixture coverage, unsupported-state copy, and runtime code/test deliverables. | Dashboard config tests verify flag exposure; frontend/backend tests verify fixtures and contract scaffolding; `./tools/test_unit.sh` runs before completion. |
| DOC-REQ-012 | FR-009, FR-010, FR-011, FR-015, FR-018 | Task-detail Edit/Rerun links are feature-flagged, capability-gated, and covered by runtime tests. | Frontend task-detail tests cover visibility and navigation; backend tests cover capability flags; full unit runner validates regression coverage. |

## Completeness Gate

- Every `DOC-REQ-*` from `spec.md` maps to at least one functional requirement.
- Every mapped requirement has at least one implementation surface.
- Every mapped requirement has at least one validation strategy.
- No `DOC-REQ-*` is intentionally out of scope for Phase 0/1.

## Runtime Scope Gate

This feature is runtime-scoped. Completion requires production runtime code changes and validation tests. Documentation or spec-only changes are insufficient.
