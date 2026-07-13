# Managed Agent Architecture

- **Status:** Desired state
- **Audience:** Contributors, operators, runtime authors, and integration authors
- **Purpose:** Subsystem architecture entrypoint for MoonMind managed agents and managed sessions
- **Last updated:** 2026-07-13

**Related:**

- [`docs/MoonMindArchitecture.md`](../MoonMindArchitecture.md)
- [`docs/ManagedAgents/SharedManagedAgentAbstractions.md`](./SharedManagedAgentAbstractions.md)
- [`docs/ManagedAgents/CodexManagedSessionPlane.md`](./CodexManagedSessionPlane.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/ClaudeCodeManagedSessions.md`](./ClaudeCodeManagedSessions.md)
- [`docs/ManagedAgents/LiveLogs.md`](./LiveLogs.md)
- [`docs/ManagedAgents/DockerBackendService.md`](./DockerBackendService.md)
- [`docs/ManagedAgents/OAuthTerminal.md`](./OAuthTerminal.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/Steps/SkillSystem.md`](../Steps/SkillSystem.md)
- [`docs/Memory/MemoryArchitecture.md`](../Memory/MemoryArchitecture.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

---

## 1. Summary

MoonMind's managed-agent architecture is **managed-session first**.

A managed agent is not a directly launched process handle and it is not a
collection of competing runtime-management strategies. It is declarative desired
state for a resumable and observable work session. A runtime-specific managed
session plane reconciles that desired state into a healthy runtime container and
owns session creation, reuse, observation, control, and replacement decisions.

At the system level:

- **Temporal** is the durable outer orchestrator.
- **Managed runtimes** run in separate workflow-scoped containers.
- **Artifacts and bounded workflow metadata** are durable truth.
- **Session containers** are continuity and performance caches.
- **Context** is assembled by MoonMind and delivered through artifact references,
  workspace materialization, and runtime-specific input transport.
- **Live Logs** is a MoonMind-owned artifact-first observability surface.
- **Authentication** is owned by Provider Profiles, OAuth, and secret resolution.
- **Containerized build and test work** is submitted to the API-owned
  [`Docker Backend Service`](./DockerBackendService.md).

Codex CLI is currently first-class for workflow-scoped managed sessions. Claude
Code is first-class for managed runs and has documented session-domain models,
but it does not yet enter the live managed-session controller. Future runtimes
adopt the shared session contracts instead of introducing another top-level
managed-agent abstraction.

---

## 2. Scope and non-goals

### 2.1 In scope

This document defines:

- managed agents as declarative desired state;
- runtime-specific managed session planes;
- workflow-scoped managed session containers;
- session lifecycle and reconciliation boundaries;
- context assembly and runtime delivery;
- session-aware observability;
- provider-profile-driven authentication and launch shaping;
- secret reference, resolution, and materialization boundaries;
- the relationship between session, workload, and authentication containers.

### 2.2 Out of scope

Detailed contracts live in their owning documents:

- shared managed-agent schemas and conditions;
- the Codex App Server protocol;
- OAuth PTY transport;
- the Secrets System;
- skill resolution;
- the generic container-job contract;
- external delegated-agent execution.

---

## 3. Documentation hierarchy

This document is the Managed Agents subsystem architecture entrypoint.

`SharedManagedAgentAbstractions.md` owns runtime-neutral shared contracts:
managed-agent intent, managed-session references, normalized observations,
conditions, capability flags, and reconciliation semantics.

Runtime-specific documents such as `CodexManagedSessionPlane.md` and
`CodexCliManagedSessions.md` explain how a concrete runtime realizes the shared
model. They do not redefine the top-level architecture.

`DockerBackendService.md` owns the container-job interface and Docker backend
boundary. Managed-session documents describe how sessions request that service;
they do not define their own Docker daemon topology.

---

## 4. Core architectural model

MoonMind reasons about managed agents through four layers.

### 4.1 Declarative managed-agent intent

A managed agent is a durable logical identity plus desired state. Intent names:

- target runtime;
- workspace scope;
- provider profile;
- resolved skills and context inputs;
- permissions and observability policy;
- desired presence: present, suspended, or absent.

Higher layers ask for a healthy session. They do not ask to launch a process
imperatively.

### 4.2 Runtime-specific managed session plane

A managed session plane owns:

- create, resume, adopt, and replacement decisions;
- durable-to-runtime session binding;
- runtime-specific control actions;
- event capture and normalization;
- session-state observation;
- compatibility and drift evaluation.

Runtime-specific behavior stays behind this plane. The rest of MoonMind consumes
shared contracts and normalized observations.

### 4.3 Temporal orchestration

The intended workflow shape is:

- `MoonMind.UserWorkflow` owns the workflow envelope and ordered steps;
- `MoonMind.AgentSession` owns one workflow-scoped managed session entity;
- `MoonMind.AgentRun` owns one true agent execution step;
- `MoonMind.ManagedSessionReconcile` performs bounded reconciliation;
- `MoonMind.ProviderProfileManager` coordinates runtime capacity and cooldown;
- `MoonMind.OAuthSession` owns interactive authentication flows;
- Temporal-backed container jobs own bounded workload execution.

Workflow code remains deterministic. Docker, process, filesystem, network,
artifact, and database side effects occur in Activities or external services.

### 4.4 Artifact-first durability

Managed runtimes may retain native state for continuity, but MoonMind remains
authoritative through:

- artifacts;
- bounded workflow metadata;
- execution and observability projections;
- session continuity references;
- provider-profile and policy records.

A container is never the sole source of information required for recovery,
audit, rerun, or operator understanding.

---

## 5. Container roles

MoonMind treats the following as separate roles.

### 5.1 Managed session container

A managed session container is workflow-scoped and runtime-specific. It may hold:

- the runtime's session loop;
- runtime-local configuration;
- a mounted provider-auth home or resolved credentials;
- workspace bindings;
- runtime caches;
- session scratch and artifact spool state;
- runtime-native thread or conversation state.

It owns runtime continuity, not arbitrary workload-container lifecycle.

### 5.2 Workload container

A workload container is bounded non-agent execution for a build, test, lint,
helper, or similar job. It is submitted through the Docker Backend Service and
has its own job identity, resource policy, timeout, logs, artifacts, and cleanup.

A workload container is not part of managed-session identity merely because a
session requested it.

### 5.3 OAuth/authentication container

An OAuth container is short-lived and purpose-specific. It may write approved
authentication state to a durable auth volume. It is neither a managed session
nor a general workload container.

### 5.4 Role separation rule

The three roles remain distinct in code, labels, lifecycle, durable evidence, and
operator presentation. Sharing a Docker daemon for workload image caching does
not merge workload identity with managed-session identity.

---

## 6. Managed-session container-job capability

A managed session may need a containerized compiler, SDK, test runner, database,
or other tool. The desired-state route is the API-owned Docker Backend Service.

The session:

1. identifies the required image and command;
2. submits a typed asynchronous job through MoonMind tooling;
3. supplies a logical current-workspace reference rather than a host path;
4. receives a durable job identifier;
5. reads bounded status, logs, and artifact references;
6. continues the session after the job reaches a terminal state.

The session does **not** receive:

- the host Docker socket;
- `DOCKER_HOST` pointing at the deployment daemon;
- raw Docker API credentials;
- authority to inspect or mutate unrelated containers, images, networks, or
  volumes;
- arbitrary host bind-mount authority.

The Docker Backend Service uses the deployment-selected Docker daemon. The
current implementation target is the existing system Docker host or proxy. Its
image store survives session and workflow completion, so images acquired by one
job are reusable by later workflows.

MoonMind preserves safety through an API boundary rather than by giving the
managed runtime a private daemon. Authentication, authorization, logical
workspace resolution, resource ceilings, registry credentials, labels, cleanup,
and durable evidence remain MoonMind-owned.

---

## 7. Session lifecycle

### 7.1 Workflow scope

The default unit is a workflow-scoped managed session. It may:

- serve one step;
- serve multiple ordered steps;
- be cleared between steps;
- be replaced when policy or drift requires it;
- coexist with another session when isolation requires it.

Step boundaries remain first-class even when the runtime container is reused.

### 7.2 Identity and reset boundaries

The stable pattern is:

- one logical `session_id`;
- one `session_epoch` per continuity interval;
- one runtime-native container/session/thread identity;
- one active turn identity when relevant.

A clear or reset operation writes control and reset-boundary evidence, increments
the epoch, starts a new runtime thread or equivalent, clears the active turn, and
preserves the logical session identity.

### 7.3 Reconciliation

The managed session plane observes actual runtime state and reconciles toward
desired state. Reconciliation is idempotent and bounded. It may adopt a matching
healthy runtime, replace an incompatible one, or fail closed when ownership or
identity is ambiguous.

### 7.4 Termination

Termination stops the managed runtime, finalizes session evidence, releases
provider-profile capacity, and marks the session absent. Container jobs requested
by the session retain their own lifecycle and are canceled through their job
ownership rules, not by treating them as nested session state.

---

## 8. Context architecture

### 8.1 MoonMind owns context assembly

Managed sessions consume context assembled by MoonMind. Sources may include:

- workflow objectives and plan state;
- prior run history;
- long-term memory and conventions;
- document retrieval;
- attachments;
- immutable resolved skill snapshots;
- operator steering input.

### 8.2 Durable context versus runtime-local memory

Runtime-local context is useful but disposable. Durable context sources are
artifact-backed instructions, execution history, retrieval indexes, session
summaries, checkpoints, and resolved skill manifests.

A reset or replacement can rebuild sufficient context without treating the
runtime's local thread database as authoritative.

### 8.3 Skill delivery

MoonMind resolves an immutable active skill set before launch or turn execution.
Compact indices may be delivered in prompts, while full skill bodies are
materialized into stable workspace-visible paths. Large skill bodies do not enter
Temporal history.

### 8.4 Secret rule

Skill bodies, context packs, and prompt artifacts are not secret stores. Provider
credentials remain references until a controlled launch boundary materializes
them.

---

## 9. Live Logs and observability

Live Logs is not an interactive terminal. It is a merged, session-aware timeline
of:

- stdout and stderr;
- MoonMind system annotations;
- session lifecycle events;
- turn lifecycle events;
- approvals;
- checkpoint, summary, and reset-boundary publication;
- associated container-job state and artifact references.

Durable artifacts and bounded metadata remain authoritative. Live streaming is
an optional delivery path over the same evidence.

Observation and intervention stay separate. Pause, resume, clear, approve,
reject, cancel, and terminate use explicit Temporal-backed control surfaces.

---

## 10. Authentication and secrets

### 10.1 Provider Profiles

Provider Profiles own runtime and provider selection, credential source,
materialization mode, default model intent, command shaping, concurrency,
cooldown, and required auth references.

### 10.2 OAuth

Interactive runtime authentication uses a dedicated OAuth architecture and
short-lived PTY/WebSocket bridge. OAuth state is stored only in approved auth
volumes or secret backends.

### 10.3 Secret materialization

Durable contracts carry secret references, not raw secret values. Activities
resolve and materialize the minimum required credential close to execution.
Logs, artifacts, and outbound text apply redaction and scanning policy.

### 10.4 Container registry credentials

Container registry credentials are resolved by the Docker Backend Service for an
authorized job. They are not forwarded to the managed session. Authorization to
run a private image is checked on every job, including cache hits.

---

## 11. Security boundaries

Stable boundaries are:

- managed sessions do not receive deployment Docker authority;
- workload mount sources are resolved from authenticated logical workspace
  references;
- agent-provided host paths are rejected;
- provider and registry credentials are narrowly materialized;
- workload and session labels are MoonMind-owned;
- resources, network policy, timeout, and cleanup are externally enforced;
- artifacts and compact metadata, not container-local state, are durable truth;
- unsupported or ambiguous runtime input fails closed.

A raw Docker CLI is not the normal agent interface. Advanced internal Docker
operations remain separately gated and do not expose a socket to the agent.

---

## 12. Extensibility

A new managed runtime joins by implementing the shared session-plane contracts:

- launch or adopt;
- observe;
- send and control turns;
- clear/reset;
- terminate;
- publish normalized events and artifacts;
- declare provider-profile and workspace requirements.

It does not introduce another top-level orchestration model.

Container execution remains runtime-neutral. Codex, Claude, Omnigent-hosted
agents, and future runtimes call the same Docker Backend Service tools.

---

## 13. Document ownership map

- `SharedManagedAgentAbstractions.md`: runtime-neutral session contracts.
- `CodexManagedSessionPlane.md`: Codex session-plane architecture.
- `CodexCliManagedSessions.md`: detailed Codex CLI protocol and identity.
- `ClaudeCodeManagedSessions.md`: Claude managed-runtime/session direction.
- `LiveLogs.md`: session-aware observability.
- `OAuthTerminal.md`: interactive authentication transport.
- `DockerBackendService.md`: container jobs, Docker backend, workspace mounts,
  image reuse, and cleanup.
- `ProviderProfiles.md`: runtime/provider/credential target selection.
- `SecretsSystem.md`: durable secret references and resolution.

---

## 14. Final desired-state statement

MoonMind's managed-agent architecture is:

- managed-session first;
- Temporal-orchestrated;
- artifact-first;
- session-container based;
- context-assembled by MoonMind;
- observed through session-aware Live Logs;
- authenticated through Provider Profiles and controlled OAuth/secret systems;
- extended through runtime-specific session adapters;
- integrated with containerized tools through the API-owned Docker Backend
  Service.

Managed sessions, workload containers, and authentication containers remain
separate roles. Managed runtimes never need direct Docker daemon authority to run
containerized repository work.
