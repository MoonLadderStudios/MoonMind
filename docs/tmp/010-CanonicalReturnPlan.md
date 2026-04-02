Here’s a phased implementation plan for canonicalizing agent activity return shapes now that the declarative docs are in place.

The canonical target contracts already exist in `moonmind/schemas/agent_runtime_models.py` . The remaining workflow-side coercion still lives primarily in `moonmind/workflows/temporal/workflows/agent_run.py` and `moonmind/workflows/temporal/workflows/run.py`  , while the external adapter base already has canonical builders for handles, statuses, and results .

## Goal

Move all provider-specific and runtime-specific normalization to the adapter/activity boundary so that:

* external and managed runtime activities return canonical contracts directly
* workflow code stops repairing provider-shaped payloads
* remaining compatibility glue is either deleted or reduced to one narrow execution-loop adapter

## Success criteria

By the end of this work:

* `integration.<provider>.start` returns canonical `AgentRunHandle`
* `integration.<provider>.status` returns canonical `AgentRunStatus`
* `integration.<provider>.fetch_result` returns canonical `AgentRunResult`
* `integration.<provider>.cancel` returns canonical `AgentRunStatus`
* `agent_runtime.status` returns canonical `AgentRunStatus`
* `agent_runtime.fetch_result` returns canonical `AgentRunResult`
* `MoonMind.AgentRun` no longer depends on:

  * `_coerce_external_status_payload`
  * `_coerce_external_start_status`
  * `_coerce_managed_status_payload`
* `MoonMind.Run` no longer needs provider/runtime-specific result repair logic

---

## Phase 0 — Lock scope and compatibility strategy

### Objectives

Freeze the implementation boundary and decide what compatibility code must stay temporarily for replay safety.

### Tasks

* [x] Treat the updated declarative docs as normative for this implementation pass.
* [x] Inventory all remaining workflow-side normalization points:

  * [x] `MoonMind.AgentRun._coerce_external_status_payload`
  * [x] `MoonMind.AgentRun._coerce_external_start_status`
  * [x] `MoonMind.AgentRun._coerce_managed_status_payload`
  * [x] `MoonMind.AgentRun` special-case handling for non-canonical external start dicts
  * [x] `MoonMind.Run._map_agent_run_result`
* [x] Inventory all provider/runtime activity handlers that currently emit non-canonical or mixed-shape payloads.
* [x] Decide in-flight cutover strategy (per Temporal Compatibility Policy):

  * [x] determine if `workflow.patched` is required for in-flight executions crossing this deployment
  * [x] do NOT introduce translation aliases or soft wrap-arounds; superseded patterns must be entirely removed from active logic or guarded by explicit Temporal patching constraints.
* [x] Define a single implementation rule:

  * [x] all new histories and new activity executions must emit canonical Pydantic-compatible shapes only

### Deliverables

* [x] implementation inventory
* [x] compatibility matrix
* [x] explicit “new-history only” vs “replay compatibility” decisions

> **Phase 0 Deliverables completed**: See [`specs/118-canonical-return-phase0/plan.md`](specs/118-canonical-return-phase0/plan.md) for the full inventory and compatibility cutover strategy.

---

## Phase 1 — Harden canonical contract validation at the activity boundary

### Objectives

Make the activity boundary the only place allowed to normalize or reject runtime/provider payloads.

### Tasks

* [x] Review `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult` schema constraints and confirm they cover all currently needed fields.
* [x] Add or tighten small shared helpers for activity-side contract validation:

  * [x] “build canonical start handle”
  * [x] “build canonical status”
  * [x] “build canonical result”
  * [x] “raise UnsupportedStatus on unknown provider/runtime state”
* [x] Add one shared contract-enforcement helper per activity family if useful:

  * [x] external provider activity helper
  * [x] managed runtime activity helper
* [x] Standardize metadata usage:

  * [x] `providerStatus`
  * [x] `normalizedStatus`
  * [x] `externalUrl`
  * [x] provider/runtime-specific extras in `metadata` only
* [x] Prohibit provider-shaped top-level fields from crossing the workflow boundary:

  * [x] `external_id`
  * [x] `tracking_ref`
  * [x] `provider_status`
  * [x] raw status dicts
* [x] Add tests that assert malformed shapes fail at the activity boundary, not later in workflow code.

### Deliverables

* [x] shared contract-validation helpers
* [x] failing tests for non-canonical payloads
* [x] explicit contract-boundary enforcement

> **Phase 1 Deliverables completed**: Added canonical factory helpers (`build_canonical_status`, etc.) and contract boundary enforcement to `agent_runtime_models.py`.

---

## Phase 2 — Canonicalize all external provider activities

### Objectives

Make every external provider emit canonical contracts directly so `MoonMind.AgentRun` can stop repairing them.

### Tasks

* [x] Audit each external provider integration:

  * [x] Jules
  * [x] Codex Cloud
  * [x] any other polling-style provider
  * [x] OpenClaw streaming path
* [x] For each polling-style provider, update:

  * [x] `integration.<provider>.start`
  * [x] `integration.<provider>.status`
  * [x] `integration.<provider>.fetch_result`
  * [x] `integration.<provider>.cancel`
* [x] Ensure each provider adapter normalizes provider-native state into canonical `AgentRunState` before returning.
* [x] Ensure unknown provider states raise `UnsupportedStatus` at the adapter/activity boundary.
* [x] Remove any remaining dependence on provider-shaped return payloads from activity handlers.
* [x] Make `integration.<provider>.start` always return canonical `AgentRunHandle`, never a dict that needs special casing in the workflow.
* [x] Make `integration.<provider>.status` always return canonical `AgentRunStatus`.
* [x] Make `integration.<provider>.fetch_result` always return canonical `AgentRunResult`.
* [x] For OpenClaw:

  * [x] keep the execute-style branch
  * [x] ensure `integration.openclaw.execute` always returns canonical `AgentRunResult`
  * [x] reject raw streaming aggregate payloads that are not canonicalized
* [x] Add provider-specific tests:

  * [x] valid canonical outputs
  * [x] unknown status rejection
  * [x] metadata placement
  * [x] no provider-specific top-level fields leak out

### Deliverables

* [x] all external provider activities canonicalized
* [x] provider adapters become the sole owner of provider-state normalization
* [x] no workflow-side external payload repair needed for new histories

> **Phase 2 Deliverables completed** (PR #1126): Jules, Codex Cloud, and OpenClaw activities extracted into standalone modules (`jules_activities.py`, `codex_cloud_activities.py`, `openclaw_activities.py`). All external provider activities now return typed canonical `AgentRunHandle` / `AgentRunStatus` / `AgentRunResult`. `TemporalIntegrationActivities` forwards to these modules. Unknown provider states raise `UnsupportedStatus` at the adapter boundary.

---

## Phase 3 — Canonicalize managed runtime activities

### Objectives

Make the managed runtime activity family return canonical runtime contracts directly.

### Tasks

* [ ] Audit the managed runtime activity family:

  * [ ] `agent_runtime.launch`
  * [ ] `agent_runtime.status`
  * [ ] `agent_runtime.fetch_result`
  * [ ] `agent_runtime.cancel`
  * [ ] `agent_runtime.publish_artifacts`
* [ ] Make `agent_runtime.status` always return canonical `AgentRunStatus`.
* [ ] Make `agent_runtime.fetch_result` always return canonical `AgentRunResult`.
* [ ] Make `agent_runtime.cancel` return canonical `AgentRunStatus` if the current design expects a status return.
* [ ] Confirm whether `agent_runtime.launch` remains an internal launch/support activity or should also be wrapped more tightly around canonical handle semantics.
* [ ] Ensure managed runtime state normalization is done before workflow consumption:

  * [ ] store/supervisor state → canonical `AgentRunState`
  * [ ] provider/runtime error conditions → canonical failure fields
* [ ] Ensure large runtime outputs stay in artifacts and only refs enter `AgentRunResult`.
* [ ] Ensure rate-limit and cooldown-related metadata still flows correctly without requiring workflow-side repair.
* [ ] Add managed-runtime tests:

  * [ ] running state
  * [ ] terminal success
  * [ ] terminal failure
  * [ ] canceled
  * [ ] timed out
  * [ ] malformed status/result rejection

### Deliverables

* managed runtime family canonicalized
* managed normalization lives in the managed runtime layer, not in workflow code

---

## Phase 4 — Remove workflow-side coercion from `MoonMind.AgentRun`

### Objectives

Delete the compatibility glue in `MoonMind.AgentRun` once Phases 2 and 3 are complete.

### Tasks

* [ ] Remove external start special-casing for non-canonical dicts.
* [ ] Delete `_coerce_external_start_status`.
* [ ] Delete `_coerce_external_status_payload`.
* [ ] Delete `_coerce_managed_status_payload`.
* [ ] Simplify external status polling path to assume canonical `AgentRunStatus`.
* [ ] Simplify managed status polling path to assume canonical `AgentRunStatus`.
* [ ] Simplify fetch-result paths to assume canonical `AgentRunResult`.
* [ ] Utilize Temporal `workflow.patched` strictly for in-flight history replay safety (if absolutely mandated), entirely deleting any non-patched compatibility aliases.
* [ ] Add regression tests proving `MoonMind.AgentRun` works with canonical returns only.

### Deliverables

* `MoonMind.AgentRun` becomes a lifecycle orchestrator, not a payload repair layer
* provider/runtime repair logic removed from workflow code

---

## Phase 5 — Narrow or remove parent-side result adaptation in `MoonMind.Run`

### Objectives

Reduce `MoonMind.Run` to one narrow execution-loop adapter or remove the remaining result translation if feasible.

### Tasks

* [ ] Review whether `_map_agent_run_result` is still needed once child workflows return canonical results cleanly.
* [ ] Decide one of two directions:

  * [ ] keep one narrow parent-side adapter that translates canonical `AgentRunResult` into the generic execution-loop result shape
  * [ ] refactor the execution loop so agent-runtime steps are handled natively without `_map_agent_run_result`
* [ ] Remove any provider/runtime-specific assumptions from `_map_agent_run_result`.
* [ ] Ensure publication and proposal logic still receives the data it needs after narrowing the mapper.
* [ ] Add tests proving `MoonMind.Run` no longer depends on provider-specific child result details.

### Deliverables

* parent workflow no longer contains provider/runtime-specific result repair
* any remaining mapping is generic and local to execution-loop mechanics only

---

## Phase 6 — Testing, rollout, and cleanup

### Objectives

Roll out safely without breaking in-flight histories.

### Tasks

* [ ] Add contract tests for every agent-facing activity type.
* [ ] Add workflow integration tests for:

  * [ ] managed child run
  * [ ] external polling provider
  * [ ] streaming-gateway provider
  * [ ] rate-limit/cooldown loop
  * [ ] cancellation path
* [ ] Add replay-safety tests where existing histories must still work.
* [ ] Gate any compatibility removal behind Temporal patching where required.
* [ ] Run end-to-end validation on:

  * [ ] Jules
  * [ ] Codex Cloud
  * [ ] OpenClaw
  * [ ] one managed runtime
* [ ] Update remaining tracker docs only after implementation lands.

### Deliverables

* full test coverage for the canonical boundary
* safe rollout path
* cleanup of dead compatibility glue

---

## Recommended implementation order

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4
6. Phase 5
7. Phase 6

That order keeps the contract boundary solid before deleting workflow glue.

## Suggested PR breakdown

To balance review size with the project's strict compatibility rules, these PRs should be merged in rapid succession to minimize the time the codebase spends in a partial migration state.

### PR 1
Contract hardening helpers + tests

### PR 2
External provider activity canonicalization

### PR 3
Managed runtime activity canonicalization

### PR 4
`MoonMind.AgentRun` glue removal

### PR 5
`MoonMind.Run` result-adaptation narrowing

### PR 6
Cleanup, replay-safe removals (using `workflow.patched` if needed), and final test hardening

## Definition of done

This work is done when:

* canonical schemas are the only normal workflow-facing runtime shapes
* adapters/activities own normalization and rejection
* `MoonMind.AgentRun` no longer coerces provider/runtime payloads
* `MoonMind.Run` no longer contains provider/runtime-specific result repair
* compatibility code exists only where replay safety truly requires it

I can also turn this into a markdown plan document with checkbox task lists in MoonMind’s docs style.
