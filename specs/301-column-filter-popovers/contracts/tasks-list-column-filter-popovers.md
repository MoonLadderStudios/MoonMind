# Contract: Tasks List Column Filter Popovers

## UI Contract

Each filterable column exposes a filter button. Activating it opens a popover whose edits are local draft state until Apply.

Required popover behaviors:

- Apply commits draft filter state, closes the popover, resets pagination, updates the URL, and triggers the task-scoped list request.
- Cancel closes the popover without changing applied filter state.
- Escape closes the popover without changing applied filter state.
- Outside click closes the popover without changing applied filter state.
- Clear removes the column filter consistently and resets pagination.
- Value labels render as text.

Required fields:

- Status: value-list include/exclude with canonical lifecycle order.
- Runtime: value-list include/exclude storing raw identifiers and displaying human-readable labels.
- Skill: value-list include/exclude using stable skill values from task data.
- Repository: value-list include/exclude plus legacy exact text behavior.
- Scheduled: inclusive date bounds plus blank filtering.
- Created: inclusive date bounds, no blank filtering.
- Finished: inclusive date bounds plus blank filtering.

Active chip behaviors:

- Every applied filter has a chip.
- Clicking the chip body opens the matching popover with the applied state loaded.
- Clicking the chip remove action clears only that filter and resets pagination.
- Clear filters clears all filters and restores the default task-run view.

## URL Contract

Legacy load-time mappings:

```text
state=<value> -> Status include filter for one value
repo=<value> -> Repository exact include filter for one value
targetRuntime=<value> -> Runtime include filter for one value
```

Canonical post-edit encoding:

```text
stateIn=completed,failed
stateNotIn=canceled
targetRuntimeIn=codex_cli,claude_code
targetRuntimeNotIn=jules
targetSkillIn=moonspec-implement
targetSkillNotIn=fix-ci
repoIn=MoonLadderStudios/MoonMind
repoNotIn=owner/archived
repoExact=owner/repo
scheduledFrom=2026-05-01
scheduledTo=2026-05-05
scheduledBlank=include
createdFrom=2026-05-01
createdTo=2026-05-05
finishedFrom=2026-05-01
finishedTo=2026-05-05
finishedBlank=include
```

Rules:

- `limit`, `sort`, and `sortDir` remain outside filter state.
- Applying, removing, or clearing filters removes `nextPageToken`.
- Unsupported workflow-scope URL parameters remain fail-safe and must not widen ordinary `/tasks/list` visibility.

## API Contract

`GET /api/executions?source=temporal&scope=tasks` accepts canonical filter parameters:

- `stateIn`, `stateNotIn`
- `targetRuntimeIn`, `targetRuntimeNotIn`
- `targetSkillIn`, `targetSkillNotIn`
- `repoIn`, `repoNotIn`, `repoExact`
- date bounds and blank flags where supported by the task-list source

The route must combine canonical filters with the ordinary task-scope query and user ownership constraints. Unsupported or unavailable field filters must fail safe with validation or be ignored only when the UI does not emit them.
