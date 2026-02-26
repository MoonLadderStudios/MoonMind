# Requirements Traceability Matrix: Task UI Queue Layout Switching

**Feature**: `037-queue-layout-switch`  
**Source**: `docs/TaskUiQueue.md`

| DOC-REQ ID | Mapped FR(s) | Planned Implementation Surface | Validation Strategy |
|------------|--------------|--------------------------------|--------------------|
| `DOC-REQ-001`<br/>Maintain existing table on ≥768 px while serving cards on smaller breakpoints | `FR-002`, `FR-004`, `FR-007` | `api_service/static/task_dashboard/dashboard.js` (`renderQueueTable`, `renderQueueLayouts`) plus Tailwind rules in `dashboard.tailwind.css` controlling `.queue-table-wrapper`/`.queue-card-list` display | Automated dashboard tests asserting both layouts render from one call; manual QA via quickstart at 320 px, 768 px, 1024 px; CSS snapshot review to confirm breakpoint toggles |
| `DOC-REQ-002`<br/>Single queue field definition list | `FR-001` | `dashboard.js` constants next to `toQueueRows()`, helper `renderQueueFieldValue` | JS unit tests verifying both table and card renderers iterate the same array; reviewer spot-check ensures future fields require a single change |
| `DOC-REQ-003`<br/>Shared helper powering `/tasks/queue` & Active queue subsets | `FR-004`, `FR-005`, `FR-006` | `dashboard.js` updates to `renderQueueListPage`, filter re-render code, and `renderActivePage` queue panels | Tests covering filter callbacks + Active page outputs; manual verification that orchestrator rows remain table-only |
| `DOC-REQ-004`<br/>Card composition with header, metadata, definition list, CTA | `FR-003`, `FR-008` | `renderQueueCards` helper inside `dashboard.js`; `.queue-card-*` classes in `dashboard.tailwind.css` | DOM snapshot/unit test for card markup; accessibility inspection (list roles, focus order) documented in quickstart |
| `DOC-REQ-005`<br/>Table composition derived from shared definitions | `FR-002` | `renderQueueTable` + supporting header/cell builders in `dashboard.js` | Unit tests comparing header/value order to `queueFieldDefinitions`; manual regression test vs pre-change screenshots |
| `DOC-REQ-006`<br/>CSS/tailwind rules + MoonMind tokens | `FR-007` | `dashboard.tailwind.css`, rebuild via `npm run dashboard:css:min`, documentation update in `docs/TaskDashboardStyleSystem.md` | Visual QA + dark-mode checks; gzip delta measurement recorded in docs |
| `DOC-REQ-007`<br/>Accessibility & DOM growth guardrails | `FR-003`, `FR-004`, `FR-008` | Semantic markup returned by `renderQueueCards`/`renderQueueLayouts`; existing status badge helpers reused | Manual screen-reader/focus path validation; automated tests verifying `role="list"` presence and row count parity |
| `DOC-REQ-008`<br/>Testing, documentation, rollout notes | `FR-009`, `FR-010` | `tests/task_dashboard/*.js` additions, `docs/TaskDashboardStyleSystem.md` + `specs/037-queue-layout-switch/quickstart.md` updates, measurement notes | `./tools/test_unit.sh` run recorded in task log; QA checklist stored in quickstart/checklists |
