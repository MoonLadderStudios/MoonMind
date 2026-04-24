# Contract: Managed-Session Retrieval Durability Boundaries

## Purpose

Define the MoonMind-owned contract for authoritative retrieval truth, compact durable publication, and reset-era recovery for managed-session retrieval context under MM-507.

## Inputs

### Published Retrieval Context

Required behavior:
- Retrieval output is published as a durable artifact or ref representing the latest `ContextPack` for the step.
- Compact runtime/workflow metadata may point to the artifact or ref.
- Large retrieved bodies remain inside the artifact/ref-backed output rather than durable workflow payloads.

### Managed Session Continuity Event

Required behavior:
- A reset, replacement, or new session epoch is treated as a continuity boundary.
- The continuity boundary may discard runtime-local cache state.
- The continuity boundary must not delete or orphan the authoritative durable retrieval artifact/ref.

## Outputs

### Durable Truth Surface

MoonMind must preserve a recovery surface that lets later runtime or workflow boundaries identify the authoritative retrieval output.

Contract:
- The durable truth surface is artifact/ref-backed.
- Compact metadata may identify the latest durable retrieval artifact/ref.
- Session-local runtime memory is not a valid substitute for the durable truth surface.

### Recovery Behavior

MoonMind must expose a deterministic recovery path after reset or a new session epoch.

Contract:
- Recovery may rerun retrieval or reattach the latest durable context reference.
- The selected recovery path must be observable and consistent for the managed-session boundary.
- Recovery cannot require the previous session-local cache contents to remain present.

## Invariants

- Managed-session continuity caches are convenience state only, not durable truth.
- Large retrieved bodies remain behind durable artifacts or refs.
- Reset and session-epoch transitions preserve authoritative retrieval evidence.
- The contract remains runtime-neutral across Codex and future managed runtimes.
- Externally visible semantics at the MoonMind boundary must not depend on runtime-specific persistence rules.

## Verification Expectations

Unit verification must prove:
- compact metadata points to durable retrieval artifacts/refs,
- reset or reconcile flows preserve durable retrieval evidence,
- recovery decisions do not depend on session-local cache state.

Integration or workflow-boundary verification must prove:
- the latest durable retrieval artifact/ref remains usable after reset or a new session epoch,
- recovery behavior reruns retrieval or reattaches the latest durable context reference deterministically,
- runtime-visible durability semantics remain consistent across managed-runtime boundaries.
