# Research: Codex Managed Session Phase 0 and Phase 1

## Decision: Treat The Managed Session Store As Recovery Index, Not Operator Truth

**Rationale**: The source document distinguishes durable operator/audit truth from operational recovery state. Artifacts plus bounded workflow metadata are the surfaces operators inspect; `ManagedSessionStore` is still allowed in the production path for supervision restart, reconciliation, and locating active runtime state.

**Alternatives considered**:

- Treat `ManagedSessionStore` as operator truth. Rejected because it would contradict the artifact-first contract and make operator presentation depend on a mutable supervision record.
- Remove the store from the production path. Rejected for this slice because recovery and reconciliation currently depend on it.

## Decision: Keep Controller/Supervisor As Production Artifact Publisher

**Rationale**: The current controller and supervisor path owns summary/checkpoint/control/reset artifact publication. Transitional in-container summary/publication helpers can remain as diagnostics or bring-up helpers, but must not be described or used as production publishers while they return empty refs.

**Alternatives considered**:

- Promote in-container helpers to production. Rejected because current helper output is incomplete.
- Defer documentation until later phases. Rejected because Phase 1 workflow hardening depends on unambiguous truth surfaces.

## Decision: Use Typed Workflow Updates For Mutations

**Rationale**: The source contract defines a canonical control vocabulary. Typed Updates allow validators to reject invalid requests before accepted Update events enter history, which is necessary for stale epochs, missing handles, missing active turns, duplicate clear, and terminating states.

**Alternatives considered**:

- Keep the generic `control_action` signal. Rejected because it makes workflow mutation ambiguous and bypasses Update validators.
- Use Signals for all controls. Rejected because controls need request/response semantics and deterministic boundary validation.

## Decision: Keep `attach_runtime_handles` As A Signal

**Rationale**: Runtime handle attachment is state propagation from runtime setup and can remain fire-and-forget. It is not the public mutating operator/caller control surface.

**Alternatives considered**:

- Convert handle attachment to an Update. Rejected for this slice because it is not operator-driven control and existing callers only need propagation semantics.

## Decision: Wire `InterruptTurn` Through Existing Runtime Support

**Rationale**: The runtime/controller surface already has interruption support, so Phase 1 can expose workflow-level `InterruptTurn` without waiting for later lifecycle phases.

**Alternatives considered**:

- Defer interruption to Phase 2. Rejected because the feature request explicitly calls out `InterruptTurn` as a Phase 1 workflow API gap.

## Decision: Expose `SteerTurn` Contract While Preserving Current Runtime Failure Behavior

**Rationale**: Phase 1 is the workflow control-plane contract slice. Real steering support in the Codex runtime/container protocol remains Phase 2 scope, so the workflow can validate and route `SteerTurn` while the runtime still owns unsupported behavior until later implementation.

**Alternatives considered**:

- Implement end-to-end steering in Phase 1. Rejected as scope creep beyond the requested Phase 0/1 slice.
- Omit `SteerTurn` until runtime support exists. Rejected because the Phase 1 control vocabulary requires the typed workflow surface.

## Decision: Resolve Current Epoch Before Parent Termination Update

**Rationale**: Parent workflows may hold an older binding after `ClearSession` advances the epoch. Loading the current session snapshot before dispatching `TerminateSession` lets the parent satisfy the stale-epoch validator without reintroducing generic signals.

**Alternatives considered**:

- Send the cached binding epoch. Rejected because it can fail after clear/reset.
- Remove stale-epoch validation from terminate. Rejected because the spec requires stale epoch rejection at the workflow boundary.
