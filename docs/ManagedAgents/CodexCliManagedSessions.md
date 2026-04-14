# Codex CLI Managed Sessions

Status: Desired state
Owners: MoonMind Platform
Last updated: 2026-04-09
Related:
- [`docs/ManagedAgents/SharedManagedAgentAbstractions.md`](./SharedManagedAgentAbstractions.md)
- [`docs/ManagedAgents/ClaudeCodeManagedSessions.md`](./ClaudeCodeManagedSessions.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/ArtifactPresentationContract.md`](../Temporal/ArtifactPresentationContract.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/Tasks/AgentSkillSystem.md`](../Tasks/AgentSkillSystem.md)

## 1. Purpose

This document defines the desired-state architecture contract for the **Codex managed session plane**.

It freezes the smallest supported session-plane shape before broader implementation work:

- Codex only
- Docker only
- one task-scoped session container per task
- no cross-task session reuse
- artifact-first logs and continuity
- no Kubernetes orchestration
- no Claude/Gemini managed session plane
- no generic runtime marketplace

This document defines the target contract only. Rollout sequencing and implementation backlog belong in specs or `docs/tmp/`.

## 2. Layering

The Codex managed session plane is split into two layers.

### 2.1 Agent Orchestration Layer

MoonMind owns:

- Temporal workflows
- activities
- runtime adapters
- policy evaluation
- artifact publication
- observability hooks
- recovery and cancellation semantics

Temporal remains the control plane for workflow orchestration.

In the current near-term production path, durable operator/audit truth comes from:

- artifacts
- bounded workflow metadata

The JSON-backed `ManagedSessionStore` is also part of the production path, but as an
operational supervision record for recovery and reconciliation, not as the
operator-facing source of truth.

### 2.2 Managed Session Plane

The managed session plane is the task-scoped Codex runtime environment:

- one Docker container per task
- Codex App Server running inside that container
- one active Codex thread per session epoch
- continuity reused across steps within the same task only

The session plane is a continuity and performance cache. It is not durable truth.

Managed-session steps may invoke **control-plane tools** that launch separate workload containers as described in [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md). Those workload containers remain outside session identity: they do not become `session_id`, `session_epoch`, `container_id`, `thread_id`, or `active_turn_id`, and they do not replace the task-scoped session container.

Codex session containers that need to create additional MoonMind tasks must be
launched on the configured MoonMind Docker network and receive `MOONMIND_URL`
pointing at the internal API endpoint. This keeps task creation on the
Temporal-aware API path instead of relying on removed queue/DB shortcuts.

## 3. Protocol

For the Codex MVP, the session protocol is **Codex App Server**, not PTY scraping and not `codex exec` as the primary session surface.

MoonMind maps the managed session plane to Codex App Server concepts:

- thread lifecycle
- turn lifecycle
- steering and interruption
- optional approvals and command execution later

`codex exec --json` remains a bring-up and smoke-test harness, not the primary long-lived session protocol.

## 4. Mapping to Shared Managed Agent Abstractions

This document is the Codex CLI runtime binding of [`SharedManagedAgentAbstractions.md`](./SharedManagedAgentAbstractions.md). It does not define a separate top-level agent model.

| Shared contract | Codex CLI managed-session realization |
| --- | --- |
| `ManagedAgentSpec` | Task-scoped desired state for a Codex-backed managed session, including workspace, profile, permissions, observability, and reuse policy. |
| `reconcile(spec)` | Start, resume, or replace the task-scoped session container and Codex App Server thread as required by the current binding and compatibility hash. |
| `ManagedSessionBinding` | Binds the task-scoped `session_id` to the current `container_id`, `session_epoch`, and Codex `thread_id`. |
| `ManagedSessionObservation` | Normalized status derived from supervisor state, App Server turn state, published artifact refs, and bounded workflow metadata. |
| `ManagedSessionRef` | Opaque reference to the Codex session identity. Higher layers must not parse `thread_id` or container details to infer semantics. |
| `watch(sessionRef)` | Stream normalized session and turn events from the supervisor and Codex App Server into MoonMind observability records. |
| `sendInput(sessionRef, input)` | Execute a Codex `send_turn` action for the active thread and epoch. |

Codex-specific action names such as `start_session`, `send_turn`, and `interrupt_turn` are the plane-local control vocabulary. The shared layer should still reason in terms of reconciliation, normalized observation, and opaque session refs.

### 4.1 Normalized phase mapping

| Shared phase | Codex CLI source state |
| --- | --- |
| `pending` / `reconciling` | Container launch, App Server startup, thread creation, or compatibility replacement in progress. |
| `ready` | Session container and Codex thread are bound with no active turn. |
| `running` | `active_turn_id` is set and Codex App Server is processing a turn. |
| `waitingForInput` | The thread is idle and ready for the next MoonMind input. |
| `awaitingApproval` | Codex App Server has emitted an approval or intervention request. |
| `interrupted` | A turn was interrupted or canceled while preserving the session binding. |
| `completed` | The task-scoped session reached normal terminal completion. |
| `failed` | Supervisor, container, App Server, or turn failure prevents successful continuation. |
| `deleted` | `clear_session`, cancellation cleanup, or retention policy removed the active binding. |

## 5. Session Identity

The canonical bounded session identity is:

- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`

Rules:

1. `session_id` identifies the MoonMind task-scoped managed session.
2. `session_epoch` identifies one logical continuity interval within that session.
3. `container_id` identifies the active Docker container for the task-scoped session.
4. `thread_id` identifies the active Codex App Server thread for the current epoch.
5. `active_turn_id` identifies the in-flight Codex turn when one exists.

## 6. Control Actions

The canonical Phase 1 control actions are:

- `start_session`
- `resume_session`
- `send_turn`
- `steer_turn`
- `interrupt_turn`
- `clear_session`
- `cancel_session`
- `terminate_session`

These actions are the stable MoonMind-side vocabulary for the Codex managed session plane. Runtime-specific transport details stay behind the adapter boundary.

## 7. Clear / Reset Semantics

`clear_session` is not a terminal slash-command emulation.

The canonical semantics are:

1. write a `session.control_event` artifact
2. write a `session.reset_boundary` artifact
3. increment `session_epoch`
4. start a new Codex thread inside the same container
5. clear `active_turn_id`

Rules:

- clear/reset preserves `session_id`
- clear/reset preserves `container_id`
- clear/reset requires a new `thread_id`
- UI and API consumers must present the new epoch boundary explicitly

## 8. Durable State Rule

The managed session plane has three different truth surfaces.

### 8.1 Operator / Audit Truth

Operator presentation, audit, and continuity review come from:

- artifacts
- bounded workflow metadata

These are the authoritative surfaces operators should inspect.

### 8.2 Operational Recovery Index

The JSON-backed `ManagedSessionStore` record is allowed to participate in recovery
and reconciliation as the operational supervision index. It tracks the currently
known session/container/thread state and the latest published artifact refs so the
controller and supervisor can recover or reconcile after restarts.

It is not the operator/audit source of truth.

### 8.3 Disposable Cache

Container-local runtime state is performance and continuity cache only.

Operator/audit truth must not depend on:

- in-memory container state
- container-local thread databases
- terminal scrollback
- runtime home directories as durable truth

Artifact-backed logs and diagnostics remain authoritative even when live streaming exists.

## 9. Artifact Expectations

Every managed Codex step must remain execution-centric even when a container is reused across steps.

At minimum, each step must produce durable evidence through the existing artifact system, including step outputs and runtime diagnostics. Session continuity should be represented with summary, checkpoint, and control-boundary artifacts rather than inferred from container state.

For the current production path, `managed_session_controller` plus the managed-session
supervisor are the production artifact publishers for summary/checkpoint/control/reset
refs and related session observability. The transitional in-container
`fetch_session_summary()` and `publish_session_artifacts()` helpers may still exist as
fallback or bring-up helpers, but they are not the production publication path while
they return empty publication refs.

## 10. Non-goals for This Contract Slice

This contract does not define:

- Kubernetes launch semantics
- cross-task session reuse
- session UIs beyond continuity-aware artifact presentation
- generalized multi-runtime certification
- Claude/Gemini session-plane behavior
- PTY attach or interactive terminal embedding
