# Research: codex-managed-session-phase0-1

## Key Findings

1. The canonical doc already captures the desired control vocabulary and durable-state rule, but it does not explain the current operational role of `ManagedSessionStore`, which is why the Phase 0 slice is necessary before more workflow behavior changes.
2. `MoonMind.AgentSession` still exposes two mutation styles at once: a generic `control_action` signal and two typed updates (`SendFollowUp`, `ClearSession`). This is the main Phase 1 contract mismatch.
3. The workflow’s binding state is initialized in `run()`, which means handler-visible state is not established through `@workflow.init` even though callers and validators need that state before `run()` progresses.
4. The controller/runtime stack already supports `interrupt_turn`, so the highest-value typed update missing from the workflow layer can be implemented in this slice without waiting on later phases.
5. `steer_turn` remains unsupported in the transitional runtime. This slice can still expose the typed workflow update and validator contract while preserving the current runtime failure path until Phase 2.

## Decision

Implement Phase 0 and Phase 1 as one slice:

- Phase 0: clarify current production truth surfaces in the canonical doc.
- Phase 1: harden `MoonMind.AgentSession` around typed updates, validators, and handler-safe initialization.

This keeps the slice within the user’s request and avoids leaking into later work such as true terminate semantics, steer runtime implementation, or Continue-As-New.
