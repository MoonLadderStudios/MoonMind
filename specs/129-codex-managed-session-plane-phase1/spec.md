# Spec: Codex Managed Session Plane Phase 1

## Problem

MoonMind has canonical contracts for managed agent execution and artifact presentation, but it does not yet have a frozen desired-state contract for a **task-scoped Codex managed session plane**. Without that contract, later implementation work risks drifting across docs, workflow payloads, and runtime boundaries.

Phase 1 must lock the smallest viable session-plane contract before new workflows, activities, or API projections are built.

## Requirements

### Functional Requirements

- **FR-001**: MoonMind MUST define a canonical Phase 1 managed session-plane contract for **Codex only**.
- **FR-002**: The Phase 1 contract MUST constrain the managed session plane to **Docker only**.
- **FR-003**: The Phase 1 contract MUST define **one task-scoped session container per task** and MUST explicitly disallow cross-task session reuse.
- **FR-004**: The Phase 1 contract MUST define the bounded session identity fields: `session_id`, `session_epoch`, `container_id`, `thread_id`, and `active_turn_id`.
- **FR-005**: The Phase 1 contract MUST define the canonical control actions: `start_session`, `resume_session`, `send_turn`, `steer_turn`, `interrupt_turn`, `clear_session`, `cancel_session`, and `terminate_session`.
- **FR-006**: The Phase 1 contract MUST define `clear_session` as an epoch boundary that preserves `session_id` and `container_id`, increments `session_epoch`, requires a new `thread_id`, and clears `active_turn_id`.
- **FR-007**: The Phase 1 contract MUST define the durable-state rule that artifacts plus bounded workflow metadata are authoritative, while container state is continuity/performance state only.
- **FR-008**: Canonical docs MUST describe the Codex managed session plane as a desired-state contract without embedding phased migration narrative in the canonical document.

### Non-Functional Requirements

- **NF-001**: The contract MUST be encoded in executable schema/state models, not only prose.
- **NF-002**: The clear/reset epoch semantics MUST be covered by unit tests.
- **NF-003**: Existing schema tests for agent runtime contracts MUST continue to pass unchanged.
- **NF-004**: The implementation MUST avoid introducing Kubernetes, cross-task reuse, or multi-runtime abstractions in this phase.

### Acceptance Criteria

1. **Given** the Phase 1 session-plane contract is instantiated, **When** it is inspected, **Then** it exposes Codex-only, Docker-only, task-scoped defaults with artifact-first continuity and no cross-task reuse.
2. **Given** a managed session state with `session_epoch=1`, `thread_id="thread-1"`, and `active_turn_id="turn-1"`, **When** `clear_session` is applied with `new_thread_id="thread-2"`, **Then** the returned state preserves `session_id` and `container_id`, increments `session_epoch` to `2`, sets `thread_id` to `"thread-2"`, and clears `active_turn_id`.
3. **Given** `clear_session` is attempted with a blank or unchanged `new_thread_id`, **When** validation runs, **Then** the operation fails fast.
4. **Given** canonical docs are reviewed, **When** operators look for the Codex managed session-plane definition, **Then** the desired-state contract is documented under `docs/` and phase sequencing remains in `specs/` rather than the canonical doc.

## Scope

- **In scope**: schema/state contract models for the Codex managed session plane, unit tests for those models, canonical desired-state documentation, and a new spec/plan/tasks set for the phase.
- **Out of scope**: `MoonMind.AgentSession`, activity implementations, session launch/resume APIs, session projection endpoints, operator UI controls, Kubernetes, non-Codex runtimes, and runtime marketplace abstractions.
