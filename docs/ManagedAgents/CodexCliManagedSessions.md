# Codex CLI Managed Sessions

Status: Desired state  
Owners: MoonMind Platform  
Last updated: 2026-07-13
Related:

- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/ArtifactPresentationContract.md`](../Temporal/ArtifactPresentationContract.md)
- [`docs/ManagedAgents/DockerBackendService.md`](./DockerBackendService.md)
- [`docs/Steps/SkillSystem.md`](../Steps/SkillSystem.md)

---

## 1. Purpose

This document defines the desired-state architecture contract for the **Codex
CLI binding** of the shared managed session plane.

The supported session-plane shape is:

- Codex binding only;
- Docker-deployed MoonMind runtime containers;
- one workflow-scoped session container per workflow;
- containerized repository work submitted through the Docker Backend Service;
- no direct Docker socket or Docker API authority in the session;
- no cross-workflow session reuse;
- artifact-first logs and continuity;
- no Kubernetes orchestration in this contract;
- no Claude Code binding details;
- no generic runtime marketplace.

This is a declarative target contract. Implementation sequencing does not belong
in this canonical document.

---

## 2. Layering

### 2.1 Agent orchestration layer

MoonMind owns:

- Temporal workflows and Activities;
- runtime adapters;
- policy evaluation;
- artifact publication;
- observability hooks;
- recovery and cancellation semantics;
- provider-profile capacity;
- container-job submission and evidence.

Temporal remains the durable control plane. Artifacts and bounded workflow
metadata are operator and audit truth. The `ManagedSessionStore` is an
operational supervision index, not a second durable source of truth.

### 2.2 Managed session plane

The managed session plane is the workflow-scoped Codex runtime environment:

- one session container per workflow execution;
- Codex App Server running inside that container;
- one active Codex thread per session epoch;
- continuity reused across ordered steps in the same workflow only.

The session plane is a continuity and performance cache.

### 2.3 Container-job boundary

Ordinary repository builds and tests that require a container are submitted to
[`DockerBackendService.md`](./DockerBackendService.md). The Codex session invokes
MoonMind's typed asynchronous tools and consumes the returned job status, logs,
and artifacts.

The session does not receive the host socket, `DOCKER_HOST`, or raw Docker API
authority. Workload containers remain outside session identity: they do not
become the session's `session_id`, `session_epoch`, `container_id`, `thread_id`,
or `active_turn_id`.

The deployment-selected daemon may reuse an image across workflows. That image
cache is backend state, not Codex session state.

### 2.4 MoonMind API reachability

A Codex session that needs to create workflows or submit container jobs joins the
configured MoonMind application network and receives `MOONMIND_URL` for the
internal API. Requests stay on authenticated Temporal-aware API and MCP paths.

---

## 3. Protocol

The primary session protocol is **Codex App Server**, not PTY scraping and not
`codex exec` as the long-lived session surface.

MoonMind maps the session plane to:

- thread lifecycle;
- turn lifecycle;
- steering and interruption;
- clear/reset boundaries;
- optional approval and command surfaces.

`codex exec --json` may be used for smoke tests, but it is not the primary
session protocol.

### 3.1 In-flight output

During `send_turn`, the container-side runtime mirrors visible Codex rollout
events into the managed-session artifact spool. The supervisor tails that spool
and publishes normalized stdout/stderr and system events into Live Logs.

The raw rollout transcript is a runtime-local cache. MoonMind publishes selected
visible content as artifact-backed output and observations.

### 3.2 Turn instruction preparation

`MoonMind.AgentRun` prepares managed-session turn instructions through
`agent_runtime.prepare_turn_instructions` before `agent_runtime.send_turn`.
Preparation builds bounded final prompt text, materializes selected skills, and
attaches compact retrieval metadata.

Preferred command order for new histories:

1. perform metadata-only preparation preflight when launch metadata needs refs;
2. launch or resume the workflow-scoped session;
3. prepare final instructions against the launched workspace boundary;
4. submit the turn.

Changes to this order are replay-visible and require Temporal patch/version
markers or Worker Versioning so in-flight histories replay their recorded path.

---

## 4. Session identity

The canonical bounded identity is:

- `session_id`;
- `session_epoch`;
- `container_id`;
- `thread_id`;
- `active_turn_id`.

Rules:

1. `session_id` identifies the workflow-scoped managed session.
2. `session_epoch` identifies one continuity interval.
3. `container_id` identifies the active Codex session container.
4. `thread_id` identifies the active App Server thread.
5. `active_turn_id` identifies the in-flight turn when present.

A container job requested by the session has its own `job_id` and workload
container identifier. Those fields are associations, not additions to session
identity.

The supervisor reconciles active-turn state from the runtime while a turn is
running so summaries and Live Logs remain truthful before the long-running
Activity returns.

---

## 5. Control actions

The stable MoonMind-side vocabulary is:

- `start_session`;
- `resume_session`;
- `send_turn`;
- `steer_turn`;
- `interrupt_turn`;
- `clear_session`;
- `cancel_session`;
- `terminate_session`.

Runtime-specific transport details stay behind the adapter boundary.

Container jobs have separate controls: submit, status, logs, artifacts, and
cancel. Session-control verbs must not be overloaded to mean “run an arbitrary
container.”

---

## 6. Clear and reset semantics

`clear_session` is not terminal-command emulation.

Its canonical semantics are:

1. write a `session.control_event` artifact;
2. write a `session.reset_boundary` artifact;
3. increment `session_epoch`;
4. start a new Codex thread in the same compatible session container;
5. clear `active_turn_id`.

Clear/reset preserves `session_id` and normally preserves the compatible session
container. It requires a new thread and an explicit UI/API epoch boundary.

Clear/reset does not clear the Docker backend's shared image cache and does not
redefine container-job identity.

---

## 7. Durable-state rule

### 7.1 Operator and audit truth

Operator presentation and continuity review come from artifacts and bounded
workflow metadata.

### 7.2 Operational recovery index

`ManagedSessionStore` may track the latest known session, container, thread,
active turn, and artifact references for recovery and reconciliation. It is not
the operator/audit source of truth.

### 7.3 Disposable cache

Container-local thread databases, runtime homes, in-memory state, terminal
scrollback, and backend daemon state are disposable caches. Any required output
must be published as artifacts or compact durable metadata.

---

## 8. Artifact expectations

Every managed Codex step remains execution-centric even when one session
container is reused.

At minimum, each step publishes:

- bounded stdout/stderr or durable log references;
- runtime diagnostics;
- declared outputs;
- relevant session summary/checkpoint references;
- control and reset-boundary artifacts when applicable;
- associated container-job references and artifacts when a job was requested.

Session-state checkpoints prove continuity only. They do not imply workspace
capture or restore. Container-job artifacts likewise do not become session-state
checkpoints.

The managed-session controller and supervisor are the production publishers for
session continuity evidence. Bring-up helpers that return empty publication refs
are not authoritative publication paths.

---

## 9. OAuth token rotation

OAuth-backed Codex sessions use two distinct filesystem authorities:

- the provider profile's OAuth volume owns durable credential state;
- the workflow-scoped writable Codex home owns disposable per-run state.

At launch, MoonMind seeds the per-run home from the OAuth volume. When Codex
rotates `auth.json`, MoonMind writes the rotated credential state back through a
compare-and-swap boundary. The write is allowed only when the durable source
still matches the digest that seeded the session; a concurrent reconnect or
another credential owner wins and must never be overwritten by stale session
state. Lock acquisition is non-blocking, and filesystem failures remain
auxiliary to the authoritative assistant turn: MoonMind records a diagnostic
and retries persistence at a later session lifecycle boundary.

Provider responses such as `token_expired`, `refresh_token_reused`, or an access
token that cannot be refreshed are authentication failures. They terminate with
the canonical reauthentication recommendation and must not be retried as empty
assistant turns.

---

## 10. Rate-limit behavior

Codex turns surface provider rate limits through the shared managed-runtime
failure taxonomy. The controller applies bounded exponential backoff with jitter,
honors retry hints where available, and keeps attempt evidence bounded.

If retries are exhausted, summaries and diagnostics explicitly classify the
rate-limit failure and set the canonical `AgentRunResult` metadata.

---

## 11. Container-job behavior

A Codex session may request a job when repository instructions or verification
require a containerized environment.

The normal flow is:

1. inspect the repository and choose an image and command;
2. call `container.submit` with the current logical workspace reference;
3. poll `container.status` or wait through the tool host;
4. read bounded `container.logs` when useful;
5. retrieve `container.artifacts` at completion;
6. call `container.cancel` when the owning step is canceled.

MoonMind resolves the workspace and constructs the Docker run request. The agent
cannot provide an arbitrary host source path. A daemon-visible workspace probe
runs before an expensive missing-image pull.

The same interface supports .NET SDK images, Unreal automation images, Node or
Java toolchains, integration-test services, and other permitted images. No
runtime-specific worker pool is part of the Codex contract.

---

## 12. Non-goals

This contract does not define:

- Kubernetes launch semantics;
- cross-workflow session reuse;
- direct Docker CLI use from the Codex session;
- session UI beyond continuity-aware artifact presentation;
- generalized multi-runtime certification;
- Claude Code binding behavior;
- PTY attach or an embedded interactive terminal;
- a durable detached arbitrary-service framework.

---

## 13. Stable rules

1. One logical Codex session is workflow-scoped.
2. Temporal owns orchestration and replay-safe command ordering.
3. Codex App Server is the session protocol.
4. Artifacts and bounded metadata are durable truth.
5. Session state and workload state use separate identities.
6. Containerized work is requested through the Docker Backend Service.
7. The session receives neither the Docker socket nor `DOCKER_HOST`.
8. Images may be reused across workflows by the deployment-selected backend.
9. Clear/reset changes session continuity, not backend image retention.
10. Runtime-specific behavior remains behind the Codex adapter/controller.
