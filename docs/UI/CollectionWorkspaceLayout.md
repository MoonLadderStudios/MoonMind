# Collection Workspace Layout

Status: **Target Architecture**  
Owners: MoonMind Engineering  
Last updated: 2026-07-10  
Canonical for: dashboard application rail placement, reusable collection sidebars, far-left workspace geometry, and the shared Workflow/Recurring entity-detail frame

**Implementation tracking:** implementation checklists belong in GitHub issues or `docs/tmp/`. This document defines durable desired state.

## 1. Purpose

Define one application-wide desktop composition for MoonMind collection surfaces. Workflows, Recurring, and Skills must not invent separate left-navigation systems, and detail routes must not mount a sidebar inside a centered page wrapper.

## 2. Non-negotiable geometry

The desktop shell has three ordered regions:

```text
┌──────────────────┬──────────────────────────┬───────────────────────────────────────────┐
│ Application rail │ Collection sidebar       │ Primary page or entity-detail pane        │
│ viewport far-left│ content-region far-left  │ fluid workspace; readable widths inside  │
└──────────────────┴──────────────────────────┴───────────────────────────────────────────┘
```

1. The **application rail** is the first visual column at the far-left edge of the viewport. It contains the MoonMind brand and top-level destinations, including **Workflows**, **Create**, **Recurring**, and **Skills**.
2. A **collection sidebar**, when the route owns one, is the first child of the dashboard content region immediately to the right of the application rail.
3. The primary pane begins immediately after the collection sidebar and consumes the remaining width.
4. Neither left-side region may be placed inside a centered, constrained, or `max-width` page container.
5. A detail pane may constrain prose, forms, logs, or evidence *inside itself*. Those constraints must never move the collection sidebar away from the content edge or create a large empty left margin.
6. The application rail remains present across desktop dashboard routes. Collection sidebars are route-owned and may be absent only where the route contract explicitly calls for a full-table or focused single-pane presentation.

On tablet and mobile, the shell may collapse into a drawer or list-to-detail flow. Hidden desktop rails and controls must leave the accessibility tree.

## 3. Application rail contract

The application rail is React-owned by `DashboardShell` and uses router-native links. Its shared anatomy is:

- brand/home control;
- grouped top-level route links with icons and text labels;
- unmistakable active-route treatment;
- flexible spacer;
- environment, version, account, and settings utilities;
- compact/collapsed state when supported.

The rail uses the same tokens, border, focus ring, hover brightening, icon sizing, tooltip behavior, and responsive collapse behavior everywhere. Primary navigation must not be recreated as a centered pill row inside the page masthead.

## 4. Shared collection sidebar primitive

Workflows, Recurring, and Skills use one entity-neutral `CollectionSidebar`/`CollectionWorkspace` primitive with adapters for data and copy. Every adapter supplies:

- region and table-slice labels;
- column header;
- list query and query state;
- row identity, primary text, optional compact metadata, and active state;
- canonical navigation or selection callback;
- filter labels and behavior;
- pinned-current-row behavior;
- empty, loading, error, and retry copy;
- optional page action slot.

Shared anatomy and behavior:

1. labelled navigation landmark;
2. table-compatible header row;
3. optional compact filter/search control;
4. optional pinned current row;
5. independently scrolling rows;
6. active row with visible, non-color-only selection and `aria-current="page"` for route links;
7. localized loading, empty, error, and retry states;
8. right-edge divider and resize/collapse affordance only when the common primitive supports them.

The sidebar is a collapsed slice of its collection list, not a card stack or unrelated menu. All adapters share width, row metrics, header height, padding, border, focus, hover, selected, scrolling, and state components. Entity-specific data remains in adapters; workflow rows must not leak into Recurring or Skills.

### 4.1 Required desktop adapters

| Route family | Sidebar header | Required desktop behavior |
| --- | --- | --- |
| Workflows detail and participating Create surfaces | `Workflow` | Workflow rows and route-derived selection; visible by default on direct detail visits unless the persisted Workflow preference is `hidden`. |
| Recurring detail | `Recurring` | Recurring schedule rows only; visible by default on `/schedules/{definitionId}` unless the persisted Recurring preference is `hidden`. |
| Skills preview and create | `Skill` | Skill rows; remains present for desktop preview and create states. |

Workflows and Recurring may additionally expose `hidden`, `sidebar`, and `table` modes. Their mode controls live in the shell/workspace utility area associated with the current collection, not in a centered masthead. Skills does not inherit Workflow mode state; it owns a persistent desktop sidebar and a mobile list-to-detail fallback.

## 5. Shared entity-detail frame

Workflow detail and Recurring schedule detail use one `EntityDetailFrame` composition. The entity adapter supplies labels, status semantics, actions, facts, sections, and data; the frame supplies common structure and styling:

1. breadcrumb/back context;
2. title, subtitle, identity, and status cluster;
3. primary and overflow action placement;
4. compact summary/facts strip;
5. tab or section navigation;
6. main content/evidence slab;
7. optional right facts rail;
8. localized loading, not-found, permission, and error states;
9. focus restoration, sticky behavior, and responsive stacking.

Workflow-specific content includes execution progress, steps, logs, artifacts, remediation, and recovery actions. Recurring-specific content includes cadence, next run, schedule policy, configuration, and spawned-run history. These differences are adapters, not reasons for a second page shell.

Shared detail elements must use the same typography, spacing, surface hierarchy, status-chip family, button placement, tab treatment, facts-rail geometry, error presentation, and responsive breakpoints. Destructive actions remain visually separated and capability-gated.

## 6. Ownership and composition

`DashboardShell` owns the application rail and global providers. A route-family workspace owns its collection sidebar and primary pane as siblings. The detail component owns only the content inside the primary pane.

```text
DashboardShell
├── ApplicationRail
└── DashboardContent
    └── CollectionWorkspace
        ├── CollectionSidebar
        └── PrimaryPane
            └── EntityDetailFrame | list page | create/preview page
```

Forbidden compositions include:

- `CenteredPageContainer > CollectionSidebar + Detail`;
- `EntityDetailFrame > CollectionSidebar`;
- page-specific copies of sidebar CSS or state components;
- a second top-level navigation system on Recurring or Skills;
- a large decorative left gutter before either rail.

## 7. Tokens and component seams

Shared implementations should converge on neutral names such as:

```css
--mm-app-rail-width
--mm-collection-sidebar-width
--mm-collection-header-height
--mm-collection-row-height
--mm-workspace-divider
--mm-detail-header-gap
--mm-detail-facts-rail-width

.dashboard-shell
.application-rail
.dashboard-content
.collection-workspace
.collection-sidebar
.collection-sidebar__header
.collection-sidebar__row
.collection-sidebar__row--active
.entity-detail-frame
.entity-detail-frame__header
.entity-detail-frame__summary
.entity-detail-frame__tabs
.entity-detail-frame__main
.entity-detail-frame__facts
```

Legacy workflow-prefixed classes may remain temporarily, but new page adapters must not fork them. Tokens and neutral primitives are canonical.

## 8. Resilience and accessibility

Sidebar and detail requests are independent: a sidebar failure must not blank an authorized detail, and a detail failure must not erase a loaded sidebar. Refetches preserve selection and focus. Remembered selections are reauthorized before use.

All rails, filters, rows, mode controls, tabs, and actions are keyboard reachable with visible focus. Landmarks have entity-specific accessible names. Active state is not color-only. Route changes restore focus deterministically. Reduced-motion settings suppress large layout transitions.

## 9. Acceptance tests

Representative frontend tests must prove:

1. the application rail is the viewport's far-left desktop column;
2. Workflows, Recurring, and Skills links appear in the same rail with common active/focus behavior;
3. each collection sidebar is the first content-region column and is not inside a centered/max-width wrapper;
4. Workflow, Recurring, and Skills adapters share the same sidebar shell and state components;
5. direct Workflow and Recurring detail routes show the correct entity sidebar by default on desktop;
6. Skills preview and create states keep the Skills sidebar present on desktop;
7. Workflow and Recurring detail pages render the shared detail-frame regions with entity-specific content;
8. sidebar and detail failures remain localized;
9. mobile removes non-rendered desktop controls from the accessibility tree;
10. screenshots/layout assertions catch a reintroduced large left margin or centered split workspace.
