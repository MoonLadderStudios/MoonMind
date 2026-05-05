# Data Model: Shareable Filter URL Compatibility

## Task List URL State

Represents shareable client-visible query state for `/tasks/list`.

Fields:
- `limit`: page size.
- `nextPageToken`: current pagination cursor, omitted after filter or page-size changes.
- `sort`: active sort field when non-default.
- `sortDir`: active sort direction when non-default.
- `filters`: canonical column filter params plus accepted legacy inputs.

Validation:
- Empty list values are omitted.
- Repeated canonical list params are equivalent to comma-encoded values.
- Include and exclude lists for the same field cannot both be non-empty.
- Unsupported workflow scope state cannot widen the normal task list beyond task-run rows.

## Column Filter

Represents one field-specific filter.

Fields:
- `mode`: include or exclude for value-list filters.
- `values`: raw canonical values used in URL and API state.
- `exactText`: exact repository text when using the repository exact filter.
- `blank`: blank include/exclude mode where the field supports blank filtering.
- `from` / `to`: date bounds for date filters.

Validation:
- Values are trimmed and de-duplicated.
- UI labels are display-only and are not serialized as canonical values.

## Execution List Query

Represents the FastAPI `/api/executions` request after URL/API parsing.

Fields:
- task scope and optional owner filters.
- canonical include/exclude filters for state, repo, runtime, and skill.
- date/blank filters.
- pagination token and page size.

Validation:
- Non-admin users cannot request other owners.
- Normal task-list scope remains bounded to task executions.
- Contradictory include/exclude filter pairs produce a `422` validation error.
