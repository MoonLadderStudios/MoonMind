new_diagram = '''Default desktop layout:\n\n```text\n┌──────────────────┬──────────────────────────┬──────────────────────────────────────────┐\n│ Application rail │ Recurring sidebar        │ Shared entity-detail frame               │\n│ viewport far-left│ content-region far-left  │ breadcrumb, title/state/actions          │\n│                  │                          │ summary/facts, tabs, main, optional rail │\n└──────────────────┴──────────────────────────┴──────────────────────────────────────────┘\n```\n\nThe Recurring sidebar and detail frame are workspace siblings. The full composition is fluid and must not be centered inside a narrower page container.'''
text = replace_once(text, old_diagram, new_diagram, path)
text = replace_once(text, "The Recurring detail page is a schedule-definition control surface. It should reuse the Workflow detail page composition where practical", "The Recurring detail page is a schedule-definition control surface. It must use the shared `EntityDetailFrame` and reuse the Workflow detail composition", path)
save(path, text)

# Skills workspace.
path = "docs/UI/SkillsTabDesign.md"
text = load(path)
text = replace_once(text, "- Dedicated dashboard entry point for skills (route `/skills`).", "- Dedicated dashboard entry point for skills (route `/skills`) in the shared far-left application rail.\n- Reuse the geometry and neutral primitives in [`CollectionWorkspaceLayout.md`](CollectionWorkspaceLayout.md).", path)
text = replace_once(text, "- Top-level nav pill **Skills** in `.route-nav` → `/skills`, consistent with `/workflows`, `/workflows/new`, etc.", "- Top-level **Skills** destination in the shared application rail → `/skills`, using the same icon/label, active, hover, focus, tooltip, and responsive behavior as Workflows and Recurring.", path)
text = replace_once(text, "- `/skills` uses the same top-level split-workspace composition as other dashboard sidebar surfaces: a left sidebar sibling and a right primary pane.", "- `/skills` uses the shared `CollectionWorkspace`: the Skills sidebar is the first content-region column immediately right of the far-left application rail, and the primary pane is its sibling.\n- The Skills sidebar is always present for desktop preview and create states; it is not optional Workflow list-display state.", path)
text = replace_once(text, "- The sidebar starts at the dashboard content area's left edge", "- The sidebar starts at the dashboard content region's far-left edge and is never inside a centered/max-width page wrapper", path)
text = text.replace("the shared pinned-row behavior may show **Current skill**", "the shared pinned-row behavior must show **Current skill**")
text = replace_once(text, "- At breakpoints where the desktop sidebar cannot remain usable", "- Below the shared desktop workspace breakpoint (initially `lg`), where the sidebar and primary pane cannot both remain usable", path)
save(path, text)

# Detail page contracts.
path = "docs/UI/WorkflowDetailsPage.md"
text = load(path)
text = replace_once(text, "Addendum: `docs/UI/WorkflowWorkspaceSidebar.md` defines", "Addendum: `docs/UI/CollectionWorkspaceLayout.md` defines the shared entity-detail frame and far-left collection geometry. `docs/UI/WorkflowWorkspaceSidebar.md` defines", path)
text = replace_once(text, "Desktop layout uses a main content column and an optional right rail. Mobile layout stacks all sections vertically while preserving the header and primary actions near the top of the page.", "Desktop Workflow detail renders inside the shared `EntityDetailFrame`: breadcrumb; title/subtitle/status; primary and overflow actions; summary/facts strip; tabs/sections; main evidence slab; and optional right facts rail. The frame is the primary pane sibling of the far-left Workflow sidebar; it never owns that sidebar and the pair is never centered inside a max-width wrapper. Recurring schedule detail uses the same structural, spacing, status, action, tab, facts-rail, loading, error, and responsive primitives with a schedule adapter. Mobile stacks these regions and removes the desktop collection sidebar.", path)
save(path, text)

path = "docs/UI/RecurringScheduleDetailsPage.md"
text = load(path)
text = text.replace("**Last Updated:** 2026-06-26", f"**Last Updated:** {DATE}", 1)
text = replace_once(text, "- `docs/Temporal/WorkflowSchedulingGuide.md`", "- `docs/UI/CollectionWorkspaceLayout.md` — shared far-left workspace geometry and `EntityDetailFrame`.\n- `docs/UI/WorkflowDetailsPage.md` — entity-detail content conventions shared with schedules.\n- `docs/Temporal/WorkflowSchedulingGuide.md`", path)
