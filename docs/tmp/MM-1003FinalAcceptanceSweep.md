# MM-1003 Final Acceptance Sweep

Source issue: MM-1003  
Source traceability: MM-975  
Date: 2026-06-28

## Scope

This sweep covers the remaining MM-1003 QA backlog for the Workflow Workspace Sidebar contract after MM-997 and MM-998.

## Acceptance Coverage

- Desktop list remains the canonical `/workflows` full-width list route: covered by `workflow-list.test.tsx`.
- Desktop detail workspace shell renders detail routes and subroutes: covered by `workflow-detail.test.tsx`.
- Sidebar switching, active highlight, and action containment: covered by MM-1003 workspace switching test.
- Sidebar close/open, focus return, route stability, and persisted collapsed state: covered by MM-1003 workspace control test.
- Expand to full list preserves list query state from workspace mode: covered by MM-1003 expand query test.
- Sidebar failure leaves selected detail usable: covered by MM-1003 sidebar failure test.
- Detail failure leaves loaded sidebar usable and shows pinned current workflow: covered by MM-1003 detail failure test.
- Existing Workflow Details primary actions are verified inside workspace mode, including menu, remediation, lifecycle, pause/cancel dialog behavior, and toasts/dialog surfaces owned by the detail page: covered by MM-1003 workspace actions test plus existing detail action tests.
- Mobile card rendering, card title and `View details` navigation, mobile filters, standalone detail, and absence of sidebar controls in the mobile accessibility tree: covered by MM-1003 mobile list/detail regression tests. Mobile standalone detail uses `Back to workflows` instead of the desktop/sidebar `Expand to full list` label.
- Reduced-motion workspace behavior: covered by scoped `prefers-reduced-motion: reduce` CSS for workspace sidebar/open/detail surfaces.
- Documentation reflection: existing UI docs now link to `docs/UI/WorkflowWorkspaceSidebar.md` as an addendum without replacing their canonical contracts.

## Verification

- Passed on 2026-06-28: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- Passed on 2026-06-28: `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src/entrypoints/workflow-detail.tsx frontend/src/entrypoints/workflow-detail.test.tsx`
- Passed on 2026-06-28: `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/workflow-detail.test.tsx frontend/src/entrypoints/workflow-list.test.tsx` (211 frontend tests).
- Blocked by local Python environment on 2026-06-28: `./tools/test_unit.sh tests/integration/api/test_workflow_console_routes.py` stopped before running tests because Python `pytest` is not installed in this container.
- Earlier local evidence retained for context: `./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src/entrypoints/workflow-detail.tsx frontend/src/entrypoints/workflow-detail.test.tsx frontend/src/entrypoints/workflow-list.test.tsx frontend/src/utils/dashboardPreferences.ts` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/workflow-detail.test.tsx frontend/src/entrypoints/workflow-list.test.tsx`; the non-dashboard wrapper path stopped before frontend tests because Python `pytest` was not installed.

## Residual Risk

The targeted frontend suite, typecheck, and focused lint pass locally. Python-backed route verification could not run because this container does not have `pytest`; no backend or API route code changed in this MM-1003 step.
