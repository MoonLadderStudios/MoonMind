# Quickstart: Author Governed Tool Steps

## Test-First Flow

1. Add or update frontend tests in `frontend/src/entrypoints/task-create.test.tsx` before production code.
2. Mock `/api/mcp/tools` with Jira and GitHub Tool metadata.
3. Render the Create page, switch the primary step to `Tool`, and verify grouped Tool choices appear.
4. Search for a Jira transition Tool and select it.
5. Enter prerequisite input such as `issueKey: MM-576` and mock trusted `jira.get_transitions` results.
6. Verify target-status options appear and selecting one produces the expected Tool input payload without guessing transition ids.
7. Verify unknown Tool ids, missing required schema fields, dynamic option lookup failure, and arbitrary shell-like input block submission before `/api/executions`.
8. Implement the Create-page metadata loading, grouping/search, schema guidance, dynamic Jira option lookup, and fail-closed validation.

## Commands

Focused frontend validation:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Full unit validation before final verification:

```bash
./tools/test_unit.sh
```

Hermetic integration validation is not required unless backend route behavior changes beyond the existing trusted MCP tool metadata/call contracts. If backend contracts change, run the relevant targeted pytest first and then the full unit suite.

## Story Verification

The story is complete when a task author can discover a governed Tool through grouping/search, configure schema-shaped inputs, use trusted Jira dynamic options for target statuses, submit a typed Tool step, and see invalid or untrusted Tool submissions rejected before execution. Verification must preserve `MM-576` and the original Jira preset brief in the final report.
