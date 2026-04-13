# Requirements Traceability: Jira UI Test Coverage

| Source Requirement | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-002, FR-014 | `task_dashboard_view_model.py`, `task-create.tsx`, runtime config tests, Create page tests | Assert Jira config is absent when disabled, present only when enabled, endpoint templates are MoonMind-owned, and UI controls remain hidden without complete enabled config. |
| DOC-REQ-002 | FR-005, FR-010 | `moonmind/integrations/jira/browser.py`, service tests, Create page tests | Assert board columns preserve order, include stable IDs/counts/status IDs, and frontend renders returned order. |
| DOC-REQ-003 | FR-005, FR-006, FR-010 | Jira browser service issue grouping and Create page column controls | Assert issues group into service-provided column buckets, empty columns render, unmapped statuses are safe, and column switching changes visible issues. |
| DOC-REQ-004 | FR-007, FR-011 | Jira issue-detail normalization service and Create page issue preview | Assert issue detail returns description text, acceptance criteria text, preset import text, and step import text without browser-side rich-text parsing. |
| DOC-REQ-005 | FR-003, FR-004, FR-006, FR-008 | `task-create.tsx`, Create page tests | Assert one shared browser opens from preset and step targets, displays target context, switches column issues, and selecting an issue does not mutate draft fields. |
| DOC-REQ-006 | FR-008, FR-009 | Create page import mode and write-action tests | Assert import modes feed preview text and explicit replace/append actions write only the selected target. |
| DOC-REQ-007 | FR-009, FR-012 | Create page step update path and submission payload tests | Assert importing into a template-bound step detaches template instruction identity when instructions diverge and preserves unrelated steps. |
| DOC-REQ-008 | FR-008, FR-013 | Create page preset import and preset state tests | Assert importing into preset instructions after preset application shows reapply-needed signaling and does not rewrite already-expanded steps. |
| DOC-REQ-009 | FR-015, FR-016 | Create page provenance state and submission tests | Assert provenance chips are advisory, no Jira provenance is required in submitted payloads, and existing create endpoint/task shape remains unchanged. |
| DOC-REQ-010 | FR-017, FR-018 | Jira browser router error handling, Jira service errors, Create page failure UI | Assert backend errors are structured and sanitized, frontend errors remain inside the browser, and manual editing/submission remain available. |
| DOC-REQ-011 | FR-001, FR-003, FR-004, FR-006, FR-008, FR-009, FR-012, FR-013, FR-016, FR-018, FR-020 | Full Phase 9 frontend/backend validation set | Assert all required disabled, browsing, preview, import, template, reapply, failure, and unchanged submission behaviors are covered by automated tests. |

## Gate Result

All `DOC-REQ-*` identifiers from `spec.md` are mapped to at least one functional requirement and have a planned validation surface. Planning must fail if later edits add a `DOC-REQ-*` without a row in this file.
