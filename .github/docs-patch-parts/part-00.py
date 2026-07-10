from pathlib import Path

ROOT = Path.cwd()
DATE = "2026-07-10"


def load(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def save(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing {label}: {old[:100]!r}")
    return text.replace(old, new, 1)


def replace_section(text: str, start: str, end: str, replacement: str, label: str) -> str:
    try:
        a = text.index(start)
        b = text.index(end, a)
    except ValueError as exc:
        raise RuntimeError(f"missing section markers for {label}") from exc
    return text[:a] + replacement.rstrip() + "\n\n" + text[b:]


COLLECTION_DOC = r'''# Collection Workspace Layout

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
