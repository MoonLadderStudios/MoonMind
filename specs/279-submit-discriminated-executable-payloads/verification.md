# Verification: Submit Discriminated Executable Payloads

**Feature**: `specs/279-submit-discriminated-executable-payloads/spec.md`  
**Original Request Source**: MM-559 Jira preset brief preserved in `spec.md` Input  
**Verdict**: FULLY_IMPLEMENTED

## Requirement Coverage

| ID | Status | Evidence |
| --- | --- | --- |
| FR-001 | VERIFIED | `TaskStepSpec` validates explicit `type: "tool"` and `type: "skill"` submitted steps; `test_task_steps_accept_explicit_tool_and_skill_discriminators` covers both. |
| FR-002 | VERIFIED | `_build_runtime_planner` maps explicit Tool steps to typed tool plan nodes and explicit Skill steps to agent-runtime nodes; covered by `test_runtime_planner_maps_explicit_tool_step_to_typed_tool_node` and `test_runtime_planner_maps_explicit_skill_step_to_agent_runtime_node`. |
| FR-003 | VERIFIED | Create-page submitted preset-derived Tool and Skill steps include `type` values; covered by updated `task-create.test.tsx` expectations. |
| FR-004 | VERIFIED | `type: "preset"` is rejected at the task contract boundary; covered by `test_task_steps_reject_non_executable_step_types`. |
| FR-005 | VERIFIED | `activity` and `Activity` Step Type labels are rejected; covered by `test_task_steps_reject_non_executable_step_types`. |
| FR-006 | VERIFIED | Provenance metadata remains preserved in task contract dumps and runtime node inputs while mapping is chosen from explicit Step Type; covered by contract and runtime tests. |
| FR-007 | VERIFIED | Conflicting Tool and Skill executable payloads are rejected; covered by `test_task_steps_reject_conflicting_executable_payloads` and `test_task_steps_reject_skill_step_with_non_skill_tool_payload`. |
| DESIGN-REQ-008 | VERIFIED | Executable submission boundary accepts Tool/Skill and rejects Preset. |
| DESIGN-REQ-011 | VERIFIED | Runtime materialization distinguishes explicit Tool and Skill submitted steps. |
| DESIGN-REQ-012 | VERIFIED | Activity remains rejected as a Step Type label. |
| DESIGN-REQ-016 | VERIFIED | Submitted payloads preserve explicit discriminators and distinct sub-payload validation. |
| DESIGN-REQ-019 | VERIFIED | Preset provenance is audit metadata and unresolved Preset steps do not materialize. |

## Test Evidence

- `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py`: PASS, 69 tests.
- `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`: PASS, 213 tests.
- `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`: PASS.
- `./tools/test_unit.sh`: PASS, 4215 Python tests, 16 subtests, and 471 frontend tests.

## Notes

- The implementation preserves existing legacy step behavior when no explicit `type` is supplied, while strict validation applies once a submitted Step Type is present.
- Oversized inline instruction stripping removes type-only step remnants so artifact-backed payload slimming does not create fake executable steps.
- No raw credentials or external Jira data were committed.
