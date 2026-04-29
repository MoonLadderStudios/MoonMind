# Verification: Agentic Skill Step Authoring

## Verdict

FULLY_IMPLEMENTED

## Scope Verified

MM-564 is covered as a single-story runtime feature for authoring and validating agentic Skill steps.

## Requirement Coverage

| Requirement | Evidence | Status |
| --- | --- | --- |
| FR-001 | `frontend/src/entrypoints/task-create.tsx` exposes Skill selector, Skill Args JSON, and Skill Required Capabilities; `task-create.test.tsx` includes `submits an authored MM-564 Skill step with agentic controls`. | VERIFIED |
| FR-002 | The MM-564 regression verifies submitted `task.tool.type: "skill"` and explicit `task.skill` payload for `moonspec-orchestrate`. | VERIFIED |
| FR-003 | Existing Create-page tests reject invalid Skill Args JSON before submission; backend contract validates capability list shape. | VERIFIED |
| FR-004 | MM-564 regression verifies Skill id, args with `issueKey: "MM-564"`, and required capabilities are preserved; template service tests preserve context, permissions, and autonomy metadata. | VERIFIED |
| FR-005 | Task contract and Create-page payload keep agentic Skill work as Skill even when capabilities/internal tool affordances are present. | VERIFIED |
| FR-006 | `tests/unit/workflows/tasks/test_task_contract.py` rejects non-skill Tool payloads on Skill steps; template service tests reject mixed Skill/Tool payloads. | VERIFIED |
| FR-007 | Create-page Skill labels/help text distinguish Skill as agent work from deterministic Tool steps. | VERIFIED |
| DESIGN-REQ-005 | Skill selector, instructions, args/context, capabilities, and metadata preservation are covered by frontend and service tests. | VERIFIED |
| DESIGN-REQ-015 | Skill picker/validation and Jira-agentic Skill distinction are represented by the MM-564 regression and existing Tool/Skill contract tests. | VERIFIED |

## Test Evidence

- `pytest tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py -q`: PASS, 63 passed.
- `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`: PASS.
  - Python unit phase: 4221 passed, 1 xpassed, 16 subtests passed.
  - UI target: 1 file passed, 217 tests passed.

## Notes

- No production code change was required. Existing Step Type implementation already satisfied the runtime behavior; this story adds MM-564-specific regression evidence and complete MoonSpec traceability.
- The direct `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` command was initially blocked because `vitest` was not installed. The required setup-aware runner installed frontend dependencies and passed the target file.
