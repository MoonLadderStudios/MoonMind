# Research: Codex Session Phase 2 Runtime Behaviors

## Decision 1: Keep `CancelSession` Non-Destructive

**Decision**: `CancelSession` stops active in-flight work and leaves the session recoverable or idle. It does not destroy the container or finalize the supervision record.

**Rationale**: The canonical control vocabulary lists cancel and terminate separately. Collapsing both into destructive termination makes operator intent ambiguous and prevents recovery, inspection, or later follow-up after a canceled turn.

**Alternatives considered**:

- Treat cancel as terminate. Rejected because it leaks ambiguity into the user-facing control model and conflicts with the Phase 2 requirement.
- Add a separate container kill path for cancel. Rejected because destructive cleanup belongs to `TerminateSession`.

## Decision 2: Use Interruption as the Near-Term Active Work Stop Mechanism

**Decision**: When a cancel request arrives with an active turn, route active-work stopping through the existing interrupt boundary and then record cancellation as the latest workflow control action.

**Rationale**: The controller and runtime already expose interrupt semantics for an active turn, and the required near-term cancel behavior is to stop in-flight work without destroying continuity. Reusing interruption avoids adding another runtime protocol before a distinct provider-level cancel primitive exists.

**Alternatives considered**:

- Add a new low-level cancel action immediately. Rejected because there is no separate established container protocol in the current managed-session runtime.
- Record cancel without stopping active work. Rejected because it would not satisfy the operator goal of stopping in-flight execution.

## Decision 3: Termination Must Surface Cleanup Failures

**Decision**: `TerminateSession` must call the runtime termination activity and only mark workflow completion readiness after cleanup/finalization succeeds. If cleanup fails before confirmation, the failure remains visible to normal workflow/activity retry or operator handling.

**Rationale**: Silently completing termination after a failed cleanup creates the exact leak this phase is intended to prevent. Temporal retry behavior is useful only if the workflow does not swallow cleanup failures as apparent success.

**Alternatives considered**:

- Best-effort terminate with swallowed errors. Rejected because it can leave containers alive while the workflow appears terminal.
- Always mark terminated and rely on a later sweeper. Rejected because Phase 2 exit criteria require terminate to remove the container and finalize supervision.

## Decision 4: Implement Steering Through the Codex App Server Turn Protocol

**Decision**: `steer_turn` must validate the active turn, resume the current Codex thread if needed, send steering input to the active turn through the Codex App Server protocol, and persist the resulting runtime state.

**Rationale**: The session plane contract defines steering as a first-class turn lifecycle control. A hardcoded unsupported response prevents operator correction and keeps the workflow control surface ahead of the runtime behavior.

**Alternatives considered**:

- Keep the typed workflow update but leave runtime unsupported. Rejected because that was acceptable only as a Phase 1 bridge, not Phase 2 completion.
- Implement steering as a new full turn. Rejected because it would lose the active-turn identity and behave like follow-up rather than steering.

## Decision 5: Gate Idempotency on Durable Proof of Completion

**Decision**: Make launch, clear, interrupt, and terminate idempotent only when the durable supervision record proves the previous side effect completed for the same logical session state.

**Rationale**: Temporal activities are at-least-once. Retrying external side effects can remove the wrong container, advance an epoch twice, or duplicate cleanup. Durable proof avoids duplicated side effects without hiding stale or mismatched requests.

**Alternatives considered**:

- Treat any stale request as a successful duplicate. Rejected because stale locators may represent real client bugs or unsafe state drift.
- Add a new idempotency-key store. Rejected for this phase because the existing supervision record already contains the decisive state for launch, clear, interrupt, and terminate.

## Decision 6: Heartbeat Blocking Control Activities

**Decision**: Session control activity wrappers that may block while waiting on runtime/controller work must heartbeat and declare heartbeat timeouts.

**Rationale**: Without activity heartbeats, cancellation may not be delivered while the worker is waiting on container/runtime operations. Heartbeats make cancellation behavior observable and enforceable within the existing activity timeout model.

**Alternatives considered**:

- Rely only on start-to-close timeouts. Rejected because timeout expiry is slower and does not deliver cancellation promptly.
- Heartbeat only `send_turn`. Rejected because clear, interrupt, steer, and terminate can also block on runtime/container calls.

## Decision 7: Classify Permanent Control Failures Explicitly

**Decision**: Invalid input, stale locator, unsupported state, and invalid runtime status outcomes must be treated as permanent failures rather than transient infrastructure failures.

**Rationale**: Retrying stale or invalid control requests burns time and may repeat side effects. The retry policy should distinguish invalid control input from transient Docker/runtime transport failures.

**Alternatives considered**:

- Retry all runtime/control failures uniformly. Rejected because it conflicts with the resiliency requirement and obscures operator actionability.
- Disable retries on all session controls. Rejected because transient runtime/container transport failures remain valid retry candidates.
