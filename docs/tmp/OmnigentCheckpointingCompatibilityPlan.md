# Omnigent checkpointing compatibility plan

Status: Draft implementation note  
Date: 2026-06-30  
Related: `docs/Omnigent/OmnigentAdapter.md`, `docs/Steps/StepExecutionsAndCheckpointing.md`, `moonmind/workflows/temporal/checkpoint_policy.py`

## Problem

MoonMind's Step Execution checkpoint model currently assumes that most checkpointable workspace state is available to the MoonMind sandbox worker. That is valid for local/sandbox-backed execution, but it does not match the v1 Omnigent adapter design:

1. The Omnigent session and live workspace are provider-owned during `integration.omnigent.execute`.
2. MoonMind remains the artifact and orchestration authority, but it should harvest Omnigent streams, snapshots, diagnostics, changed files, diffs, and terminal outputs into MoonMind artifacts at the adapter boundary.
3. Omnigent v1 explicitly does not provide Temporal-checkpointed intra-session progress. Activity retry should reattach to the existing Omnigent session through the idempotency/session mapping instead of recreating the session or trying to restore a local sandbox worktree.
4. The existing `resolve_checkpoint_policy(..., runtime_kind=...)` parameter was present but not used, so Omnigent-shaped executions inherited local defaults such as `git_patch` or `worktree_archive`.

A second design gap remains outside this small change: `ephemeral_workspace_ref.workspaceRef` is ambiguous. The capture activity writes an artifact ref, while `workspace.apply_policy` treats `workspaceRef` as a sandbox filesystem path. That should be split or normalized before relying on ephemeral workspace refs for cross-boundary recovery.

## Compatibility decision for Omnigent v1

Omnigent should use the existing `external_state_ref` checkpoint kind for non-recovery-restoration Step Execution boundaries.

For Omnigent runtime kinds, `resolve_checkpoint_policy` now selects:

```text
workspacePolicy = continue_from_previous_execution
checkpointKind  = external_state_ref
```

This checkpoint lane means "reattach to or reconcile an external provider session," not "restore a MoonMind sandbox checkout." It is compatible with the Omnigent v1 adapter contract because the durable evidence lives in MoonMind artifacts and Omnigent resource IDs remain diagnostic/session correlation values.

Required evidence should be compact and ref-only:

| Boundary | Required evidence |
| --- | --- |
| `before_execution` | `externalStateRef`, `idempotencyKey`, `omnigentSessionId` |
| `after_execution` | `externalStateRef`, `diagnosticsRef`, `omnigentSessionId` |
| Other non-recovery Omnigent boundaries | `externalStateRef`, `omnigentSessionId` |

The `externalStateRef` payload should point at MoonMind-owned artifact evidence for the adapter state, not raw Omnigent payloads. At minimum it should identify the endpoint or endpoint ref, Omnigent session id, agent id when known, first-message digest/state, reattach state, stream/snapshot artifact refs, terminal result refs, and any patch/diff capture refs or patch-unavailable diagnostic.

## What this does not do

This does not make `workspace.apply_policy` restore Omnigent workspaces. Until a provider-specific restore or harvest bridge exists, `workspace.apply_policy` should continue to reject incompatible `external_state_ref` policies rather than silently fabricating local state.

This also does not introduce intra-session Temporal checkpoints. The Omnigent v1 adapter still owns live-session durability and reattach semantics inside the single execute activity.

This change lands the policy mapping at the `resolve_checkpoint_policy` resolution layer only. It does not yet make the lane fire for the documented Omnigent v1 shape (`agentKind=external`, `agentId=omnigent`, with provider fields under `parameters.omnigent`). The Step Execution call sites in `moonmind/workflows/temporal/workflows/run.py` pass `runtime_kind=self._target_runtime`, and `_target_runtime` is sourced only from `targetRuntime` / `runtime.mode` (see `_runtime_visibility_from_parameters`), never from the per-step `agentId`. For a v1 run that selects Omnigent via `agentId` alone, `runtime_kind` is `None`, the Omnigent branch is skipped, and capture falls back to the local `git_patch` / `worktree_archive` defaults. Activating the lane requires threading the external-agent identity into the checkpoint call sites (see follow-up below); the pure-function mapping is intentionally landed first to keep the deterministic workflow change reviewable and replay-safe.

## Follow-up work

1. Thread the per-step external-agent identity into the `resolve_checkpoint_policy` call sites in `moonmind/workflows/temporal/workflows/run.py` so the Omnigent lane actually fires for the documented v1 shape. Today those call sites pass only `self._target_runtime`, which is `None` for `agentId=omnigent` runs that do not also set `targetRuntime` / `runtime.mode`. The step's `agentId` is already recoverable per `logical_step_id` (see `_step_execution_compact_execution_refs` reading the step-ledger `tool.name`); use that signal rather than overloading runtime visibility. Treat this as a deterministic-workflow change: add workflow-boundary coverage and confirm replay/in-flight safety, and land it together with the `externalStateRef` emission in item 2 so the selected `external_state_ref` capture has real evidence to package.
2. Teach `integration.omnigent.execute` to emit an `externalStateRef` checkpoint artifact alongside diagnostics/result artifacts.
3. Ensure activity retry looks up the idempotency/session mapping, reconnects to the Omnigent stream, fetches a snapshot, and reconciles first-message state before waiting for terminal completion.
4. Add an adapter-level terminal harvest step that copies changed files, current files, optional diff/patch, stream mirror, snapshots, and diagnostics into MoonMind artifacts.
5. Split `ephemeral_workspace_ref` into provider-neutral artifact refs versus local sandbox paths, or introduce explicit fields so capture and policy-apply semantics cannot disagree.
6. Add end-to-end coverage for Omnigent reattach, terminal harvest, patch-unavailable diagnostics, and blocked local workspace-restore attempts.
