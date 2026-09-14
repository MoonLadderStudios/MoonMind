# Workflow Patch Retirement Convention

Last updated: 2026-09-14

**Document class:** Canonical declarative maintenance convention.
**Status:** Normative
**Owner:** MoonMind Platform.
**Audience:** Backend and workflow authors.

## 1. Purpose

`workflow.patched()` branches in `moonmind/workflows/temporal/workflows/`
are maintainability debt, but a workflow start-date query, no currently OPEN
runs, or two elapsed releases is not enough evidence to delete an old branch
and its marker. This convention defines the only supported retirement
sequence, the evidence each stage requires, and the record every new patch
must carry so the next retirement stays bounded and reviewable.

Reference: MoonLadderStudios/MoonMind#3944. Pinned SDK behavior is validated
against `temporalio >= 1.25, < 1.33` (see `pyproject.toml`), not against the
latest Temporal documentation.

## 2. Supported sequence

Retirement proceeds one stage at a time, per patch id:

1. **Inventory.** The patch id appears in the static inventory produced by
   `moonmind/workflows/temporal/patch_retirement.py` (call site, workflow
   type, branch vs bare marker vs deprecated).
2. **Deprecate.** Replace `workflow.patched(ID)` with
   `workflow.deprecate_patch(ID)` in the exact historical position once the
   audit reports `safe_to_deprecate` (Section 4). New executions stop
   recording the marker; retained pre-change histories still replay. Never
   reorder the call: the marker position is part of the recorded history.
3. **Remove.** Delete the call site only once the audit reports
   `safe_to_remove` (Section 4): healthy evidence sources, no retained
   marker, no continue-as-new chain, no pending old input, and no
   reset/replay exposure under the operating policy.

The following are never equivalent to this sequence and never justify a
removal:

- deleting the old test cases while the production marker still exists;
- deleting workflow histories or shrinking retention to make the audit pass;
- replacing a patch with a permanent new workflow type to avoid versioning;
- reusing a retired patch id for new behavior (patch ids are never reused).

## 3. New-patch record

Every new `workflow.patched()` call site must carry, in a comment at the
constant definition, these four fields:

- **Reason:** why behavior must change for new runs while old histories keep
  the old behavior.
- **Affected boundary:** the workflow type(s) and the command boundary that
  differs (activity, child workflow, timer, memo, search attribute, signal).
- **Evidence/fixture:** the representative history fixture or replay test
  that exercises both the old and the new marker state.
- **Retirement condition:** the explicit, checkable condition for deprecation
  and for later marker removal (e.g. "deprecate once no admitted execution
  can predate <cutoff>; remove once no retained history carries the marker
  within retention and reset/replay support is withdrawn for those
  executions").

No second migration framework and no fixed patch-count limit: each patch
retires on its own evidence, and pressure to delete comes from the audit,
never from a quota.

## 4. Audit evidence and blocking rules

The read-only audit (`python -m
moonmind.workflows.temporal.patch_retirement [--format json|markdown]
[--stage deprecate|remove] [--evidence-json PATH] [--max-entries N]`) is
pure: it never starts, signals, resets, or deletes an execution. Evidence is
supplied explicitly (deployment versions, admission cutoff, running
executions, retained markers, continue-as-new chains, persisted old inputs,
query health). Its verdicts:

- `safe_to_deprecate` / `safe_to_remove`: all evidence sources healthy and no
  consumers observed for the requested stage.
- `requires_compatibility`: mixed deployment versions with pre-patch
  workers, running executions that may predate the patch, retained markers,
  continue-as-new chains, pending old inputs, or (for removal) retained
  closed histories that remain reset/replay eligible. The old
  implementation or supported worker routing stays; admissions route new
  work to the new path only.
- `unknown`: any failed Visibility/history query or stale Visibility. A
  failed query never reports zero consumers.

Before retirement, old workers and admissions must stop creating new
dependencies on the pre-patch path (`retirement_gate` in the audit module:
`route_new_only`, or `block_unknown` while evidence is incomplete).

## 5. Retirement batches

Each removal is a bounded reviewed change with representative replay tests
before and after. Required history replay must pass against the changed
production code, not merely after deleting the old test cases. Surviving
behavior keeps a minimized production-regression fixture.

### Batch 1 (this change)

- **Patch:** `run-conditional-registry-read-v1`
  (`RUN_CONDITIONAL_REGISTRY_READ_PATCH`,
  `MoonMindRunWorkflow._run_execution_stage`).
- **Kind:** bare marker; the return value was discarded and the conditional
  registry read is unconditional, so no behavior changes.
- **Change:** `workflow.patched(...)` to `workflow.deprecate_patch(...)` in
  the exact historical position (after the `jules-bundling-v1` marker,
  before any lazy registry read).
- **Evidence:** static inventory confirms the single call site; pre-change
  histories record the marker and replay through the deprecation bridge;
  post-change histories record nothing. Covered by the replay tests in
  `tests/unit/workflows/temporal/test_patch_retirement.py` (old-marker and
  new-marker histories both replay against the deprecated call site) and by
  the updated marker-position test in
  `tests/unit/workflows/temporal/workflows/test_run_integration.py`.
- **Removal condition:** delete the `deprecate_patch` call only when the
  audit reports `safe_to_remove` for this id: no retained history carries
  the marker within retention and retained closed executions are no longer
  reset/replay eligible.

## 6. Non-goals

Automatic destructive cleanup, deleting workflow histories, shrinking
retention to make tests pass, or replacing patches with permanent new
workflow types.
