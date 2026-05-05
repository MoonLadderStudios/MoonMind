# Data Model: Mobile, Accessibility, and Live-Update Stability

This story does not add persistent storage or backend entities. It extends client-side filter state for the existing Tasks List page.

## Client Filter State

### TextFilter

- `contains`: optional trimmed string used for text contains filtering.

Validation rules:
- Empty or whitespace-only values are normalized away before URL/API serialization.
- Values are rendered as text only and are never interpreted as HTML.

### ColumnFilters Additions

- `taskId`: `TextFilter` serialized as `taskIdContains`.
- `title`: `TextFilter` serialized as `titleContains`.

Existing filter state remains unchanged:
- `status`: value include/exclude filter serialized as `stateIn` or `stateNotIn`.
- `repository`: exact text plus value include/exclude filters serialized as `repoExact`, `repoIn`, or `repoNotIn`.
- `targetRuntime`: value include/exclude filter serialized as `targetRuntimeIn` or `targetRuntimeNotIn`.
- `targetSkill`: value include/exclude filter serialized as `targetSkillIn` or `targetSkillNotIn`.
- `scheduledFor`, `createdAt`, `closedAt`: date range filters with blank semantics where supported.

## State Transitions

1. Open desktop filter: copy applied filters into draft filters, store originating filter control, focus first enabled control.
2. Edit desktop filter: update draft filters only.
3. Cancel, Escape, or outside click: discard draft filters, close editor, return focus to originating control.
4. Apply button or Enter: promote draft filters to applied filters, reset pagination, close editor, return focus to originating control.
5. Mobile filter change: apply immediately through the same filter application path and reset pagination.
6. Open editor with live updates enabled: execution-list polling interval is disabled until the editor closes.
