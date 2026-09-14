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
Enforcement is wired into the production admission choke point:
`TemporalClientAdapter.start_workflow` accepts optional
`retirement_evidence` / `retirement_patch_ids` and raises
`RetirementAdmissionBlocked` while any retiring patch's evidence is
incomplete. `TemporalExecutionService.create_execution` accepts the same
optional pair, fails fast with `RetirementAdmissionBlocked` before any
record is persisted, forwards the evidence to `start_workflow` (which
remains the authoritative enforcement point and never has the hold
swallowed into a projection sync), and defaults to unchanged behavior;
until the operator evidence pipeline supplies evidence, the current
posture is mechanism-wired with manual review. New work always takes the new code path under worker
versioning, so healthy evidence with known consumers still allows
admission while old workers serve only pinned old executions.

The durable per-patch catalog lives beside the audit module
(`PATCH_CATALOG` in `moonmind/workflows/temporal/patch_retirement.py`):
patch id, workflow types, changed command boundary, old/new behavior,
introduction revision, representative fixture, dead-branch vs
retained-history classification, and the explicit deprecate/remove
conditions. Every catalog entry must carry both conditions; the batch
records below mirror the catalog.

## 5. Retirement batches

Each removal is a bounded reviewed change with representative replay tests
before and after. Required history replay must pass against the changed
production code, not merely after deleting the old test cases. Surviving
behavior keeps a minimized production-regression fixture.

### Batch 0 (prior art, recorded retrospectively)

- **Patch:** `run-workflow-nested-propose-tasks`
  (`RUN_WORKFLOW_NESTED_PROPOSE_TASKS_PATCH`, `MoonMindRunWorkflow.run`).
- **Kind:** deprecated marker at the former follow-up proposal stage
  boundary; the proposal feature itself was removed by #3923.
- **Change:** `workflow.patched(...)` to `workflow.deprecate_patch(...)` in
  `5c5058c83` at the exact former stage boundary (introduced in
  `2e5ae2945`).
- **Evidence:** static inventory confirms the single deprecated call site;
  no dedicated replay test was recorded at removal time. Replay coverage
  is inherited from the `deprecate_patch` bridge semantics exercised by
  the batch-1 fixture.
- **Removal condition:** delete the `deprecate_patch` call only when the
  audit reports `safe_to_remove` for this id: no retained history carries
  the marker within retention and retained closed executions are no longer
  reset/replay eligible.

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

### Next batch (cataloged candidates, not yet retired)

These are the next retirement candidates with concrete, checkable
conditions. Neither call site is changed until its deprecate condition
holds on healthy evidence plus a replay test pairing pre/post-change
histories against the changed call site.

- **Patch:** `fetch-profile-snapshots-v1`
  (`RUN_FETCH_PROFILE_SNAPSHOTS_PATCH`,
  `MoonMindRunWorkflow._run_execution_stage`).
  - **Command boundary:** activity `provider_profile.list` per managed
    runtime (new commands when patched).
  - **Old/new behavior:** skip the snapshot fetch vs fetch snapshots so
    plan node profile refs validate against known profiles.
  - **Introduction:** `bdb4ab86` (2026-04-04).
  - **Classification:** retained-history compatibility required.
  - **Deprecate condition:** audit reports `safe_to_deprecate` with an
    admission cutoff newer than every admitted execution that could
    predate 2026-04-04, no pre-patch workers, and a pre/post-history
    replay test passes against the `deprecate_patch` call site.
  - **Remove condition:** audit reports `safe_to_remove`: no retained
    history carries the marker within retention and retained closed
    executions are no longer reset/replay eligible.
- **Patch:** `run-incident-reconstruction-v1`
  (`RUN_INCIDENT_RECONSTRUCTION_PATCH`, three call sites:
  `_record_step_execution_manifest`, `_capture_incident_failure_evidence`,
  `_emit_incident_reconstruction_manifest`).
  - **Command boundary:** payload enrichment only (traceRef setdefault,
    incident manifest/artifact payloads); confirm no command-shape change
    at any of the three call sites before deprecation.
  - **Old/new behavior:** omit incident trace refs and reconstruction
    manifests vs stamp stable trace refs and emit manifests.
  - **Introduction:** `a1bb3c71` (2026-06-24).
  - **Classification:** retained-history compatibility required.
  - **Deprecate condition:** audit reports `safe_to_deprecate` with an
    admission cutoff newer than every admitted execution that could
    predate 2026-06-24, no pre-patch workers, per-call-site boundary
    review confirming payload-only change, and pre/post-history replay
    tests pass against the `deprecate_patch` call sites.
  - **Remove condition:** audit reports `safe_to_remove`: no retained
    history carries the marker within retention and retained closed
    executions are no longer reset/replay eligible.
- **Patch:** `dependency-gate-v1`
  (`DEPENDENCY_GATE_PATCH`,
  `MoonMindRunWorkflow.run`).
  - **Command boundary:** init stage dependency wait
    (activity-polled reconciliation plus signal/timer wait loop) before
    planning.
  - **Old/new behavior:** skip the dependency wait and proceed to planning
    even with dependency ids vs await `_wait_for_dependencies` so
    prerequisite executions resolve first.
  - **Introduction:** `06906426` (2026-04-02).
  - **Classification:** retained-history compatibility required.
  - **Deprecate condition:** audit reports `safe_to_deprecate` with an
    admission cutoff newer than every admitted execution that could
    predate 2026-04-02, no pre-patch workers, and a pre/post-history
    replay test passes against the `deprecate_patch` call site.
  - **Remove condition:** audit reports `safe_to_remove`: no retained
    history carries the marker within retention and retained closed
    executions are no longer reset/replay eligible.
- **Patch:** `jules-bundling-v1`
  (literal id, `MoonMindRunWorkflow._run_execution_stage`).
  - **Command boundary:** execution-stage bundle-manifest artifact-write
    activity plus downstream step-execution command shape (bundled
    representative node vs individual nodes).
  - **Old/new behavior:** execute ordered plan nodes unbundled with no
    bundle manifest artifact vs bundle eligible Jules runtime nodes,
    write a bundle manifest artifact, and execute the representative
    node.
  - **Introduction:** `24bc98ab` (2026-03-27).
  - **Classification:** retained-history compatibility required.
  - **Deprecate condition:** audit reports `safe_to_deprecate` with an
    admission cutoff newer than every admitted execution that could
    predate 2026-03-27, no pre-patch workers, and a pre/post-history
    replay test passes against the `deprecate_patch` call site.
  - **Remove condition:** audit reports `safe_to_remove`: no retained
    history carries the marker within retention and retained closed
    executions are no longer reset/replay eligible.

### Production-history evidence procedure (read-only)

SDK-generated fixtures do not replace retained production histories. To
demonstrate a batch against one production-retained history pair without
mutating anything, the operator runs these read-only commands (never
delete histories or shrink retention to make the audit pass):

1. Find candidate executions carrying the marker:
   `temporal workflow list --namespace default --query "TemporalWorkflowType = 'MoonMind.Run'"`.
2. Fetch one pre-change history (marker recorded) and one post-change
   history:
   `temporal workflow show --namespace default --workflow-id <id> --output json`.
3. Confirm the marker event (`PatchMarkerRecorded` with the patch id) is
   present in the pre-change history and absent in the post-change
   history.
4. Record the reset/replay-policy disposition for retained closed
   executions carrying the marker, then replay both histories against the
   changed call site and preserve the pair as a minimized
   production-regression fixture.

## 6. Non-goals

Automatic destructive cleanup, deleting workflow histories, shrinking
retention to make tests pass, or replacing patches with permanent new
workflow types.
