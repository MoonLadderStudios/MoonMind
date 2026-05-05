# Tasks List Empty/Error Rollout Contract

Traceability: `MM-592`; FR-001 through FR-013; DESIGN-REQ-006, DESIGN-REQ-024, DESIGN-REQ-026, DESIGN-REQ-027, DESIGN-REQ-028.

## UI Contract

The Tasks List page must expose these rollout recovery states:

| State | Required behavior |
| --- | --- |
| Loading | Show `Loading tasks...`. |
| List API failure | Show a visible error notice. Prefer structured API detail when provided. |
| Empty first page | Show `No tasks found for the current filters.`. |
| Empty first page with active filters | Keep an enabled `Clear filters` recovery action. |
| Empty later page | Keep previous-page navigation enabled. |
| Facet failure | Show an inline fallback notice inside the filter editor without hiding loaded rows. |
| Invalid filters | Show structured validation messages and keep filter state editable or clearable. |
| Final parity | Do not render old top Scope, Workflow Type, Status, Entry, or Repository controls. |

## Error Detail Contract

For non-OK list responses, the UI should derive a sanitized display message in this order:

1. `detail.message` when the JSON body is an object with a string `message`.
2. `detail` when the JSON body is a string.
3. `message` when the JSON body is an object with a string `message`.
4. `response.statusText` when no structured message exists.
5. A generic list-fetch failure string as a final fallback.

The UI must not print auth headers, cookies, tokens, or raw stack traces.

## Non-Goal Guardrails

The final rollout must not introduce:
- spreadsheet-style editing;
- arbitrary pivot tables;
- multi-column sort;
- user-authored raw Temporal Visibility SQL;
- direct browser calls to Temporal;
- saved filter views;
- pagination replacement;
- Live updates removal;
- ordinary system workflow browsing from `/tasks/list`.
