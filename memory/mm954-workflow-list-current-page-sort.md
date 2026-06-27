---
name: mm954-workflow-list-current-page-sort
description: Why the workflow-list (frontend/src/entrypoints/workflow-list.tsx) sorting is intentionally current-page-only, not global server-side
metadata:
  type: project
---

MM-954 was implemented with the issue's **fallback** (current-page-only) path, not the preferred server-side path, even though `/api/executions` already accepts/validates `sort`/`sortDir` and applies `ORDER BY` (see `api_service/api/routers/executions.py`, `_EXECUTION_SORT_FIELDS`).

**Why:** The workflow-list's primary sortable column is the workflow **title**, but titles are indexed only as a Temporal `KeywordList` (`mm_title`) for word-membership filtering. Temporal SQL `ORDER BY` does not support `KeywordList`, the custom single-Keyword budget (10) is full (comment in `moonmind/workflows/temporal/workflows/run.py` ~line 13880), and adding a sortable title attribute is "New search indexing" — explicitly **out of scope** in MM-954. A mix of some columns sorted globally and title sorted page-only would be the misleading contract the issue set out to remove.

**How to apply:** The frontend deliberately does NOT send `sort`/`sortDir` to the API, does NOT persist them in the URL, and labels sorting as current-page-only (footer note `CURRENT_PAGE_SORT_NOTICE`, header tooltip, and screen-reader hints). The backend `sort`/`sortDir` capability remains a valid API surface for other consumers — left intact, not removed. If global sorting is ever needed, it requires adding a sortable single-Keyword title search attribute first.
