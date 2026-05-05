# Contract: Tasks List Filter Behavior

## Scope

This contract covers the browser-visible Tasks List behavior for `MM-591`. The page remains bounded to ordinary task executions.

## Request Contract

The Tasks List page requests:

```text
GET /api/executions?source=temporal&pageSize=<size>&scope=tasks
```

Optional filter parameters used by this story:

| Parameter | Meaning |
| --- | --- |
| `taskIdContains` | Task ID text contains filter |
| `titleContains` | Task title text contains filter |
| `stateIn` / `stateNotIn` | Status include/exclude values |
| `repoExact` | Exact repository text filter |
| `targetRuntimeIn` / `targetRuntimeNotIn` | Runtime include/exclude values |
| `targetSkillIn` / `targetSkillNotIn` | Skill include/exclude values |
| `scheduledFrom` / `scheduledTo` / `scheduledBlank` | Scheduled date filter |
| `createdFrom` / `createdTo` | Created date filter |
| `finishedFrom` / `finishedTo` / `finishedBlank` | Finished date filter |

Rules:
- Filter changes reset `nextPageToken`.
- Empty text filters are omitted.
- The page always sends `scope=tasks` for ordinary Tasks List requests.

## Interaction Contract

- Sort header buttons and filter buttons are separate keyboard targets.
- Filter buttons expose `aria-expanded` and accessible names that include active filter state.
- Opening a desktop filter dialog moves focus into the first enabled dialog control.
- Cancel, Escape, outside click, Apply, and Enter-close paths return focus to the originating filter control.
- Escape and outside click discard staged changes.
- Enter applies staged changes when the focused target is not a multiline text input.
- Live polling is paused while a desktop filter editor is open.

## Mobile Contract

Mobile task-card users can reach these filters without top dropdowns:

- ID
- Runtime
- Skill
- Repository
- Status
- Title
- Scheduled
- Created
- Finished

Mobile filters use the same task-scoped request contract as desktop filters.
