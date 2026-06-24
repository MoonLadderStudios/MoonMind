Status: rollout note (2026-06-24)

# Run Status Memo Upsert Cutover

## Context

`MoonMind.UserWorkflow._update_search_attributes()` used an ungated
`workflow.upsert_memo(...)` call for status-only metadata
(`waiting_reason`, `attention_required`). That command emits
`WorkflowPropertiesModified` in Temporal history.

Some in-flight histories predate that command and have an activity scheduled at
the same point. Replaying those histories with the ungated memo upsert causes a
nondeterminism failure because the worker expects `WorkflowPropertiesModified`
where the history contains `ActivityTaskScheduled`.

## Cutover Decision

New histories use the patch marker `run-status-memo-upsert-v1` before emitting
the status-only memo update. Histories that do not contain the marker skip that
status-only memo update and continue with the existing search-attribute upsert.
Mission Control still receives owner, state, and title visibility through search
attributes and can fall back to stored projections for step reads.

## In-Flight Compatibility Boundary

A new patch marker cannot distinguish both legacy shapes:

- histories that predate the status-only memo command and therefore need the
  command skipped, and
- histories that already recorded the prior ungated status-only memo command and
  therefore need the command preserved.

The current patch is deliberately targeted at recovering the first shape, which
wedges affected runs with `ModifyWorkflowPropertiesMachine does not handle
HistoryEvent(... ActivityTaskScheduled)`.

If an operator has active histories that already recorded the ungated status-only
memo command but have not completed, those runs must use one of these cutover
paths:

1. Keep them assigned to a worker build that still contains the ungated command
   until they complete, or
2. reset those workflow executions to a safe event before the status-only memo
   command and replay them on a build with `run-status-memo-upsert-v1`, or
3. terminate/restart the affected automation when reset is not acceptable.

Do not route both legacy history shapes to the same unversioned worker build and
expect a single `workflow.patched(...)` branch to infer which command sequence was
recorded.
