# Research: Column Filter Popovers, Chips, and Selection Semantics

## Story Classification

Decision: `MM-588` is a single-story runtime UI feature.
Evidence: `specs/301-column-filter-popovers/spec.md` preserves one user story and the Jira preset brief names one operator goal.
Rationale: The source design covers a broad desired Tasks List state, but Jira selected only popover filtering, selection semantics, chips, and reset behavior.
Alternatives considered: Running MoonSpec breakdown was rejected because the Jira brief already narrows the source design to one independently testable story.
Test implications: Unit and route-boundary tests are required before implementation.

## Existing Tasks List Foundation

Decision: Build on the `MM-587` implementation rather than replacing the Tasks List page.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` has `TABLE_COLUMNS`, compound header buttons, `renderFilterPopover`, status/repository/runtime controls, active chips, and `scope=tasks` API calls. `frontend/src/entrypoints/tasks-list.test.tsx` covers header sort/filter separation, legacy scope normalization, runtime filter options, and chips.
Rationale: The current code already owns the correct entrypoint and basic controls; MM-588 is an extension of filter semantics.
Alternatives considered: Creating a new filtering module first was rejected because the current behavior is localized and testable in one entrypoint.
Test implications: Existing tests should remain, with new tests added around staged state and canonical semantics.

## Staged Popover State

Decision: Add applied filter state plus popover-local draft state; only Apply/remove/clear mutate applied state and query state.
Evidence: Current controls call `setTemporalState`, `setRepository`, and `setTargetRuntime` directly on change.
Rationale: Staged editing is the central MM-588 gap and prevents live updates or accidental menu changes from changing results.
Alternatives considered: Debounced immediate application was rejected because the brief requires Apply.
Test implications: UI tests must prove row fetches and URL state do not change until Apply, and Cancel/Escape/outside click discard draft changes.

## Include/Exclude Model

Decision: Represent value-list filters as `{ mode: include|exclude, values: string[], includeBlank?: boolean, excludeBlank?: boolean }` per field.
Evidence: `docs/UI/TasksListPage.md` section 10 requires AND across columns, OR within one column, and exclude mode when deselecting unwanted values from all.
Rationale: This model expresses both `Status: completed OR failed` and `Status: not canceled`, while allowing new live-update values through exclude mode.
Alternatives considered: Single `state` and `repo` strings were rejected because they cannot express MM-588 selection semantics.
Test implications: UI and API tests must cover include arrays and `stateNotIn=canceled`.

## Canonical URL/API Encoding

Decision: Use canonical query names for new UI changes: `stateIn`, `stateNotIn`, `targetRuntimeIn`, `targetRuntimeNotIn`, `targetSkillIn`, `targetSkillNotIn`, `repoIn`, `repoNotIn`, date bound params, and blank flags. Preserve legacy `state` and `repo` only as load-time mappings.
Evidence: `docs/UI/TasksListPage.md` section 12.2 recommends `stateNotIn=canceled&targetRuntimeIn=codex_cli,claude_code`.
Rationale: Canonical names make include/exclude semantics explicit and avoid overloading legacy single-value params.
Alternatives considered: Reusing comma-separated `state` was rejected because it hides include/exclude meaning.
Test implications: UI tests verify canonical URL rewrites after new UI changes; API route tests verify task-scoped query construction.

## Skill And Date Filters

Decision: Add Skill as a value-list filter from row task-skill metadata and add Scheduled/Created/Finished date filters with inclusive bounds; Scheduled and Finished support blank filtering.
Evidence: `spec.md` FR-010 through FR-013 and `docs/UI/TasksListPage.md` sections 7 and 9.5.
Rationale: These fields are part of the MM-588 acceptance scope and are not present in the simple current filters.
Alternatives considered: Deferring date filters was rejected because the Jira brief explicitly includes date bounds and blanks.
Test implications: UI tests cover Skill labels, date bounds, Scheduled/Finished blanks, and Created without blanks.

## Task-Scope Safety

Decision: Preserve the current task-scope query foundation and extend filters inside that scope only.
Evidence: `api_service/api/routers/executions.py` normalizes scope through `_normalize_temporal_list_scope`; `tasks-list.tsx` always sends `scope=tasks`; tests assert unsafe workflow scope parameters are ignored.
Rationale: The source non-goals forbid ordinary system workflow browsing through filters.
Alternatives considered: Exposing generic Temporal visibility fields was rejected by DESIGN-REQ-027.
Test implications: Route tests must assert canonical filters remain combined with `WorkflowType="MoonMind.Run"`/task scope and owner constraints.

## Requirement Status Summary

Decision: Most MM-588 behavior is partial or missing, with task-scope safety and legacy load mappings already verified.
Evidence: `specs/301-column-filter-popovers/plan.md` Requirement Status table.
Rationale: Current code is a useful base but does not satisfy staged popover editing, include/exclude, blank handling, date filters, Skill filters, individual chip removal, or canonical URL/API encoding.
Alternatives considered: Treating current simple filters as complete was rejected because they apply immediately and only express one included value.
Test implications: Generate tasks in TDD order for missing and partial requirements.
