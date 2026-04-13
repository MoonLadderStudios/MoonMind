# Requirements Traceability: Jira Create Browser

## Source Requirement Mapping

| DOC-REQ ID | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-002 | `frontend/src/entrypoints/task-create.tsx`; `api_service/api/routers/task_dashboard_view_model.py` | Runtime config tests prove Jira config is omitted when disabled; Create page tests prove controls are hidden when disabled. |
| DOC-REQ-002 | FR-008 | `frontend/src/entrypoints/task-create.tsx`; `api_service/api/routers/task_dashboard_view_model.py` | Runtime config tests validate MoonMind-owned endpoint templates; Create page fetch mocks assert browser calls configured `/api/jira/...` paths. |
| DOC-REQ-003 | FR-009 | `frontend/src/entrypoints/task-create.tsx` | Create page tests verify board columns render in returned order. |
| DOC-REQ-004 | FR-009, FR-010 | `frontend/src/entrypoints/task-create.tsx` | Create page tests verify column switching changes the visible issue list and hides unrelated column issues. |
| DOC-REQ-005 | FR-011 | `frontend/src/entrypoints/task-create.tsx` | Create page tests verify selecting an issue loads normalized preview fields. |
| DOC-REQ-006 | FR-003, FR-004, FR-005, FR-012 | `frontend/src/entrypoints/task-create.tsx`; `frontend/src/styles/mission-control.css` | Create page tests verify the browser opens from preset and step targets and displays the selected target. |
| DOC-REQ-007 | FR-012 | `frontend/src/entrypoints/task-create.tsx`; `frontend/src/entrypoints/task-create.test.tsx` | Tests assert issue selection previews data without text import; later import tests remain out of scope for this phase. |
| DOC-REQ-008 | FR-003, FR-004, FR-005 | `frontend/src/entrypoints/task-create.tsx` | Create page tests verify preset and step target preselection in the shared browser. |

## Functional Requirement Validation

| Requirement | Primary Validation |
| --- | --- |
| FR-001 | Jira controls hidden test plus runtime-config disabled tests. |
| FR-002 | Existing Create page suite remains passing with Jira disabled. |
| FR-003 | Preset target browser-open test. |
| FR-004 | Step target browser-open test. |
| FR-005 | Target label assertions for preset and step contexts. |
| FR-006 | Typecheck and browser interaction tests covering selected project, board, column, issue, target, and loading state transitions. |
| FR-007 | Typecheck over explicit client-side Jira models. |
| FR-008 | Fetch mocks and runtime-config endpoint tests. |
| FR-009 | Board column order test. |
| FR-010 | Column switch issue visibility test. |
| FR-011 | Issue preview loading test. |
| FR-012 | Existing submission/objective tests remain passing; Phase 4 browser tests assert issue preview does not mutate draft fields. |
| FR-013 | Typecheck over replace-or-append preference state; import execution intentionally absent. |
| FR-014 | Browser-local error tests verify Jira failures stay inside the browser and manual task creation remains available. |
| FR-015 | Final validation requires focused frontend tests plus `./tools/test_unit.sh`. |

## Traceability Gate

All `DOC-REQ-*` identifiers present in `spec.md` are mapped to at least one functional requirement, an implementation surface, and a validation strategy. No unmapped source requirement remains.
