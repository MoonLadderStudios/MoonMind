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

