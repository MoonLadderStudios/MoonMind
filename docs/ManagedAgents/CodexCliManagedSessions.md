# Codex CLI Managed Sessions

Status: Desired state
Owners: MoonMind Platform
Last updated: 2026-04-09
Related:
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

This document defines the target contract only. Rollout sequencing and implementation backlog belong in specs or `local-only handoffs`.

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

### 3.1 In-flight Live Output

During `send_turn`, the container-side Codex runtime mirrors visible Codex rollout
events into the managed-session artifact spool while the turn is still running.
The mirrored stream includes assistant messages, tool-call markers, and tool-call
outputs that Codex records in the rollout transcript. The managed-session
supervisor tails that spool and publishes normalized `stdout`/`stderr` chunks into
the run-global Live Logs sequence.

The rollout transcript remains a container-local runtime cache. MoonMind does not
present the raw transcript as durable operator truth; it converts selected visible
entries into artifact-backed output and observability events.

## 4. Session Identity

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

The managed-session supervisor reconciles `active_turn_id` from the
container-written session state file while `send_turn` is still executing. This
keeps operator-visible summaries and Live Logs headers truthful during long
Codex turns, before the long-running `send_turn` activity returns a terminal
response.

## 5. Control Actions

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

## 6. Clear / Reset Semantics

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

## 7. Durable State Rule

The managed session plane has three different truth surfaces.

### 7.1 Operator / Audit Truth

Operator presentation, audit, and continuity review come from:

- artifacts
- bounded workflow metadata

These are the authoritative surfaces operators should inspect.

### 7.2 Operational Recovery Index

The JSON-backed `ManagedSessionStore` record is allowed to participate in recovery
and reconciliation as the operational supervision index. It tracks the currently
known session/container/thread state and the latest published artifact refs so the
controller and supervisor can recover or reconcile after restarts.

It is not the operator/audit source of truth.

### 7.3 Disposable Cache

Container-local runtime state is performance and continuity cache only.

Operator/audit truth must not depend on:

- in-memory container state
- container-local thread databases
- terminal scrollback
- runtime home directories as durable truth

Artifact-backed logs and diagnostics remain authoritative even when live streaming exists.

## 8. Artifact Expectations

Every managed Codex step must remain execution-centric even when a container is reused across steps.

At minimum, each step must produce durable evidence through the existing artifact system, including step outputs and runtime diagnostics. Session continuity should be represented with summary, checkpoint, and control-boundary artifacts rather than inferred from container state.

For the current production path, `managed_session_controller` plus the managed-session
supervisor are the production artifact publishers for summary/checkpoint/control/reset
refs and related session observability. The transitional in-container
`fetch_session_summary()` and `publish_session_artifacts()` helpers may still exist as
fallback or bring-up helpers, but they are not the production publication path while
they return empty publication refs.

## 9. Rate-limit retry behavior

Codex managed-session turns must surface model-provider rate limits through the
shared managed-runtime failure taxonomy. The session controller should retry
rate-limit failures with bounded exponential backoff and jitter, honor provider
retry hints when available, and keep attempt evidence bounded in summaries and
diagnostics.

If retries are exhausted, the session summary and diagnostics must explicitly
state that the turn hit a model-provider rate limit. The terminal result should
set the same `AgentRunResult` rate-limit metadata used by other managed
runtimes.

## 10. Non-goals for This Contract Slice

This contract does not define:

- Kubernetes launch semantics
- cross-task session reuse
- session UIs beyond continuity-aware artifact presentation
- generalized multi-runtime certification
- Claude/Gemini session-plane behavior
- PTY attach or interactive terminal embedding
