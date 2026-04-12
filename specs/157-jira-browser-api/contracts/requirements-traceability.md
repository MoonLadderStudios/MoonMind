# Requirements Traceability: Jira Browser API

| Source Requirement | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-014 | `moonmind/integrations/jira/browser.py`, `api_service/api/routers/jira_browser.py`, existing Create-page submission remains unchanged | Router and service tests prove this is runtime code; regression tests ensure Jira failures do not require task submission changes. |
| DOC-REQ-002 | FR-002, FR-005, FR-006, FR-008 | Browser service methods for verification, project listing, board listing, column loading, issue grouping, and issue detail | Service tests cover connection verification, allowlisted project browsing, board browsing, and grouped issue responses. |
| DOC-REQ-003 | FR-001, FR-003, FR-004 | Runtime config remains gated in `task_dashboard_view_model.py`; browser router uses MoonMind API paths and trusted Jira service | Runtime-config tests cover feature-flag separation; router tests confirm MoonMind-owned responses and no credential-bearing output. |
| DOC-REQ-004 | FR-006, FR-007 | Board configuration normalization in the browser service | Service tests cover policy denial before request, stable column IDs, status ID mappings, and board order preservation. |
| DOC-REQ-005 | FR-008, FR-009, FR-010 | Board issue grouping in the browser service | Service tests cover mapped issues, empty columns, optional filtering, and an explicit unmapped issue bucket. |
| DOC-REQ-006 | FR-011, FR-012 | Issue summary and issue detail normalization in the browser service | Service tests cover normalized summaries, plain-text description extraction, acceptance criteria extraction, and recommended import text. |
| DOC-REQ-007 | FR-013, FR-014 | Safe error mapping in the router and structured Jira browser errors from the service | Router tests cover structured safe error responses; redaction regression tests assert sensitive values do not appear in browser responses. |

## Completeness Check

- Every `DOC-REQ-*` in `spec.md` maps to at least one functional requirement.
- Every mapped functional requirement has a planned implementation surface.
- Every mapped functional requirement has a planned validation path.
- No source-document requirement is intentionally out of scope for this backend phase.
