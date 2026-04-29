# Verification: Governed Tool Step Authoring

**Original Request Source**: `spec.md` input preserving Jira issue `MM-563` and the trusted Jira preset brief.

**Verdict**: FULLY_IMPLEMENTED

## Requirement Coverage

| ID | Status | Evidence |
| --- | --- | --- |
| FR-001 | VERIFIED | `frontend/src/entrypoints/task-create.tsx` exposes editable Tool id, optional version, and JSON object inputs for Tool steps; `frontend/src/entrypoints/task-create.test.tsx` submits `jira.get_issue` with version and inputs. |
| FR-002 | VERIFIED | Manual Tool submissions emit `type: "tool"` with `tool` payload and no `skill` payload, covered by the Create-page test. |
| FR-003 | VERIFIED | Missing Tool id remains blocked, and invalid Tool input JSON is blocked before `/api/executions`; covered by Create-page tests. |
| FR-004 | VERIFIED | Submitted payload preserves `tool.id`, `tool.version`, and parsed object `tool.inputs`. |
| FR-005 | VERIFIED | `moonmind/workflows/tasks/task_contract.py` rejects `command`, `cmd`, `script`, `shell`, and `bash` on executable steps; covered by `tests/unit/workflows/tasks/test_task_contract.py`. |
| FR-006 | VERIFIED | Step Type choices remain Tool, Skill, and Preset; Tool panel copy keeps Tool terminology and tests assert no Script label. |
| DESIGN-REQ-003 | VERIFIED | Authored Tool steps now represent deterministic typed operations with explicit Tool payloads. |
| DESIGN-REQ-004 | VERIFIED | Tool id/version/input authoring and validation are covered at the Create-page boundary. |
| DESIGN-REQ-015 | VERIFIED | Tool terminology is preserved and shell/script fields are rejected. |
| SC-001 | VERIFIED | Frontend test confirms a valid authored Tool step submits the required payload. |
| SC-002 | VERIFIED | Frontend test confirms invalid Tool input JSON blocks execution submission. |
| SC-003 | VERIFIED | Python unit test confirms backend task contract rejects shell/script/command fields. |
| SC-004 | VERIFIED | Existing and new frontend assertions keep Tool terminology and omit Script as a Step Type option. |

## Test Evidence

- `pytest tests/unit/workflows/tasks/test_task_contract.py -q`: PASS, 25 passed.
- `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`: PASS, 4221 Python unit tests passed, 1 xpassed, 16 subtests passed; frontend dependencies prepared; `frontend/src/entrypoints/task-create.test.tsx` PASS, 216 tests passed.
- `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`: PASS.

## Notes

- The first MM-563 slice uses Tool id/version text entry plus JSON object inputs. The spec records schema-driven generated forms as a later layer once a Create-page tool catalog source is available.
