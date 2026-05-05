# Data Model: Executions List and Facet API Support for Column Filters

## Execution List Query

Represents task-list request state.

Fields:
- `source`: existing execution source selector; this story targets Temporal-backed list behavior.
- `pageSize`: bounded page size, preserving existing limits.
- `nextPageToken`: opaque pagination cursor.
- `sort`: supported canonical task field.
- `sortDir`: `asc` or `desc`.
- `stateIn` / `stateNotIn`: canonical lifecycle state filters.
- `targetRuntimeIn` / `targetRuntimeNotIn`: raw runtime identifier filters.
- `targetSkillIn` / `targetSkillNotIn`: raw skill identifier filters.
- `repoIn` / `repoNotIn` / `repoExact` / `repoContains`: repository filters.
- `taskId` / `taskIdContains` / `titleContains`: text filters.
- `scheduledFrom` / `scheduledTo` / `createdFrom` / `createdTo` / `finishedFrom` / `finishedTo`: date range filters.
- `scheduledBlank` / `finishedBlank`: blank/null filters for meaningful nullable date fields.

Validation:
- Include and exclude filters for the same field cannot both be present.
- Text filters are trimmed for query behavior and bounded by configured constants.
- Value-list filters are deduplicated and bounded.
- Date range bounds must be parseable and not reversed.
- Sort fields and directions must be from the supported set.

## Execution List Result

Existing paginated response for authorized task rows.

Fields:
- `items`: serialized execution rows.
- `nextPageToken`: opaque cursor for the next page.
- `count`: filtered result count when available.
- `countMode`: count confidence.
- `uiQueryModel`, `staleState`, `degradedCount`, `refreshedAt`: existing metadata.

## Facet Query

Represents a request for one facet field under the current filter context.

Fields:
- `source`: execution source selector; this story targets Temporal.
- `facet`: one supported facet field, such as status, runtime, skill, repository, or integration.
- `search`: optional bounded text search for facet values.
- `pageSize`: bounded number of facet values.
- `nextPageToken`: optional facet pagination cursor.
- Active list filters: same canonical filters as Execution List Query.

Rules:
- The requested facet's own filter is excluded by default from the query used to discover values.
- All other active filters remain in scope.
- Task and owner authorization constraints always remain in scope.

## Facet Result

Fields:
- `facet`: requested facet field.
- `items`: list of `{ value, label, count }` entries.
- `blankCount`: count for blank values when meaningful.
- `countMode`: count confidence.
- `truncated`: whether more values may exist beyond this response.
- `nextPageToken`: optional cursor for more facet values.
- `source`: `authoritative` or `current_page_fallback` where exposed to the client.

## Filter Validation Error

Structured HTTP 422 detail for invalid filter input.

Fields:
- `code`: stable error code, expected to be `invalid_execution_query` for this route family.
- `message`: safe, operator-readable validation message.
