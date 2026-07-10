text = replace_once(text, "1. The sidebar is owned by the workspace/layout composition layer, not by the detail page body.", "1. The sidebar is owned by the workspace/layout composition layer, not by the detail page body.\n2. It is the first dashboard-content column immediately right of the far-left application rail; it is never inside the detail page's centered/max-width wrapper.", path)
save(path, text)

path = "docs/UI/WorkflowWorkspaceSidebar.md"
text = load(path)
text = text.replace("Last updated: 2026-06-29", f"Last updated: {DATE}", 1)
text = replace_once(text, "- `docs/UI/DashboardSPAArchitecture.md` is canonical", "- `docs/UI/CollectionWorkspaceLayout.md` is canonical for the far-left application rail, shared collection-sidebar component, and shared entity-detail frame.\n- `docs/UI/DashboardSPAArchitecture.md` is canonical", path)
text = text.replace("far left of the dashboard content area", "far-left edge of the dashboard content region, immediately right of the application rail")
text = text.replace("dashboard content area's left edge", "dashboard content region's far-left edge immediately right of the application rail")
text = replace_once(text, "4. The split workspace must not be horizontally centered inside a narrow detail-page container.", "4. The split workspace must not be horizontally centered inside a narrow detail-page container.\n5. There is no decorative or layout gutter between the application rail and the collection sidebar beyond the shared shell divider.\n6. Workflow and Recurring sidebars share the neutral `CollectionSidebar` shell and state components; workflow-specific adapters provide row data and copy.", path)
save(path, text)

# Recurring workspace.
path = "docs/UI/RecurringSchedulesPage.md"
text = load(path)
text = text.replace("Last updated: 2026-07-08", f"Last updated: {DATE}", 1)
text = replace_once(text, "- `docs/UI/WorkflowsListPage.md`", "- `docs/UI/CollectionWorkspaceLayout.md` — canonical far-left application rail, shared collection-sidebar primitive, and Workflow/Recurring detail frame.\n- `docs/UI/WorkflowsListPage.md`", path)
rec_control = r'''## 6. Shell/workspace list display radio control

Recurring uses the shared list-display radio group in the current collection's shell/workspace utility region. It does not create a centered masthead control. The common options are `No list`, `Sidebar list`, and `Full table`, with accessible name `Recurring list display`.

`table` navigates to `/schedules`; `sidebar` keeps or opens Recurring detail with the Recurring sidebar; `hidden` keeps the detail route and removes only that collection sidebar. The control uses the same icons, radio semantics, keyboard behavior, focus treatment, and responsive hiding as Workflows, while Recurring keeps independent preference and selection state.

The Recurring detail page must never show Workflow rows. Routes without declared behavior hide the control, and mobile omits desktop-only modes.

---

'''
text = replace_section(text, "## 6. Masthead list display radio control", "## 7. Default full-table page layout", rec_control, path)
text = replace_once(text, "1. Sidebar rows link to `/schedules/{definitionId}`.", "1. The sidebar uses the shared `CollectionSidebar` shell, header, filter, row metrics, selected/focus states, divider, scrolling, and localized state components.\n2. It is the first content-region column immediately right of the far-left application rail and is never wrapped inside the detail frame or a centered page container.\n3. Sidebar rows link to `/schedules/{definitionId}`.", path)
old_diagram = '''Default desktop layout:\n\n```text\n┌────────────────────────────────────────────────────────────────────┐\n│ Recurring sidebar       │ Schedule detail                          │\n│                         │ Breadcrumb: Recurring / Nightly scan     │\n│                         │ Title + state + actions                  │\n│                         │ Summary cards                            │\n│                         │ Overview / Runs / Configuration / ...    │\n└────────────────────────────────────────────────────────────────────┘\n```'''
