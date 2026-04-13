# Requirements Traceability: Jira Failure Handling

| Source Requirement | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-003, FR-004, FR-005, FR-007, FR-008 | `api_service/api/routers/jira_browser.py`; `moonmind/integrations/jira/browser.py`; `frontend/src/entrypoints/task-create.tsx` | Router tests for known and unexpected failures; service tests for empty response normalization; Create page tests proving Jira failures stay local and draft fields remain editable/unchanged. |
| DOC-REQ-002 | FR-002, FR-005, FR-006 | `api_service/api/routers/jira_browser.py`; `frontend/src/entrypoints/task-create.tsx` | Secret-redaction regression tests on browser error responses; frontend tests asserting inline browser-panel failure copy includes manual-continuation guidance. |
| DOC-REQ-003 | FR-007, FR-008, FR-009 | `frontend/src/entrypoints/task-create.tsx`; `frontend/src/entrypoints/task-create.test.tsx` | Create page tests submit a manual task after Jira failure and verify existing submission path/payload shape; issue-detail failure tests assert no draft mutation or import. |
| DOC-REQ-004 | FR-010, FR-011 | `tests/unit/api/routers/test_jira_browser.py`; `tests/unit/integrations/test_jira_browser_service.py`; `frontend/src/entrypoints/task-create.test.tsx`; `./tools/test_unit.sh` | Required validation includes backend structured error tests, secret-safe response tests, empty-state tests where applicable, local UI failure tests, and repo unit wrapper verification. |

## Coverage Notes

- Every `DOC-REQ-*` from `spec.md` maps to one or more functional requirements.
- Every mapped functional requirement has at least one planned implementation surface and one validation path.
- Runtime deliverables are mandatory; docs/spec-only work is insufficient.
