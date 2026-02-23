# Quickstart: Task UI Queue Layout Switching

## Prerequisites
- Repo dependencies installed (`pip install -r requirements.txt` if needed, `npm install` for Tailwind CLI). Tailwind scripts live in `package.json`.
- Dashboard assets built at least once: `npm run dashboard:css:min` (writes `api_service/static/task_dashboard/dashboard.css`).
- MoonMind services running locally with queue data available: `docker compose up api rabbitmq celery-worker orchestrator` (or equivalent) plus at least one worker seeding queue rows.
- Browser with responsive dev tools (Chrome, Edge, Safari).

## 1. Run automated unit tests
```bash
./tools/test_unit.sh
```
- Confirms Python/JS suites remain green. Add/extend dashboard tests under `tests/task_dashboard/` so regressions in `renderQueueLayouts` or `queueFieldDefinitions` fail here.

## 2. Build/refresh dashboard CSS bundle
```bash
npm run dashboard:css:min
```
- Rebuilds Tailwind assets so the new `.queue-card-*` rules land in `dashboard.css`.
- Record gzip delta (DOC-REQ-008 target <3 KB):
  ```bash
  gzip -c api_service/static/task_dashboard/dashboard.css | wc -c
  ```
  Capture the number inside your release notes or `docs/TailwindStyleSystem.md` update.

## 3. Verify `/tasks/queue` on mobile widths (≤414 px)
1. Open the dashboard at `https://localhost:8443/tasks/queue`.
2. Enable device emulation at 360 px–414 px.
3. Confirm `.queue-card-list` is present with one `<li>` per queue row, each showing title/id link, status badge, queue/skill metadata line, definition list entries (Queue, Runtime, Skill, Created, Started, Finished), and a `View details` button.
4. Apply a queue filter (status/runtime) and ensure both cards and tables update together (no stale layout).

## 4. Verify `/tasks/queue` on desktop widths (≥1024 px)
1. Resize to 1024 px or larger.
2. Ensure `.queue-table-wrapper` is visible, `.queue-card-list` hidden, and column order matches `queueFieldDefinitions` exactly.
3. Toggle filters + wait for auto-refresh to confirm repeated renders keep cards hidden and tables unchanged.

## 5. Validate "Active" dashboard subset behavior
1. Navigate to `/tasks/active`.
2. Observe queue rows rendered through cards/tables using the same helper while orchestrator/manifests rows remain table-only.
3. Temporarily inject a mocked non-queue row (e.g., by editing fixtures or using existing orchestrator items) and ensure `data-sticky-table="true"` keeps the table visible on small screens.

## 6. Inspect accessibility + semantics
- Use browser dev tools to verify `<ul role="list">`, `<li>`, `<dl>`, `<dt>`, `<dd>` structure.
- Tab through cards to ensure `View details` buttons focus properly and status badges retain ARIA-friendly markup.

## 7. Document findings
- Update `docs/TailwindStyleSystem.md` with the new classes, breakpoint behavior, and recorded bundle delta.
- If QA uncovered manual nuances (e.g., layout tweaks needed for >200 rows), append them to `specs/037-queue-layout-switch/checklists/` or ops runbook before handoff.

## Appendix A - Measurement + QA log template

### A.1 Dashboard CSS gzip log

| Date (UTC) | `gzip -c dashboard.css \| wc -c` | Delta vs previous | Notes |
| --- | --- | --- | --- |
| YYYY-MM-DD | 0000 | +0 B | e.g., "Queue cards launch baseline" |

### A.2 Responsive QA checklist

| Date (UTC) | Route + viewport | Result | Notes |
| --- | --- | --- | --- |
| YYYY-MM-DD | `/tasks/queue` @ 360 px | Pass/Fail | Include filter + auto-refresh observations |
| YYYY-MM-DD | `/tasks/queue` @ 768 px | Pass/Fail | Note when tables reappear |
| YYYY-MM-DD | `/tasks/queue` @ 1024 px | Pass/Fail | Desktop parity summary |
| YYYY-MM-DD | `/tasks/active` @ 414 px | Pass/Fail | Confirm sticky table + cards interplay |

## 8. Verification log (2026-02-23)

- `timeout 300 ./tools/test_unit.sh` — **TIMED OUT (04:16 UTC)**. Pytest printed the initial progress dots while running `python -m pytest -q tests/unit` and then hung waiting on downstream services, so the `timeout` wrapper terminated the process after 5 minutes (exit 124). Please rerun in an environment with the required backing services to capture a definitive PASS/FAIL result.
- `node tests/task_dashboard/test_queue_layouts.js` — PASS (04:18 UTC) covering shared field definitions, table/card parity, the Active page wrapper, and extensibility guards.
- Dashboard CSS gzip size — `gzip -c api_service/static/task_dashboard/dashboard.css | wc -c` reports **3,755 bytes** (+281 B vs `HEAD~1`), satisfying the <3 KB delta budget.
- Manual responsive QA (320 px / 768 px / 1024 px) — pending; follow sections 3–5 above once a browser-accessible environment is available.
