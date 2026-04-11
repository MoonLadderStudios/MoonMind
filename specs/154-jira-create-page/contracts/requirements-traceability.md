# Requirements Traceability: Jira Create Page Integration

| Source Requirement | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-013, FR-014, FR-020, FR-022 | `frontend/src/entrypoints/task-create.tsx`; `moonmind/integrations/jira/browser.py` import text fields | Create-page tests open browser from preset and step targets; import tests verify preset and selected step writes. |
| DOC-REQ-002 | FR-005, FR-027, FR-030, FR-032 | `api_service/api/routers/jira_browser.py`; `moonmind/integrations/jira/browser.py`; `task-create.tsx` provenance state | Router tests assert MoonMind-owned responses only; frontend tests verify manual editing/submission when Jira disabled/unavailable; submission tests assert payload shape unchanged. |
| DOC-REQ-003 | FR-013, FR-014, FR-015 | `task-create.tsx` shared browser state and UI | Frontend tests assert one browser surface, target preselection, and no duplicate embedded per-field browser. |
| DOC-REQ-004 | FR-023, FR-026 | `task-create.tsx` import action uses existing `updateStep()` path and warning state | Frontend tests import into template-bound step and assert template ID detachment plus warning copy. |
| DOC-REQ-005 | FR-021, FR-024, FR-025 | `task-create.tsx` preset import handling and reapply message | Frontend tests apply preset, import into preset objective, assert steps unchanged and reapply-needed messaging. |
| DOC-REQ-006 | FR-001, FR-002, FR-003, FR-004, FR-005 | `moonmind/config/settings.py`; `api_service/api/routers/task_dashboard_view_model.py`; `api_service/config.template.toml` | Settings and runtime-config tests assert disabled omission, enabled source/system block, defaults, and separation from Jira tool enablement. |
| DOC-REQ-007 | FR-007, FR-009, FR-010, FR-011 | `moonmind/integrations/jira/browser.py`; `api_service/api/routers/jira_browser.py` | Service tests cover board config normalization, column ordering, empty columns, status mapping, unmapped statuses, and summary shape; router tests cover response contract. |
| DOC-REQ-008 | FR-012 | `moonmind/integrations/jira/browser.py` rich-text normalization and issue detail model | Service tests cover description/acceptance extraction, empty fields, target-specific recommended imports, and no raw ADF in browser response. |
| DOC-REQ-009 | FR-015, FR-016, FR-017, FR-018 | `task-create.tsx` browser state machine and import controls | Frontend tests navigate project -> board -> column -> issue, switch target without clearing selected issue, and verify issue selection alone does not mutate draft. |
| DOC-REQ-010 | FR-018, FR-019, FR-020, FR-022 | `task-create.tsx` import mode/write mode logic | Frontend tests cover preset brief, execution brief, description only, acceptance only, replace, append, and clear separator behavior. |
| DOC-REQ-011 | FR-027, FR-028, FR-029, FR-030 | `task-create.tsx` local provenance and session storage helpers | Frontend tests verify Jira chip, local-only provenance, session project/board memory when enabled, and no submitted provenance requirement. |
| DOC-REQ-012 | FR-030, FR-031, FR-032, FR-033 | `jira_browser.py` safe errors; `task-create.tsx` local error handling | Router tests normalize Jira errors; frontend tests simulate fetch failures and assert Create button/manual editing remain available. |
| DOC-REQ-013 | FR-036 | `task-create.tsx` browser controls, labels, focus behavior | Frontend accessibility tests assert labeled controls, keyboard-reachable actions, active column state, target context, and focus/success notice after import. |
| DOC-REQ-014 | FR-035 | All implementation test files listed in plan | Required focused backend/frontend suites plus `./tools/test_unit.sh`; trace each task to tests in `tasks.md`. |

## Completeness Gate

- Every `DOC-REQ-*` in `spec.md` is represented above.
- Every `DOC-REQ-*` maps to at least one `FR-*`.
- Every mapped requirement includes an implementation surface and validation strategy.
