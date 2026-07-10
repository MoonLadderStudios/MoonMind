# Workflow List Display Modes

Status: Proposed declarative UI contract  
Owners: MoonMind Engineering  
Last updated: 2026-07-10  
Canonical for: Workflows/Create list display modes, masthead list-mode control, workflow sidebar/table visual continuity, and first-workflow selection fallback when the list is hidden

**Implementation tracking:** Rollout task lists, ticket breakdowns, and local handoff notes belong under `docs/tmp/`, Jira, or gitignored implementation notes. This document defines the durable product and UI contract for the shared workflow-list display system.

---

## 1. Purpose

MoonMind should expose workflow navigation as one declarative display system with three mutually exclusive list presentations:

| User label | Internal mode | Meaning |
| --- | --- | --- |
| No list | `hidden` | Hide the workflow list and show only the primary surface. |
| Sidebar list | `sidebar` | Show the workflow list as a left sidebar beside the primary surface. |
| Full screen table | `table` | Show the full Workflows table as the primary surface. |

For the first implementation, the system applies only to the Workflows and Create surfaces:

1. Workflows detail surfaces may show **No list** or **Sidebar list**.
2. The Workflows list route is the **Full screen table** state.
3. The Create page may show **No list** or **Sidebar list**.
4. Selecting **Full screen table** from Create navigates to the Workflows page.
5. Future pages may opt into the same system by declaring how each mode composes with their primary surface.

The interaction should feel like changing how much of the same workflow list is visible, not like jumping between unrelated pages. The sidebar must read as the natural collapsed form of the full table's first column.

---

## 2. Relationship to existing UI contracts

This document narrows and supersedes only the list-display mode and masthead control portions of the existing workflow workspace design. It does not replace the page-specific content contracts.

Use these documents together:

- `docs/UI/CollectionWorkspaceLayout.md` is canonical for the far-left application rail, shared collection-sidebar primitive, and workspace geometry.
- `docs/UI/DashboardSPAArchitecture.md` remains canonical for the persistent SPA shell, route ownership, providers, and same-origin API model.
- `docs/UI/DashboardDesignSystem.md` remains canonical for visual language, focus states, motion posture, glass/matte treatment, and shared tokens.
- `docs/UI/WorkflowsListPage.md` remains canonical for table columns, filters, sorting, pagination, mobile cards, row data, and list API behavior.
- `docs/UI/WorkflowDetailsPage.md` remains canonical for workflow detail content, tabs, actions, evidence, logs, artifacts, and recovery states.
- `docs/UI/CreatePage.md` remains canonical for workflow authoring, step composition, schema-driven inputs, publishing controls, and submission.
- `docs/UI/WorkflowWorkspaceSidebar.md` remains canonical for the desktop list-to-detail workspace foundation where it does not conflict with this three-mode system.

When this document conflicts with an older sidebar control rule, this document wins for the new system. In particular, the old separate `Hide workflow sidebar`, `Open workflow sidebar`, and `Expand workflow list` controls should collapse into the shared masthead radio group for pages covered by this design.

---

## 3. Declarative model

The UI should represent list display as a small, typed state machine rather than scattered page-specific booleans.

```ts
export type WorkflowListDisplayMode = 'hidden' | 'sidebar' | 'table';

export type WorkflowListDisplaySurface =
  | 'workflows-table'
  | 'workflow-detail'
  | 'workflow-start'
  | 'future-dashboard-surface';

export type WorkflowListSelection = {
  workflowId: string | null;
  source: 'route' | 'last-selected' | 'first-visible-row' | 'none';
};
```

The mode registry should be data-first:

```ts
export const WORKFLOW_LIST_DISPLAY_MODES = [
  {
    value: 'hidden',
    label: 'No list',
    icon: Square,
    listRegion: 'none',
  },
  {
    value: 'sidebar',
    label: 'Sidebar list',
    icon: PanelLeft,
    listRegion: 'sidebar',
  },
  {
    value: 'table',
    label: 'Full screen table',
    icon: Rows3,
    listRegion: 'primary-surface',
  },
] satisfies ListDisplayModeDefinition[];
```

Mode resolution is declarative:

```ts
type ResolvedWorkflowListDisplay = {
  requestedMode: WorkflowListDisplayMode;
  effectiveMode: WorkflowListDisplayMode;
  surface: WorkflowListDisplaySurface;
  routeAction: 'none' | 'navigate-workflows' | 'navigate-selected-detail' | 'resolve-first-workflow';
  primarySurface: 'workflow-detail' | 'workflow-start' | 'workflow-table' | 'empty-workflows';
  listSurface: 'none' | 'sidebar' | 'table';
};
```

Rules:

1. `requestedMode` is what the user selected in the masthead control.
2. `effectiveMode` is the mode that can actually render on the current route after required navigation or first-row resolution.
3. `table` always means the Workflows list page as the primary surface.
4. `sidebar` always means the workflow list is visible in a left rail and the current page's primary surface remains visible to the right.
5. `hidden` always means no workflow list region is rendered.
6. Route-derived selection is stronger than remembered selection.
7. Remembered selection is stronger than first-row fallback.
8. First-row fallback is used only when a mode requires a Workflow Detail surface and there is no selected workflow yet.

---

## 4. Route and presentation matrix

| Current route family | Selected mode | Required route result | Primary surface | List surface |
| --- | --- | --- | --- | --- |
| `/workflows` | `table` | Stay on `/workflows`. | Full Workflows table. | Table. |
| `/workflows` | `sidebar` | Navigate to selected workflow detail, or first visible workflow when none is selected. | Workflow Detail. | Sidebar. |
| `/workflows` | `hidden` | Navigate to selected workflow detail, or first visible workflow when none is selected. | Workflow Detail. | None. |
| `/workflows/{workflowId}` and detail subroutes | `table` | Navigate to `/workflows` with safe list context preserved. | Full Workflows table. | Table. |
| `/workflows/{workflowId}` and detail subroutes | `sidebar` | Stay on current detail route. | Workflow Detail. | Sidebar. |
| `/workflows/{workflowId}` and detail subroutes | `hidden` | Stay on current detail route. | Workflow Detail. | None. |
| `/workflows/new` | `table` | Navigate to `/workflows`. | Full Workflows table. | Table. |
| `/workflows/new` | `sidebar` | Stay on `/workflows/new`. | Create page. | Sidebar. |
| `/workflows/new` | `hidden` | Stay on `/workflows/new`. | Create page. | None. |

Rules:

1. The Workflows page is the only full-screen table page in the initial system.
2. The Create page never embeds the full table; choosing `table` from Create navigates to `/workflows`.
3. Choosing `hidden` from the Workflows table must not leave the user on an empty table-less `/workflows` page. It must resolve a Workflow Detail target.
4. If no workflow has been selected yet, choosing `hidden` from the Workflows table loads the Workflow Detail for the first item in the current effective list.
5. If no workflow has been selected yet, choosing `sidebar` from the Workflows table should also resolve to a detail target so the sidebar has a primary Workflows surface beside it.
6. If the current list has no selectable workflows, the UI stays on `/workflows`, keeps the table/list empty state visible, and announces that there is no workflow to open.
7. Detail subroutes preserve their subroute when switching between `hidden` and `sidebar`.
8. Switching from any detail subroute to `table` returns to `/workflows`; it does not keep a hidden detail pane mounted.
9. Switching from Create to `table` must respect the Create page's draft-preservation and unsaved-change policy. The mode control must not silently discard a draft.

---

## 5. First-workflow fallback

The first-workflow fallback makes the **No list** mode safe when the user has not yet selected a workflow.

Resolution order:

1. Use the workflow ID in the current route when the route already identifies a workflow.
2. Otherwise use the last workflow explicitly selected during the current dashboard session when that workflow is still authorized.
3. Otherwise fetch or reuse the current Workflows list query and choose the first selectable row after the active filters, sort, source, page size, and cursor rules have been applied.
4. If no row exists, show the Workflows empty state and keep the table available.

Rules:

1. "First item" means the first visible workflow row in the same list the user was looking at, not an arbitrary newest workflow from a different query.
2. The fallback may use cached list data if it exactly matches the current effective list query.
3. If the list query is loading, the control may enter a resolving state with accessible text such as `Opening first workflow...`.
4. If the list query fails, show a recoverable list error and do not navigate to a guessed workflow.
5. The fallback must never expose workflows outside the current user's authorization scope.
6. A selected workflow that is absent from the current filtered list may still be opened from remembered selection only if the detail API authorizes it; the sidebar should label it as the current workflow rather than implying it matches active filters.

---

## 6. Shell/workspace list display control

The list display selector belongs to the current collection's shell/workspace utility region. It must remain adjacent to collection context without becoming a centered masthead element or moving into page content, the collection sidebar, or a table toolbar.

```text
[far-left application rail] [collection sidebar when visible] [collection utility + primary pane]
```

The control remains one accessible radio group with `No list`, `Sidebar list`, and `Full screen table` options using `Square`, `PanelLeft`, and `Rows3`. The selected option reflects resolved route state; keyboard users enter with Tab and use arrow keys. On routes without a declared contract, hide it. On mobile, hide desktop-only modes until a mobile contract exists.

The shell supplies common control styling and placement; each collection supplies its accessible name and route resolution. Workflow and Recurring preferences remain separate.

---

## 7. Surface composition

### 7.1 Workflows detail with no list

`hidden` mode on a workflow detail route renders only the Workflow Details surface inside the dashboard content panel.

Rules:

1. No workflow list, sidebar, list header, or list divider is rendered.
2. The Workflow Details route, tabs, actions, and data fetching remain unchanged.
3. The detail page may use the width previously occupied by the sidebar.
4. The masthead radio group remains available so the user can reopen the sidebar or return to the table.
5. The selected workflow ID remains the route parameter, not a hidden local-only state.

### 7.2 Workflows detail with sidebar list

`sidebar` mode on a workflow detail route renders:

1. the sidebar list on the left;
2. the existing Workflow Details surface on the right;
3. the same route-derived selected workflow and detail subroute.

Rules:

1. The sidebar is owned by the workspace/layout composition layer, not by the detail page body.
2. It is the first dashboard-content column immediately right of the far-left application rail; it is never inside the detail page's centered/max-width wrapper.
2. Sidebar row links navigate to canonical workflow detail URLs.
3. The active workflow row uses `aria-current="page"`.
4. Sidebar list failures do not prevent the selected detail from rendering.
5. Detail failures do not erase a successfully loaded sidebar.

### 7.3 Create with no list

`hidden` mode on `/workflows/new` renders the Create page alone.

Rules:

1. No workflow list, sidebar, list header, or list divider is rendered.
2. Create page draft state, schema-driven forms, validation, and submit behavior remain owned by `docs/UI/CreatePage.md`.
3. The masthead radio group remains available so the user can open the sidebar or navigate to the table.

### 7.4 Create with sidebar list

`sidebar` mode on `/workflows/new` renders:

1. the sidebar list on the left;
2. the Create page on the right.

Rules:

1. The sidebar shows workflows using the same compact/list-row data as the Workflows table.
2. The Create form remains the primary surface and must not be squeezed into an unusable width.
3. Clicking a workflow row in the sidebar navigates to that workflow's detail route; it does not select a workflow inside the Create form.
4. The Create draft should be preserved according to the Create page's draft persistence model before sidebar navigation leaves the route.
5. The sidebar must be optional; Create remains fully usable in `hidden` mode.

### 7.5 Full screen table

`table` mode renders the Workflows list page.

Rules:

1. `/workflows` is the canonical URL.
2. The table owns filters, sorting, view options, column visibility, pagination, active filter chips, loading states, empty states, and mobile cards.
3. No Workflow Detail pane remains active in the UI.
4. No Create form remains mounted as a hidden pane.
5. When entered from sidebar mode, the table should reuse list query state and scroll position where practical.

---

## 8. Sidebar-as-table-slice visual contract

The sidebar is not a separate visual invention. It is the collapsed form of the full Workflows table.

The sidebar should render as a one-column table slice:

```text
┌──────────────────────────────┐
│ Workflow                     │  ← same header row styling as table
├──────────────────────────────┤
│ Workflow row 1               │  ← same row height as table
├──────────────────────────────┤
│ Workflow row 2               │
├──────────────────────────────┤
│ Workflow row 3               │
└──────────────────────────────┘
```

Rules:

1. The first visible row in the sidebar is a header row labeling the first column.
2. The header label should be `Workflow` unless the Workflows table changes the first column label.
3. Header typography, casing, background, border, padding, and hover/focus exclusions match the full table header.
4. Sidebar body rows use the exact same block size as full table body rows.
5. The table and sidebar must share row metric tokens rather than duplicating pixel values.
6. Recommended shared tokens:

```css
:root {
  --workflow-list-header-row-height: 2.75rem;
  --workflow-list-body-row-height: 4rem;
  --workflow-list-column-workflow-width: 20rem;
  --workflow-list-divider-width: 1px;
  --workflow-list-divider-color: rgb(var(--mm-border) / 0.72);
}
```

7. The sidebar's right divider uses the same width, color, and vertical rhythm as the table's column divider or row grid line.
8. The sidebar should align its header baseline, row top edges, row bottom borders, and active row background with the full table's first column.
9. The full table's first column should be width-compatible with the sidebar so switching modes feels like revealing or hiding additional columns to the right.
10. The sidebar row content may hide secondary table columns, but it must not introduce extra vertical metadata that changes row height.
11. Titles clamp within the shared row height; overflow must not make individual sidebar rows taller than table rows.
12. Status icons or compact supplements may appear inside the Workflow cell only if they also fit the shared row height.
13. Empty, loading, and error sidebar states should preserve the header row and divider so the list region still feels like the same table slice.

Implementation guidance:

1. Prefer a shared `WorkflowListTableFrame` or `WorkflowListRows` primitive that can render `variant="table"` and `variant="sidebar"`.
2. If semantic `<table>` markup remains practical, the sidebar may render a one-column `<table>` with the same `<thead>` and `<tbody>` row primitives.
3. If div/grid markup is used for virtualization or layout reasons, preserve table-equivalent semantics with roles and accessible labels.
4. Do not style the sidebar as a card stack, menu, or unrelated navigation rail.

---

## 9. Motion and continuity

Switching between `sidebar` and `table` should feel like expanding or collapsing the same list.

Rules:

1. Header row height is constant before, during, and after the transition.
2. Body row height is constant before, during, and after the transition.
3. The sidebar right divider becomes the table's first-column divider or aligns with it during expansion.
4. The full table reveals additional columns to the right of the Workflow column.
5. The primary surface to the right of the sidebar withdraws when entering `table` mode; it should not shove rows vertically.
6. Avoid large page-slide animations.
7. In reduced-motion mode, the mode change snaps or uses near-instant opacity changes.
8. When the same list query and row set are used, preserve vertical scroll position where practical.
9. If table and sidebar cannot share scroll position because of pagination or virtualization, preserve the selected row and focus target instead.
10. The route may change, but the dashboard shell and masthead remain mounted.

---

## 10. State persistence

The system should remember user intent without making URLs confusing.

Recommended persisted preferences:

```ts
type DashboardWorkflowListPreferences = {
  workflowListDisplayMode?: 'hidden' | 'sidebar' | 'table';
  lastSelectedWorkflowId?: string;
};
```

Rules:

1. Persist the last explicit list display mode in dashboard preferences.
2. Persist the last explicitly selected workflow ID for first-row fallback avoidance.
3. Do not put `hidden` or `sidebar` mode in the workflow detail path; detail URLs remain canonical workflow URLs.
4. Full table mode is naturally represented by `/workflows`.
5. Query parameters may preserve list filters, sort, page size, cursor, and safe return context according to the Workflows list contract.
6. Query parameters must not include raw prompts, full Create drafts, secrets, presigned URLs, logs, artifacts, or large detail payloads.
7. On direct desktop visits to `/workflows/{workflowId}`, default to the persisted mode when it is `hidden` or `sidebar`; otherwise default to `sidebar` so navigation context is visible.
8. On direct desktop visits to `/workflows/new`, default to the persisted mode when it is `hidden` or `sidebar`; otherwise default to `hidden` so authoring remains focused.
9. On direct visits to `/workflows`, effective mode is `table` regardless of persisted preference until the user selects another mode.

---

## 11. Data fetching and cache reuse

The sidebar and full table should share a list data model.

Rules:

1. The full table and sidebar use the same authorized workflow list API family.
2. The sidebar should reuse cached Workflows list data when the query parameters match.
3. The first-workflow fallback uses the same list query as the table the user was viewing.
4. Selected detail data is fetched independently from list data.
5. Create page data and draft state are independent from list data.
6. List refetching must not steal focus from the Create form, Workflow Detail, or masthead mode control.
7. Live updates may refresh row content, but they must preserve active selection, row height, and focus.
8. Sidebar empty, loading, and error states are scoped to the sidebar list region and do not blank the primary surface.
9. Table empty, loading, and error states remain owned by the Workflows list page.

---

## 12. Accessibility requirements

Rules:

1. The masthead mode selector is reachable by keyboard immediately after the MoonMind brand link.
2. The mode selector has a stable accessible name such as `Workflow list display`.
3. Each option announces its label and checked state.
4. Changing to a mode that navigates routes must move focus deterministically after navigation.
5. Choosing `hidden` from `/workflows` with no prior selection announces first-workflow resolution or failure.
6. The sidebar list has an accessible name such as `Workflow navigation`.
7. The sidebar header row is not announced as a selectable workflow.
8. Workflow row links include enough text to identify the workflow.
9. Active workflow links expose `aria-current="page"`.
10. Color is not the only active, hover, selected, or focus indicator.
11. Mobile users must not encounter hidden desktop-only sidebar controls in the accessibility tree.
12. Unsaved Create draft warnings, when required, must be accessible before route-changing mode changes complete.

---

## 13. Empty, loading, and error states

| State | Behavior |
| --- | --- |
| Table loading | Show the Workflows list loading state in table mode. |
| Sidebar loading | Keep the sidebar header/divider visible and show compact loading rows or `Loading workflows...`. |
| First-workflow resolving | Keep the selected radio interaction acknowledged and announce `Opening first workflow...`. |
| First-workflow not found | Stay on `/workflows`, show the empty list state, and keep table mode available. |
| Sidebar list empty beside Create | Show the sidebar header plus `No workflows match the current list filters.`; Create remains usable. |
| Sidebar list error beside Detail | Show retry inside sidebar; Detail remains usable when authorized. |
| Sidebar list error beside Create | Show retry inside sidebar; Create remains usable. |
| Detail error with sidebar | Show existing Workflow Details error; sidebar remains usable when it can load. |
| Create unsaved draft before table navigation | Use the Create page's draft preservation or unsaved-change confirmation model before leaving `/workflows/new`. |

Rules:

1. Sidebar empty/loading/error states preserve the table-slice frame.
2. Sidebar recovery retries only sidebar list data.
3. Detail recovery retries only detail data.
4. Table recovery retries the Workflows list page data.
5. Mode controls remain available unless a route guard intentionally blocks navigation to protect user data.

---

## 14. Future extension model

Other pages may adopt this system later by registering a surface contract.

```ts
type WorkflowListDisplaySurfaceContract = {
  surface: WorkflowListDisplaySurface;
  supportsHidden: boolean;
  supportsSidebar: boolean;
  supportsTable: boolean;
  hiddenPrimarySurface: ReactNode;
  sidebarPrimarySurface: ReactNode;
  onTableMode: 'navigate-workflows' | 'render-local-table' | 'unsupported';
};
```

Rules:

1. A page must declare mode behavior before the masthead control appears on that page.
2. Unsupported modes should not appear enabled.
3. Pages outside Workflows/Create should not receive accidental workflow sidebars just because they are dashboard routes.
4. Future non-workflow lists may reuse the visual pattern, but they should define their own list entity, route, and selection semantics.
5. The Workflows table remains the canonical table target for workflow entities.

---

## 15. Testing contract

Implementation should add or preserve tests for these behaviors:

1. The masthead renders the list display radio group immediately after the MoonMind brand on Workflows and Create surfaces.
2. The radio group exposes `No list`, `Sidebar list`, and `Full screen table` options with the `Square`, `PanelLeft`, and `Rows3` icons.
3. `/workflows` resolves to `table` mode.
4. Choosing `hidden` from `/workflows` with a previously selected workflow navigates to that workflow detail and hides the list.
5. Choosing `hidden` from `/workflows` with no previous selection opens the first visible workflow detail and hides the list.
6. Choosing `hidden` from `/workflows` with an empty list stays on the Workflows empty state and does not guess an ID.
7. Choosing `sidebar` from a workflow detail route shows the sidebar without changing the selected detail route.
8. Choosing `hidden` from a workflow detail route hides the sidebar without changing the selected detail route.
9. Choosing `table` from a workflow detail route navigates to `/workflows` and unmounts the detail pane.
10. Choosing `sidebar` from `/workflows/new` shows the workflow sidebar beside the Create page.
11. Choosing `hidden` from `/workflows/new` shows the Create page without a workflow list.
12. Choosing `table` from `/workflows/new` navigates to `/workflows` after respecting Create draft preservation rules.
13. The sidebar renders a header row labeled `Workflow` before workflow rows.
14. Sidebar header styling matches the Workflows table first-column header styling.
15. Sidebar row height equals Workflows table row height.
16. Sidebar divider styling matches the table divider styling.
17. Switching from sidebar to table preserves list query context where practical.
18. Sidebar list failure does not prevent Workflow Detail or Create from rendering.
19. Detail failure does not erase the sidebar list.
20. Keyboard users can operate the masthead radio group and receive deterministic focus after navigation.
21. Reduced-motion settings disable large layout animations.
22. Unauthorized workflows never appear in table rows, sidebar rows, first-row fallback, remembered selections, or pinned current rows.
