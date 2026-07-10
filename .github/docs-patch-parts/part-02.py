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
'''

# Roadmap milestone.
path = "docs/MoonMindRoadmap.md"
text = load(path)
text = replace_once(text, "Last updated: 2026-07-09", f"Last updated: {DATE}", path)
milestone = r'''## Milestone 1 — Dashboard Navigation, Shared Sidebars & Detail Frames 🚧

**Goal:** Establish one far-left application rail, one reusable collection-sidebar system for Workflows, Recurring, and Skills, and one shared detail-frame language for Workflow and recurring schedule detail pages.

**Why it matters:** Operators should experience MoonMind as one console. Top-level navigation must stay at the viewport edge; local collection navigation must start at the content edge; and related detail pages must reuse recognizable structure instead of inheriting centered wrappers or page-specific shells.

### Remaining work

- [ ] **1.0 Declarative design first** — make `docs/UI/CollectionWorkspaceLayout.md` canonical for far-left shell geometry, shared collection sidebars, and the Workflow/Recurring entity-detail frame; reconcile `docs/UI/DashboardSPAArchitecture.md`, `docs/UI/DashboardDesignSystem.md`, `docs/UI/WorkflowListDisplayModes.md`, `docs/UI/WorkflowWorkspaceSidebar.md`, `docs/UI/RecurringSchedulesPage.md`, `docs/UI/SkillsTabDesign.md`, `docs/UI/WorkflowDetailsPage.md`, and `docs/UI/RecurringScheduleDetailsPage.md` before implementation.
- [ ] **1.1 Far-left application shell** — replace centered/header-only primary navigation with a responsive application rail at the viewport's far-left edge, containing Workflows, Create, Recurring, Skills, RAG/Manifests, Omnigent Agents, Omnigent Policies, Remediation, Artifacts/Observability, and Settings.
- [ ] **1.2 Reusable collection workspace** — implement a parent layout whose optional collection sidebar is the first column immediately right of the application rail and whose primary pane fills the remaining width; never wrap the split workspace in a centered/max-width page container.
- [ ] **1.3 Shared sidebar component system** — provide common header, filter, row metrics, active/focus states, pinned-current row, divider, scrolling, loading/empty/error states, accessibility, and responsive behavior with entity-specific adapters.
- [ ] **1.4 Required collection sidebars** — use the shared primitive for Workflow detail/Create, Recurring detail, and Skills preview/create; Recurring lists schedules only, Skills lists skills only, and desktop Skills keeps its sidebar present.
- [ ] **1.5 Shared Workflow/Recurring detail frame** — reuse breadcrumb/header, status and action placement, summary/facts strip, tabs/sections, main slab, optional facts rail, and loading/error/responsive patterns while retaining entity-specific content.
- [ ] **1.6 Reusable list display modes** — generalize Workflows/Recurring `hidden`, `sidebar`, and `table` behavior with per-collection preferences and route-owned coercion; place controls in the shell/workspace utility area rather than a centered masthead.
- [ ] **1.7 Full-page list and route inventory** — classify each major page and harden full-page list routes for workflows, recurring schedules, skills, manifests/RAG sources, Omnigent agents/policies, remediations, and artifacts/reports.
- [ ] **1.8 Preferences, responsiveness, and accessibility** — prevent preference cross-talk, preserve deep links and selection, provide deterministic focus, and collapse to an accessible drawer or list-to-detail flow where three columns do not fit.
