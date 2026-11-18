# Recurring Schedules Page

Status: Desired-state product and implementation contract
Owners: MoonMind Engineering
Last updated: 2026-07-10
Canonical for: Recurring dashboard route, recurring schedule table, recurring schedule sidebar, schedule detail workspace composition, shell/workspace list display behavior for recurring schedules, and the product boundary between recurring schedules and one-off scheduled workflow executions

**Implementation tracking:** rollout task lists, issue links, PR breakdowns, and local handoff notes belong in GitHub issues, `docs/tmp/`, or gitignored implementation notes. This document defines the durable desired-state UI and product contract for the Recurring schedules page.

---

## 1. Purpose

MoonMind should expose recurring schedule definitions as a first-class dashboard workspace.

The page helps operators scan, create, inspect, edit, pause, resume, manually run, and delete recurring workflow schedules. It should reuse the Workflows list/detail mental model: a full table for scanning many items, a sidebar for adjacent navigation after selecting an item, and a detail page for controlling the selected entity.

This page is not a generic Temporal namespace browser and not a combined view of every future-dated workflow execution. It is the control-plane surface for reusable recurring schedule definitions.

---

## 2. Related docs and implementation surfaces

Use this document for Recurring schedules page behavior.

Use related docs for shared concepts and adjacent surfaces:

- `docs/UI/CollectionWorkspaceLayout.md` — canonical masthead navigation, shared collection-sidebar primitive, and Workflow/Recurring detail frame.
- `docs/UI/WorkflowsListPage.md` — reference for the full Workflows table visual rhythm, filtering posture, loading states, mobile cards, pagination, and row scanning behavior.
- `docs/UI/WorkflowListDisplayModes.md` — reference for the shell/workspace list display radio group, the three-mode list model, and sidebar-as-table-slice visual contract.
- `docs/UI/WorkflowWorkspaceSidebar.md` — reference for desktop list-to-detail workspace composition and route-owned sidebars.
- `docs/UI/RecurringScheduleDetailsPage.md` — earlier recurring schedule detail contract; this document narrows and extends it for the full Recurring workspace.
- `docs/UI/WorkflowConsoleArchitecture.md` — dashboard route model and API boundary expectations.
- `docs/UI/DashboardDesignSystem.md` — shared dashboard visual language, focus states, density, panels, and motion posture.
- `docs/Temporal/WorkflowSchedulingGuide.md` — scheduling flows and backend/API expectations.
- `docs/Temporal/TemporalScheduling.md` — Temporal-native scheduling desired state.

Representative implementation surfaces:

```text
frontend/src/entrypoints/schedules.tsx
frontend/src/lib/collectionListDisplayMode.ts
frontend/src/components/workflows/WorkflowWorkspaceSidebar.tsx
frontend/src/styles/dashboard.css
api_service/api/routers/recurring_workflows.py
api_service/services/recurring_workflows_service.py
```

---

## 3. Product stance

The visible product surface should be named **Recurring**, not broadly **Schedules**.

Core rules:

1. The global nav label should be `Recurring`.
2. The page title may be `Recurring` or `Recurring schedules`; use `Recurring schedules` where extra clarity is helpful.
3. The route may remain `/schedules` for compatibility even when the visible label is `Recurring`.
4. The page lists recurring schedule definitions only.
5. One-off scheduled workflow executions belong in the Workflows table and Workflow detail pages.
6. A recurring schedule is a reusable definition that can spawn many workflow executions.
7. A one-off scheduled workflow is a single execution with a future scheduled time; it should use the normal workflow execution lifecycle, status, logs, artifacts, and detail surface.
8. If the visible label ever remains **Schedules**, the product must either add clear `Recurring` and `One-off` sub-navigation or explicitly label the current table `Recurring schedules` and leave one-off scheduled executions out of scope.

Rationale:

- The current API and detail model operate on recurring schedule definitions.
- The actions on this page, such as pause, resume, run now, edit cadence, and delete future dispatch, are definition-level actions.
- Mixing one-off scheduled executions into this table would blur execution state with schedule-definition state.
- Operators already inspect one-off workflow executions through Workflows, where scheduled timing, status, dependencies, artifacts, and logs are available.

Recommended naming model:

| Surface | Label | Notes |
| --- | --- | --- |
| Global nav item | `Recurring` | Compact and unambiguous. |
| Page title | `Recurring schedules` | Explicit in content. |
| Table accessible name | `Recurring schedules` | Clear for assistive technology. |
| Sidebar accessible name | `Recurring schedule navigation` | Distinguishes it from workflow navigation. |
| Detail breadcrumb | `Recurring / {schedule name}` | Avoids broad `Schedules` ambiguity. |
| Existing route | `/schedules` | Keep initially to avoid route churn. |
| Optional future route alias | `/recurring` | May become canonical in a route migration. |

---

## 4. Route and hosting model

The initial canonical routes remain:

```text
/schedules
/schedules/{definitionId}
```

Route purposes:

| Route | Purpose |
| --- | --- |
| `/schedules` | Full Recurring table view. |
| `/schedules/{definitionId}` | Recurring schedule detail, controls, configuration, and run history. |

Rules:

1. `/schedules` opens in full table mode by default on desktop.
2. `/schedules/{definitionId}` opens in schedule detail mode with the recurring schedule sidebar visible by default on desktop.
3. `definitionId` is the stable MoonMind product identity for the schedule definition.
4. The route key must not be a spawned workflow execution ID.
5. Internal navigation should use the dashboard SPA/router model where available, not document reloads.
6. Direct deep links to either route must remain reloadable and shareable.
7. The browser calls MoonMind APIs only. It must not call Temporal directly.
8. Existing `/schedules` links should remain valid even if the visible product label changes to `Recurring`.

Optional future route migration:

1. Add `/recurring` as an alias or new canonical route only after link compatibility is planned.
2. If `/recurring` becomes canonical, redirect or route `/schedules` safely.
3. Do not rename the route and the visible nav label in the same change if that creates migration risk.

---

## 5. List display mode model

Recurring participates in the same dashboard list display concept as Workflows, but the entity is a recurring schedule definition rather than a workflow execution.

Suggested typed model:

```ts
export type RecurringListDisplayMode = 'hidden' | 'sidebar' | 'table';

export type RecurringListDisplaySurface =
  | 'recurring-table'
  | 'recurring-detail'
  | 'future-recurring-create';

export type RecurringListSelection = {
  definitionId: string | null;
  source: 'route' | 'last-selected' | 'first-visible-row' | 'none';
};
```

Route and mode behavior:

| Current route family | Selected mode | Required route result | Primary surface | List surface |
| --- | --- | --- | --- | --- |
| `/schedules` | `table` | Stay on `/schedules`. | Full Recurring table. | Table. |
| `/schedules` | `sidebar` | Navigate to the last selected recurring schedule when the table shows it, otherwise the first visible one. | Recurring detail. | Sidebar. |
| `/schedules` | `hidden` | Same selection as `sidebar`. | Recurring detail. | None. |
| `/schedules/{definitionId}` | `table` | Navigate to `/schedules`. | Full Recurring table. | Table. |
| `/schedules/{definitionId}` | `sidebar` | Stay on current detail route. | Recurring detail. | Sidebar. |
| `/schedules/{definitionId}` | `hidden` | Stay on current detail route. | Recurring detail. | None. |

Default behavior:

1. Direct desktop visits to `/schedules` resolve to `table` mode regardless of persisted preference.
2. Direct desktop visits to `/schedules/{definitionId}` resolve to `sidebar` mode unless the user has explicitly persisted `hidden` for Recurring detail surfaces.
3. Switching to `table` from detail unmounts the detail pane and returns to the full table.
4. Switching to `sidebar` from the table requires a selected schedule: route selection, then the remembered selection if the filtered, sorted table shows it, then the first visible row.
5. Switching to `hidden` from the table follows the same selection fallback as `sidebar`, then hides the list region after detail opens.
6. If no recurring schedule is selectable, stay on `/schedules`, keep the empty table state visible, and announce that there is no recurring schedule to open.
7. Mobile does not expose desktop-only sidebar behavior in the first implementation.

---

## 6. Shell/workspace list display radio control

The list display selector belongs to the current Recurring collection's shell/workspace utility region. It remains adjacent to route and collection context without becoming a centered masthead element or moving into page content, the Recurring sidebar, or the table toolbar.

```text
[Masthead navigation] [Recurring sidebar when visible] [Recurring utility + primary pane]
```

Recurring-specific control contract:

| Mode | Icon | Accessible label | Tooltip/title |
| --- | --- | --- | --- |
| `hidden` | `Square` | `No list` | `No list` |
| `sidebar` | `PanelLeft` | `Sidebar list` | `Sidebar list` |
| `table` | `Rows3` | `Full table` | `Full table` |

Rules:

1. The control is one radio group, not three unrelated buttons.
2. On Recurring routes, expose a stable accessible name such as `Recurring list display`.
3. Each option uses native radio semantics or `role="radio"` with `aria-checked`.
4. The selected option reflects the resolved mode after route transitions settle.
5. Keyboard users Tab into the group and use arrow keys to move between options.
6. Each icon-only option has an accessible name and visible focus state.
7. Hover, selected, disabled, and focus states use the shared dashboard control tokens.
8. `table` always means the full Recurring table at `/schedules`.
9. `sidebar` always means a Recurring schedule sidebar beside Recurring schedule detail.
10. `hidden` keeps the current detail route and removes only the Recurring collection sidebar.
11. The Recurring detail page must never show Workflow rows as its list region.
12. Recurring preference and selection state remain independent from Workflow state.
13. Routes without declared Recurring list-display behavior hide the control.
14. Mobile omits desktop-only modes until a mobile-specific contract exists.

Implementation guidance:

- Generalize the display-mode registry so Workflows and Recurring are entity-aware participants in one shell/workspace control pattern.
- Share icons, radio semantics, focus behavior, and visual treatment while preserving entity-specific labels and route resolution.
- Do not duplicate separate, visually divergent controls for each route family.

---

## 7. Default full-table page layout

The `/schedules` table starts directly below the masthead and reaches both page edges. It uses the Workflows table's header, divider, and transparent body treatment, without a title/description band, refresh/add toolbar, summary counts, or standalone Filters button. An accessible page heading and browser-tab title remain available.

Creation stays available through a compact `+` action in the shell on the overview and beside the Recurring sidebar heading on desktop detail. Both use the accessible label `Create recurring schedule` and open `/workflows/new?scheduleMode=recurring`.

The overview contains schedule definitions only; selecting a name opens `/schedules/{definitionId}`. Table mode does not mount a detail pane.

---

## 8. Saved runtime metadata

Provider Profile, repository, model, and effort describe the saved target, rather than a past occurrence. Current Temporal targets store parameters in `initialParameters`; older queue targets use `job.payload`. Structured repository references display their repository name. A saved model tier displays as `Tier N`; missing values display `-` instead of implying a resolved model or profile. Dispatch and policy diagnostics remain on detail.

---

## 9. Full table row model and columns

The Recurring table should feel like a schedule-specific sibling of the Workflows table.

Recommended row fields:

| Field | Meaning |
| --- | --- |
| `id` / `definitionId` | Stable recurring schedule identity and route key. |
| `name` | Primary display name and link text. |
| `description` | Optional subtitle or tooltip text. |
| `enabled` | Active/paused state input. |
| `scheduleType` | Recurrence type when useful. |
| `cron` | Cadence expression. |
| `timezone` | Cadence timezone. |
| `nextRunAt` | Next expected dispatch time. |
| `lastScheduledFor` | Most recent scheduled run time. |
| `lastDispatchStatus` | Last dispatch outcome/status. |
| `lastDispatchError` | Compact error supplement for attention rows. |
| `scopeType` / `scopeRef` | Legacy provenance, never visibility or action authorization. |
| `permissions` / `actions` | Backend-owned `canEdit`, `canRunNow`, `canDelete`, and `disabledReasons`. |
| `target` | Target workflow/job payload and metadata. |
| `policy` | Overlap, catchup, jitter, and other schedule policy. |
| `temporalScheduleId` | Advanced Temporal identity when available. |
| `updatedAt` | Last definition update or reconciliation timestamp. |

Desktop table columns:

| Column | Contents |
| --- | --- |
| Schedule | Schedule name linked to detail; column filter. |
| State | Active, Paused, or Needs attention; column filter. |
| Provider Profile | Saved target's Provider Profile reference. |
| Repository | Saved repository name; column filter. |
| Model | Authored model or model tier. |
| Effort | Authored effort. |
| Cadence / Timezone | Human-readable cadence with raw cron tooltip; column filter. |
| Next run | Next occurrence time; server sorting. |
| Actions | Run now and pause/resume when available. |

Rules:

1. The first column is the primary accessible link to `/schedules/{definitionId}`.
2. Row click may be supported, but the schedule name must remain a normal link.
3. Column order keeps identity and state before saved runtime metadata and next timing.
4. The `State` column should use the dashboard status pill style family.
5. `Needs attention` should be visually distinct without relying on color alone.
6. Dispatch errors remain on detail, with overview attention indicated by State.
7. Provider Profile, repository, model, and effort use separate columns.
8. Raw target payload belongs in detail, not the table.
9. The table should use the same density, header, border, hover, focus, loading, empty, and error visual language as Workflows.
10. Sorting/filtering should follow the Workflows table posture: deterministic URL state where server-authoritative behavior exists and explicit copy where sorting is current-page-only.
11. The empty state explains that schedules can be created from the workflow page; the shell retains its create action.
12. The table must not fetch per-run workflow details to render the list.
13. The table must not show spawned workflow steps, logs, artifacts, or diagnostics.

---

## 10. Filters, sorting, pagination, and URL state

The Recurring table should converge on the same list interaction quality as Workflows.

Initial filter candidates:

```text
Schedule
State
Target
Repository
Cadence / Timezone
Next run
Last scheduled
Dispatch
Updated
```

Rules:

1. Filter controls should belong to columns where possible.
2. There is no standalone Filters button. Existing active chips can reopen their filter sheet, including filters retained in older URLs.
3. Active filter chips should summarize applied filters and reopen the corresponding filter section.
4. Filter changes reset pagination to the first page.
5. Page-size changes reset pagination to the first page.
6. Pagination should use the same opaque cursor posture as other dashboard lists if the backend is cursor-based.
7. Sorting should be server-authoritative before the UI presents it as global ordering across the full result set.
8. Client-side current-page-only sorting is acceptable as an interim state only when labeled clearly.
9. URL query parameters may preserve filters, page size, cursor, sort, and safe return context.
10. URL state must not include raw target payloads, prompts, secrets, logs, artifacts, or large detail data.

---

## 11. Recurring schedule sidebar

When a user opens `/schedules/{definitionId}` on desktop, the workspace shows a recurring schedule sidebar by default.

The sidebar is the collapsed table-slice version of the Recurring table. It is not a generic nav menu and not the Workflows sidebar.

Suggested shape:

```text
┌──────────────────────────────┐
│ Recurring                [+] │
├──────────────────────────────┤
│ Nightly code scan       ▶    │
├──────────────────────────────┤
│ Weekly dependency audit  Ⅱ   │
└──────────────────────────────┘
```

Rules:

1. The sidebar uses the shared `CollectionSidebar` shell, header, row metrics, selected/focus states, divider, scrolling, and localized state components.
2. It is the first content-region column immediately right of the masthead navigation and is never wrapped inside the detail frame or a centered page container.
3. Sidebar rows link to `/schedules/{definitionId}`.
4. The active schedule row exposes `aria-current="page"`.
5. The sidebar has an accessible name such as `Recurring schedule navigation`.
6. The sidebar list uses the same authorized recurring schedule API family as the full table when possible.
7. The sidebar should reuse cached Recurring table data when the query matches.
8. Sidebar failures do not block selected detail from rendering.
9. Detail failures do not erase a successfully loaded sidebar.
10. The sidebar header row remains visible during loading, empty, and error states.
11. Sidebar row height is compatible with the table's first-column row height.
12. Rows show an accessible Active or Paused icon, without next-run text; dispatch attention remains in detail and the full table State.
13. Sidebar rows must not grow individually based on long descriptions, errors, payloads, or target metadata.
14. Long names clamp; full names remain available through accessible text or title attributes when appropriate.
15. The sidebar has no search box or refresh action. Its localized error state retains Retry.
16. A compact `+` action sits to the right of the Recurring heading, using the same primary-button treatment as Skills.
17. Clicking a spawned Workflow run inside detail navigates to the Workflow detail route and does not change the Recurring sidebar selection.

Visual contract:

1. The sidebar reads as the first-column slice of the full Recurring table.
2. Header typography, row height, divider, hover, selected, and focus states align with the full table and the shared collection-sidebar tokens.
3. Do not style the sidebar as cards, a menu, or a separate navigation rail.
4. The sidebar's right divider aligns with shared table/list divider tokens.
5. Reduced-motion users do not see large layout animations when switching between table and sidebar modes.

---

## 12. Detail page composition

The Recurring detail page is a schedule-definition control surface. It must use the shared `EntityDetailFrame` and reuse the Workflow detail composition, but not imply the schedule definition is itself a workflow execution.

Default desktop layout:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ Masthead navigation                                                                    │
├──────────────────────────┬─────────────────────────────────────────────────────────────┤
│ Recurring sidebar        │ Shared entity-detail frame                                  │
│ content-region far-left  │ breadcrumb, title/state/actions                            │
│                          │ summary/facts, tabs, main, optional rail                   │
└──────────────────────────┴─────────────────────────────────────────────────────────────┘
```

The Recurring sidebar and detail frame are workspace siblings. The full composition is fluid and must not be centered inside a narrower page container.

Header content:

| Element | Contract |
| --- | --- |
| Breadcrumb | `Recurring / {schedule name}`. |
| Title | schedule name. |
| Subtitle | description or compact target summary. |
| State pill | Active, Paused, Needs attention. |
| Primary actions | Refresh, Edit schedule, Run now, Pause/Resume. |
| Destructive action | Delete schedule when backend config exposes delete and authorization allows it. |

Summary cards:

| Card | Source |
| --- | --- |
| Next run | `nextRunAt`. |
| Cadence | Human-readable cadence plus `timezone`; raw `cron` stays in the editor and the Details disclosure. |
| Last run | `lastScheduledFor` or latest run history. |
| Dispatch / Attention | `lastDispatchStatus` and `lastDispatchError`. |

Main sections or tabs:

| Section | Purpose |
| --- | --- |
| `Overview` | High-level state, target, next run, cadence, timezone, policy, latest dispatch. |
| `Runs` | Schedule-owned run history; spawned workflows link to Workflow detail. |
| `Configuration` | Read-only configuration by default; edit mode, drawer, or modal when editing. |
| `Activity` | Optional future audit, reconciliation, and Temporal describe events. |
| `Target payload` | Advanced/debug payload in a closed disclosure. Definition and Temporal schedule IDs, scope, and raw cron sit in a closed Details disclosure; overlap and catch-up policy stay visible. |

Rules:

1. A recurring schedule is not itself a workflow execution.
2. Do not show workflow steps, artifacts, logs, or execution diagnostics directly on the schedule definition page.
3. Runs link to `/workflows/{workflowId}?source=temporal` when workflow IDs are available.
4. Workflow detail remains the source of truth for execution-specific state and artifacts.
5. Editing stays on `/schedules/{definitionId}`.
6. Successful edits refetch detail and run history.
7. Run Now stays on detail and refreshes run history.
8. Pause/Resume should be available from detail when authorized.
9. Delete should clearly state that future recurring dispatch stops, but prior workflow executions and artifacts remain.
10. If the schedule no longer exists, show a not-found state with a link back to Recurring.
11. If run history fails, keep schedule controls available and show a localized runs-panel error.
12. Show update errors inline. Validation failures leave saved state unchanged; a saved/pending Temporal synchronization error requires refreshing the committed definition, not creating a replacement.

---

## 13. Schedule actions

Recurring schedule actions operate on the schedule definition unless explicitly stated otherwise.

| Action | Surface | Behavior |
| --- | --- | --- |
| Create | Shell or Recurring sidebar heading | Starts recurring schedule creation. Initial route: `/workflows/new?scheduleMode=recurring`. |
| Open detail | Table row or sidebar row | Navigates to `/schedules/{definitionId}`. |
| Edit schedule | Detail | Edits definition fields such as name, description, enabled, cron, timezone, policy, and supported target parameters. |
| Run now | Detail | Creates an immediate manual run from the definition. |
| Pause | Detail | Disables future automatic dispatch without deleting prior runs. |
| Resume | Detail | Re-enables future automatic dispatch. |
| Delete schedule | Detail | Stops future recurring runs by deleting or disabling the definition and Temporal schedule. |
| Open spawned workflow | Run history row | Navigates to normal Workflow detail. |

Rules:

1. Row actions in the full table should be limited to safe, common actions unless the action menu has clear confirmation and permission handling.
2. Destructive deletion belongs primarily on detail, visually separated from routine actions.
3. Permission-disabled actions should expose useful disabled reasons when the backend provides them.
4. Delete is rendered only when runtime config exposes a delete route and authorization allows it.
5. Existing workflow executions spawned by the recurring schedule must not be deleted by schedule deletion.

---

## 14. API and runtime config expectations

The page uses MoonMind recurring schedule APIs. The router owns instance-wide
visibility and action permissions; the dashboard consumes those decisions.
It admits requests through `get_instance_access`. Stored
`ownerUserId`, `scopeType`, and `scopeRef` are legacy provenance, not human
ownership gates. The deprecated `scope=personal` and `scope=global` query values
are accepted but ignored; new requests omit them. The dashboard also removes
`scope` from retained boot-config URLs.

Current runtime config from `api_service/api/routers/workflow_console_view_model.py`:

```json
{
  "sources": {
    "schedules": {
      "list": "/api/recurring-workflows",
      "create": "/api/recurring-workflows",
      "detail": "/api/recurring-workflows/{definitionId}",
      "update": "/api/recurring-workflows/{definitionId}",
      "runNow": "/api/recurring-workflows/{definitionId}/run",
      "runs": "/api/recurring-workflows/{definitionId}/runs?limit=200",
      "delete": "/api/recurring-workflows/{definitionId}"
    }
  }
}
```

Endpoint purposes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/recurring-workflows` | Load the instance schedule list after admission. |
| `GET` | `/api/recurring-workflows/{definitionId}` | Load schedule detail. |
| `PATCH` | `/api/recurring-workflows/{definitionId}` | Save schedule edits. |
| `POST` | `/api/recurring-workflows/{definitionId}/run` | Trigger immediate manual run. |
| `GET` | `/api/recurring-workflows/{definitionId}/runs` | Load schedule-owned run history. |
| `DELETE` | `/api/recurring-workflows/{definitionId}` | Delete or disable future recurring dispatch. |

Definition fields are listed in [the row model](#9-full-table-row-model-and-columns).

The router already returns result-level `count`, `activeCount`, `next24hCount`,
`attentionCount`, and action permissions, with cursor pagination and server-side
sorting/filtering. The admitted operator can manage every instance schedule; the UI
must use the returned action fields and disabled reasons rather than infer
permissions from scope or owner fields. Delete additionally requires the
runtime-config route. A compact `targetSummary` is a desired display extension;
the current router supplies nested `target` metadata instead.

---

## 15. One-off scheduled workflow executions

One-off scheduled workflow executions are intentionally out of scope for the Recurring page.

Rules:

1. One-off scheduled workflows remain in the Workflows table.
2. Workflows may expose filters for scheduled time, queued/scheduled status, and future `scheduledFor` timestamps.
3. A one-off scheduled workflow opens the normal Workflow detail page.
4. Execution-specific steps, logs, artifacts, cost, live logs, and diagnostics stay in Workflow detail.
5. The Recurring page must not include one-off scheduled executions unless the product intentionally changes from `Recurring` to a broader `Schedules` model with clear sub-navigation.

If a future combined Schedules product is desired, it should use explicit sub-surfaces:

```text
Schedules
  Recurring
  One-off
```

That combined model is a separate design and should not be smuggled into this Recurring workspace.

---

## 16. Empty, loading, error, and permission states

Full table states:

| State | Behavior |
| --- | --- |
| Loading | Preserve the table header and show table skeletons. |
| Empty | Show `No recurring schedules yet.` and a create action. |
| Filtered empty | Show that no recurring schedules match the current filters and keep filters editable. |
| List error | Show a recoverable error in the table region; keep the shell/workspace mode selector available. |
| Permission denied | Show an access message without leaking schedule identities. |

Sidebar states:

| State | Behavior |
| --- | --- |
| Loading | Preserve header row and divider; show compact loading rows. |
| Empty | Preserve sidebar frame and state that no recurring schedules are available. |
| Error | Show retry inside sidebar; do not block selected detail. |
| Selected item absent from list | Keep detail route authoritative; optionally label the selected schedule as outside the current list filters if authorized. |

Detail states:

| State | Behavior |
| --- | --- |
| Loading | Show schedule detail loading state beside sidebar when sidebar is available. |
| Not found | Show not-found message and link back to Recurring. |
| Detail error | Show recoverable detail error; do not erase sidebar when sidebar loaded. |
| Runs error | Keep detail controls available and show localized run-history error. |
| Update error | Show inline error and keep edit state visible when practical. |
| Delete unavailable | Hide delete or show disabled reason only if the backend exposes a clear reason. |

---

## 17. Responsive behavior

Desktop:

1. `/schedules` renders full table mode.
2. `/schedules/{definitionId}` renders sidebar plus detail by default.
3. The shell/workspace list display radio control is available on Recurring routes.
4. Sidebar and detail scroll independently when practical.

Tablet / narrow desktop:

1. The table may reduce visible columns or use column visibility controls.
2. The sidebar may collapse at a breakpoint where detail becomes too narrow.
3. If the sidebar is hidden due to layout constraints, do not expose active desktop sidebar controls in the accessibility tree.

Mobile:

1. Use a card-list-to-standalone-detail flow.
2. Hide the desktop list display radio control until mobile-specific behavior exists.
3. Creation remains available in shell navigation.
4. Cards include schedule name, state, cadence, next run, Provider Profile, repository, model, and effort.
5. Mobile detail keeps the same actions and run-history behavior, but without the desktop sidebar.

---

## 18. Accessibility requirements

Rules:

1. The shell/workspace mode selector has a stable accessible name such as `Recurring list display`.
2. The mode selector uses radio semantics and announces checked state.
3. The mode selector is keyboard reachable in the Recurring collection utility region after route/page context.
4. Route-changing mode switches restore focus deterministically to the page title, selected schedule row, active sidebar row, or detail heading.
5. The full table has an accessible name such as `Recurring schedules`.
6. The sidebar has an accessible name such as `Recurring schedule navigation`.
7. The sidebar header row is not announced as a selectable schedule.
8. Active sidebar links expose `aria-current="page"`.
9. The create button has visible text or an accessible label such as `Create recurring schedule`.
10. Status and attention are not conveyed by color alone.
11. Error and permission messages use appropriate live-region or alert semantics when they appear after user action.
12. Icon-only controls have accessible names and visible focus states.
13. Mobile users must not encounter hidden desktop-only sidebar controls in the accessibility tree.
14. Long schedule names and IDs remain accessible even when visually truncated.

---

## 19. Motion and continuity

Switching between full table and sidebar/detail should feel like changing how much of the same recurring schedule list is visible.

Rules:

1. Avoid large page-slide animations.
2. Preserve `DashboardShell`, masthead, and collection-utility continuity.
3. Header row height should remain stable between table and sidebar.
4. Sidebar row height should match or intentionally derive from the table first-column row height.
5. Preserve selected schedule identity across mode changes.
6. Preserve list query context and scroll position where practical.
7. In reduced-motion mode, snap or use near-instant opacity changes.
8. If scroll position cannot be preserved, preserve selected row and focus target instead.

---

## 20. State persistence

Recommended persisted preferences:

```ts
type DashboardRecurringListPreferences = {
  recurringListDisplayMode?: 'hidden' | 'sidebar' | 'table';
  lastSelectedDefinitionId?: string;
};
```

Rules:

1. Persist the last explicit Recurring list display mode.
2. Persist the last explicitly selected recurring schedule ID for fallback navigation.
3. Do not put `hidden` or `sidebar` mode in the detail path; detail URLs remain canonical.
4. Full table mode is naturally represented by `/schedules`.
5. On direct visits to `/schedules`, effective mode is `table` regardless of persisted preference.
6. On direct desktop visits to `/schedules/{definitionId}`, default to persisted `hidden` or `sidebar`; otherwise default to `sidebar`.
7. A remembered schedule ID is used only when the visible table (an authorized list) contains it; otherwise it is dropped silently.

---

## 21. Testing contract

Implementation should add or preserve tests for these behaviors:

1. The visible nav/page copy uses `Recurring` or `Recurring schedules` rather than ambiguous broad `Schedules`, unless a combined Schedules design is intentionally implemented.
2. `/schedules` resolves to full table mode on desktop.
3. The table starts at the top without an overview control band or schedule counts.
4. The create action has accessible text `Create recurring schedule` and points to the recurring creation flow.
5. The shell/workspace utility region renders the list display radio group on Recurring routes with `No list`, `Sidebar list`, and `Full table` options.
6. The radio group exposes an accessible name such as `Recurring list display`.
7. Selecting `Full table` from detail navigates to `/schedules` and unmounts the detail pane.
8. Selecting `Sidebar list` from `/schedules` opens the last selected recurring schedule when the filtered table shows it, otherwise the table's first visible schedule.
9. Selecting `No list` on detail keeps the detail route and hides the recurring sidebar.
10. Selecting a schedule from the table navigates to `/schedules/{definitionId}`.
11. Direct desktop visits to `/schedules/{definitionId}` render the recurring schedule sidebar by default.
12. The detail sidebar lists recurring schedules, not workflows.
13. The active recurring schedule row exposes `aria-current="page"`.
14. Sidebar failure does not prevent authorized detail from rendering.
15. Detail failure does not erase a successfully loaded sidebar.
16. One-off scheduled workflow executions do not appear in the Recurring table.
17. Run-history rows link to Workflow detail when spawned workflow IDs are available.
18. Delete confirmation copy says prior workflow executions and artifacts remain available.
19. Mobile does not expose non-rendered desktop sidebar controls.
20. Keyboard users can operate the shell/workspace radio group and receive deterministic focus after navigation.
21. Reduced-motion settings avoid large layout animations.
22. Unauthorized schedules never appear in table rows, sidebar rows, first-row fallback, remembered selections, or pinned current rows.

---

## 22. Rollout notes

Recommended implementation sequence:

1. Update visible copy from broad `Schedules` to `Recurring` / `Recurring schedules` while keeping `/schedules` route compatibility.
2. Extract reusable list display mode primitives so Recurring can declare entity-specific mode behavior.
3. Use the full-width, flush-top table surface and shell create action.
4. Build the recurring schedule sidebar using the sidebar-as-table-slice contract.
5. Mount recurring detail beside the recurring sidebar by default on desktop.
6. Preserve existing edit, run now, pause/resume, run history, and delete behaviors inside detail.
7. Add tests for route/mode resolution, shell/workspace radio semantics, runtime metadata, sidebar selection, and one-off exclusion.
8. Keep server-side counts, sorting, and filtering aligned with the table and sidebar queries.

This rollout should avoid changing the user's mental model all at once: keep the existing `/schedules` route and detail capabilities, then upgrade the page composition, naming, and list display behavior around them.
