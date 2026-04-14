# Managed Agent Vector Embedding Workflow

Status: Desired state
Owners: MoonMind Engineering
Last Updated: 2026-04-14
Related:
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](../Temporal/WorkflowArtifactSystemDesign.md)

## Purpose

This document defines the desired-state approach for managed agent access to
MoonMind vector search and embedding services.

It is compatible with the Codex managed session plane:

- Codex is the concrete managed-session runtime.
- Each task has one task-scoped Codex session container.
- Session continuity is a performance and context cache, not durable truth.
- Temporal and MoonMind activities remain the orchestration control plane.
- Artifacts and bounded workflow metadata remain the operator/audit source of
  truth.
- Workload containers launched by control-plane tools remain outside managed
  session identity.

The vector system is a context data plane. It must improve retrieval and memory
without turning workflow history, session-local storage, or runtime home
directories into durable vector truth.

## Baseline

- Vector storage is Qdrant, exposed as a MoonMind-managed infrastructure service.
- Embedding generation is provider-backed and configured through MoonMind runtime
  settings.
- Temporal workflows coordinate work, retries, cancellation, and policy routing.
- Managed Codex sessions execute task steps through Codex App Server inside a
  task-scoped Docker container.
- Durable operator evidence is published through artifacts and bounded workflow
  metadata.

## Recommended Access Model

Use a three-surface model.

### 1. Control-Plane Vector Service

MoonMind-owned activities and API services own:

- collection creation and validation
- embedding provider selection
- embedding dimension checks
- Qdrant credentials and endpoint policy
- tenant, repository, task, and run scoping
- write authorization
- quota and rate enforcement
- audit summaries and artifact publication

This is the default surface for writes, embedding generation, and policy-sensitive
queries. Provider API keys should stay in MoonMind-owned service or activity
contexts unless a runtime has been explicitly authorized to receive a scoped
credential.

### 2. Managed Session Vector Tools

The Codex session container may receive bounded tools for vector retrieval. Those
tools should call the MoonMind control-plane vector surface by default, rather
than requiring the Codex process to know Qdrant topology or embedding-provider
secrets.

Session-visible vector tools should expose task-oriented operations such as:

- search indexed repository context
- search task memory
- request indexing for the active workspace
- report vector operation progress

The session container may cache short-lived query results for step continuity.
That cache is disposable and must not be treated as operator/audit truth.

### 3. Specialized Workload Containers

Large indexing jobs may run in separate workload containers launched through
control-plane tools, as described by the Docker-out-of-Docker model. These
containers can batch file parsing, chunking, embedding, and upsert work without
becoming part of managed session identity.

Rules:

- Workload containers do not create a new managed session.
- Workload container IDs do not replace `session_id`, `session_epoch`,
  `container_id`, `thread_id`, or `active_turn_id`.
- Workload outputs are summarized through artifacts and bounded metadata.
- Raw vectors and large chunk payloads are stored in Qdrant or blob artifacts, not
  Temporal workflow history.

## Direct Qdrant Access

Direct container-to-Qdrant access is not the default managed-session contract.

It is allowed only for an explicitly authorized high-throughput runtime path where
all of the following are true:

- the container receives a narrow service credential, not user credentials
- collection, tenant, repository, and run scopes are enforced before access
- embedding model and vector dimension compatibility are validated before query
  or upsert
- writes are idempotent or de-duplicated by stable chunk identifiers
- operation summaries are published as artifacts or bounded workflow metadata
- no embedding vectors or large document chunks are recorded in workflow history

For Codex managed sessions, direct Qdrant access should be rare. The preferred
shape is that Codex invokes a MoonMind vector tool, and that tool either serves
the request through the control plane or launches a separate workload container
for heavy batch work.

## Temporal Boundary

Temporal remains the orchestration control plane, not the vector data plane.

Workflow and activity payloads may carry:

- vector operation intent
- collection names or refs
- repository/task/run scope
- chunk counts and timing summaries
- artifact refs
- failure classification

Workflow and activity payloads must not carry:

- raw embedding arrays
- large chunk bodies
- full repository indexes
- provider secrets
- unbounded query result sets

Activities may heartbeat progress for long-running vector work, but heartbeat
details should remain compact. Full diagnostics belong in artifacts.

## Artifact Expectations

Every managed vector operation that affects task execution should leave durable
evidence through the artifact system.

Recommended artifact types include:

- vector operation summary
- indexing manifest
- query summary
- degraded retrieval diagnostic
- collection validation report

Artifacts should include counts, timings, scope, collection refs, model identity,
dimension, and failure summaries. They should not include raw provider secrets or
unbounded vector payloads.

## Security And Policy

Vector access must keep runtime identity, user identity, and service identity
separate.

Requirements:

- Session containers do not receive broad Qdrant admin credentials.
- Provider embedding credentials are not exposed to Codex by default.
- Runtime-visible tools enforce task and repository scope before access.
- Collection names or filters encode tenant/project boundaries where applicable.
- Unsupported embedding model or dimension values fail fast.
- Logs, artifacts, and diagnostics redact secret-like values.

## Operational Semantics

Vector retrieval is allowed to influence agent context, but it is not durable
session state.

Rules:

- A `clear_session` resets Codex thread continuity but does not delete persisted
  vector collections.
- A `cancel_session` or `terminate_session` stops in-flight vector work through
  normal Temporal cancellation or workload-container termination.
- Reused task-scoped session containers may retain disposable local retrieval
  caches, but those caches must be safe to lose.
- Recovery after worker restart uses workflow metadata, artifacts, vector service
  state, and the managed-session supervision index, not container-local vector
  cache state.

## Minimal Runtime Contract

A managed Codex session or workload vector tool needs only bounded configuration:

- MoonMind API endpoint for control-plane vector operations.
- Run, task, repository, and tenant scope.
- A scoped service token or adapter-issued capability, when required.
- Optional Qdrant endpoint and credential only for approved direct-access paths.
- Embedding model identity and expected vector dimension, supplied by MoonMind
  configuration or collection metadata.

This keeps vector search compatible with the Codex managed session plane while
preserving Temporal as the orchestrator, artifacts as durable evidence, and
session containers as replaceable runtime envelopes.
