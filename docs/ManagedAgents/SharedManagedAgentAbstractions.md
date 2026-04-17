# Shared Managed Agent Abstractions

- **Status:** Desired state
- **Audience:** Runtime authors, workflow authors, adapter authors, and Mission Control authors
- **Purpose:** Runtime-neutral contract layer underneath the managed-agent architecture entrypoint
- **Last updated:** 2026-04-14

**Related:**
- [`docs/ManagedAgents/ManagedAgentArchitecture.md`](./ManagedAgentArchitecture.md)
- [`docs/ManagedAgents/CodexManagedSessionPlane.md`](./CodexManagedSessionPlane.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/LiveLogs.md`](./LiveLogs.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/Tools/SkillSystem.md`](../Tools/SkillSystem.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

## 1. Summary

This document defines the shared, runtime-neutral contract for MoonMind managed agents.

The subsystem architecture is defined by [`ManagedAgentArchitecture.md`](./ManagedAgentArchitecture.md). This document sits directly underneath that entrypoint and defines the common abstractions that runtime-specific managed session planes must implement.

The core model is:

- a **managed agent** is declarative desired state,
- a **managed session** is the runtime-owned realization of that desired state,
- a **managed session plane** reconciles desired state into observed runtime state,
- higher layers store and exchange bindings, observations, events, and artifact refs,
- runtime-native process, thread, container, transcript, and session identifiers remain opaque outside the owning plane.

Codex is the current concrete reference implementation. Claude Code, Gemini CLI, and future managed runtimes should implement the same shared contract through their own runtime-specific planes.

---

## 2. Contract boundary

### 2.1 Shared layer owns

The shared managed-agent layer owns the runtime-neutral vocabulary and payload shapes for:

- managed-agent desired state,
- managed-session references and bindings,
- normalized observations,
- phases, readiness, and conditions,
- runtime capability flags,
- normalized event envelopes,
- input and control command envelopes,
- reconciliation semantics,
- compatibility and replacement signaling.

Workflow code, Mission Control, and cross-runtime orchestration should depend on these shared surfaces first.

### 2.2 Managed session planes own

A managed session plane owns runtime-specific realization, including:

- creating, resuming, adopting, suspending, resetting, and deleting sessions,
- mapping shared policy into runtime-native launch or control settings,
- attaching workspace, provider profile, resolved skill, and context materialization inputs,
- interpreting runtime-native session and turn identifiers,
- subscribing to runtime-native streams,
- translating runtime events into normalized MoonMind events,
- deciding whether spec drift is in-place, deferred, or replacement-requiring.

The plane may delegate concrete CLI/API calls to lower-level adapters. Those adapters are implementation details, not shared architecture concepts.

### 2.3 Outside this contract

This document does not own:

- the top-level managed-agent subsystem architecture,
- the Codex-specific session protocol,
- Provider Profile schemas and auth flows,
- secret backend schemas and resolver implementations,
- the skill-resolution algorithm,
- the Docker workload-container contract,
- external delegated agent execution.

Those details belong in the related subsystem documents. Shared managed-agent payloads may reference those systems, but they must not inline their large bodies or raw secret values.

---

## 3. Normative terms

### 3.1 Managed agent

A **managed agent** is a stable logical identity plus desired state for an owned runtime work session.

A managed agent is not:

- a process handle,
- a container id,
- a CLI session id,
- a provider thread id,
- a transcript path,
- an always-live runtime object.

Higher layers request that a managed agent be reconciled. They do not directly launch runtime processes.

### 3.2 Managed session

A **managed session** is the concrete runtime-owned work session that satisfies a managed-agent spec.

A managed session may have runtime-native state, local caches, transcripts, thread ids, or container state. Those details are owned by the plane. Durable MoonMind truth remains in workflow state, artifacts, bounded metadata, and read models.

### 3.3 Managed session plane

A **managed session plane** is the runtime-specific control and reconciliation layer.

Each runtime kind has exactly one owning plane for a given managed session. The plane is the only component that may interpret native session identifiers or decide how shared operations map to runtime-native behavior.

### 3.4 Desired state and observed state

Managed-agent execution is reconciliation-based:

- **desired state** says what MoonMind wants to exist,
- **observed state** says what the runtime currently has,
- the plane closes the gap and reports a normalized observation.

Reconciliation must be idempotent. Repeating the same reconcile request without meaningful spec drift must converge on the same binding rather than creating duplicate sessions.

---

## 4. Core contract objects

The names below are the normative documentation names for the shared model. Implementation type names may vary while Codex remains the only concrete runtime, but their semantics must stay aligned with this contract.

### 4.1 `ManagedAgentSpec`

`ManagedAgentSpec` is the desired-state object.

```text
ManagedAgentSpec
  id
  runtime
  desiredState
  workspace
  providerProfileRef
  agentProfileRef
  contextRefs
  resolvedSkillSetRef
  sessionPolicy
  permissions
  observability
  metadata
```

Field requirements:

- `id` is the stable logical managed-agent identity. It must not be derived from a runtime-native session id.
- `runtime` selects the managed session plane, such as `codex`, `claude-code`, or `gemini-cli`. Unsupported runtime values must fail fast.
- `desiredState` is one of `present`, `suspended`, or `absent`.
- `workspace` defines the repo, worktree, sandbox root, or equivalent scope to bind into the session.
- `providerProfileRef` references the Provider Profile used for runtime/provider selection and launch shaping. It must not contain raw credentials.
- `agentProfileRef` references role, behavior, or task-template policy when applicable.
- `contextRefs` references context packs, attachments, memory snapshots, retrieval results, or instruction artifacts. Large context bodies belong behind refs.
- `resolvedSkillSetRef` references the immutable resolved skill snapshot selected for the run or step.
- `sessionPolicy` defines reuse, retention, reset, replacement, isolation, resume, and steering behavior.
- `permissions` defines approval, sandbox, network, filesystem, and escalation defaults.
- `observability` defines event publishing, transcript retention, diagnostic capture, and live-follow policy.
- `metadata` is non-secret business metadata for routing, display, and correlation.

### 4.2 Desired-state values

`present` means a compatible session should exist and be usable for work.

The plane may create, resume, or adopt a session. The resulting observation should make readiness explicit.

`suspended` means no active execution is required, but resumable state may be retained according to policy.

The plane may unload, detach, stop polling, or preserve runtime-native state. Resume behavior remains plane-specific.

`absent` means the managed session binding should be removed and retention policy applied.

The plane should delete, archive, or detach runtime-native state according to policy and report a terminal observation.

### 4.3 `ManagedSessionRef`

`ManagedSessionRef` is an opaque reference to a runtime-owned session.

```text
ManagedSessionRef
  runtime
  planeId
  sessionId
  sessionEpoch
  nativeRef
```

Rules:

- `runtime` and `planeId` identify the owning plane.
- `sessionId` is the logical MoonMind session identity visible to shared systems.
- `sessionEpoch` identifies the current continuity interval.
- `nativeRef` is an opaque plane-owned reference. Higher layers may store and pass it back to the same plane, but must not parse it or depend on its structure.

### 4.4 `ManagedSessionBinding`

`ManagedSessionBinding` connects a logical managed agent to a concrete session.

```text
ManagedSessionBinding
  managedAgentId
  sessionRef
  bindingMode
  compatibilityFingerprint
  createdAt
  updatedAt
  expiresAt
  retentionPolicy
```

`bindingMode` is one of:

- `created`,
- `resumed`,
- `adopted`.

The `compatibilityFingerprint` is the plane-computed value used to detect whether an existing session still satisfies the current spec. It must not include raw secrets or large context bodies.

### 4.5 `ManagedSessionObservation`

`ManagedSessionObservation` is the normalized snapshot of what the plane currently knows.

```text
ManagedSessionObservation
  sessionRef
  phase
  readiness
  conditions
  lastActivityAt
  activeTurnRef
  pendingApproval
  continuityRefs
  terminalReason
  runtimeDetails
```

Shared phases:

- `pending`
- `reconciling`
- `ready`
- `running`
- `waitingForInput`
- `awaitingApproval`
- `interrupted`
- `completed`
- `failed`
- `suspended`
- `deleted`

`runtimeDetails` may contain bounded runtime-native diagnostic metadata. It must not contain raw credentials, full transcripts, large skill bodies, or large prompt/context payloads.

### 4.6 `ManagedSessionCondition`

Conditions provide stable cross-runtime status details.

```text
ManagedSessionCondition
  type
  status
  reason
  message
  lastTransitionAt
```

Recommended shared condition types:

- `Bound`
- `Ready`
- `Compatible`
- `DriftDetected`
- `RequiresReplacement`
- `AwaitingApproval`
- `Suspended`
- `Terminal`
- `Recoverable`
- `Degraded`

Runtime-specific conditions may be added when needed, but shared consumers should be able to make normal routing and display decisions from shared condition types.

### 4.7 `ManagedSessionPlaneCapabilities`

Capabilities describe runtime differences without changing the top-level abstraction.

```text
ManagedSessionPlaneCapabilities
  supportsResume
  supportsAdoptExisting
  supportsInterrupt
  supportsSteer
  supportsReset
  supportsStructuredEvents
  supportsApprovalCallbacks
  supportsWorkspaceOverride
  supportsFork
  supportsSubagents
  supportsEphemeralSessions
  supportsLiveFollow
```

Capability flags are descriptive contract data. They must not create silent fallback behavior. If a caller requests an unsupported operation, the plane should fail fast with a normalized error and observation update.

---

## 5. Plane contract

A managed session plane exposes the runtime-owned reconciliation and control surface.

```text
ManagedSessionPlane
  runtimeKind
  capabilities

  reconcile(spec) -> ManagedSessionBinding + ManagedSessionObservation
  get(sessionRef) -> ManagedSessionObservation
  watch(sessionRef) -> stream<ManagedSessionEvent>
  sendInput(sessionRef, input) -> ManagedSessionObservation
  steer(sessionRef, input) -> ManagedSessionObservation
  interrupt(sessionRef, control) -> ManagedSessionObservation
  resolveApproval(sessionRef, approval) -> ManagedSessionObservation
  reset(sessionRef, control) -> ManagedSessionBinding + ManagedSessionObservation
  suspend(spec or sessionRef) -> ManagedSessionObservation
  delete(spec or sessionRef) -> ManagedSessionObservation
```

Exact method names can vary, but the shared boundary must remain reconciliation and observation based. Shared callers should not depend on raw process control operations.

Plane operations must:

- validate the runtime kind before acting,
- authorize workspace, context, skill, and credential materialization,
- keep raw secrets out of durable payloads,
- publish normalized observations after meaningful state changes,
- preserve idempotency for retryable workflow activity execution,
- report unsupported operations explicitly.

---

## 6. Reconciliation semantics

### 6.1 Reconcile flow

For a `ManagedAgentSpec`, reconciliation follows this shape:

1. Select the plane using `spec.runtime`.
2. Load any existing binding for `spec.id`.
3. Compare the binding compatibility fingerprint with the current spec.
4. If the binding is compatible, resume, attach, or refresh observation.
5. If no compatible binding exists and adoption is supported, adopt an eligible runtime-native session.
6. If adoption is unavailable or unsuitable, create a new runtime-native session.
7. Persist the binding and normalized observation.
8. Publish normalized events and artifact refs for operator visibility.

### 6.2 Idempotency

`reconcile(spec)` must be safe under workflow retries.

Repeated reconcile calls for the same desired state must not:

- create duplicate runtime sessions,
- leak duplicate auth or workspace mounts,
- duplicate durable artifacts except where append-only event capture intentionally records a retry,
- advance session epochs without an explicit reset or replacement boundary.

### 6.3 Compatibility and replacement

Some spec drift may be applied in place. Other drift requires a replacement session.

Usually replacement-requiring changes include:

- runtime kind,
- workspace root or isolation mode,
- identity-defining Provider Profile changes,
- incompatible resolved skill set materialization policy,
- incompatible permission or sandbox policy.

Potentially in-place or next-turn changes include:

- steering instructions,
- approval policy refinements,
- observability toggles,
- model or reasoning defaults when the runtime supports them without changing provider identity.

The plane decides using runtime semantics, but it must surface the outcome through `Compatible`, `DriftDetected`, and `RequiresReplacement` conditions.

### 6.4 Reset and session epochs

A reset or clear operation starts a new continuity interval.

When a plane resets a session, it must:

- record an explicit reset boundary,
- increment `sessionEpoch`,
- preserve or replace `sessionId` according to policy,
- publish an observation that makes the new continuity interval visible,
- keep durable context, artifacts, and read models authoritative outside the runtime container.

---

## 7. Input and control envelopes

### 7.1 `ManagedSessionInput`

Input sent to a session should be artifact-first.

```text
ManagedSessionInput
  turnId
  instructions
  instructionRef
  contextRefs
  attachmentRefs
  resolvedSkillSetRef
  permissions
  metadata
```

Rules:

- Inline `instructions` should remain bounded.
- Large prompt, context, attachment, or skill bodies should use refs.
- Secret values must not appear in input envelopes.
- Runtime-specific input details belong in the plane or adapter, not in shared workflow logic.

### 7.2 Control operations

Shared control operations include:

- `steer`,
- `interrupt`,
- `resolveApproval`,
- `reset`,
- `suspend`,
- `delete`.

Control operations should be surfaced as explicit workflow signals, updates, or activity calls. Live Logs remains an observation surface; it must not become the authority for interventions.

---

## 8. Normalized event model

Planes translate runtime-native streams into normalized events.

```text
ManagedSessionEvent
  eventId
  sessionRef
  sequence
  timestamp
  type
  severity
  turnRef
  payload
  payloadRef
  redactionState
  runtimeDetails
```

Rules:

- Events must be ordered per session where the runtime exposes enough ordering information.
- Large payloads should be stored as artifacts and referenced through `payloadRef`.
- Secret-like values must be redacted before events are stored or published.
- Runtime-native details may be attached only as bounded diagnostics.

Recommended shared event types:

- `SessionCreated`
- `SessionResumed`
- `SessionAdopted`
- `SessionReady`
- `SessionReset`
- `TurnStarted`
- `MessageDelta`
- `ToolCallStarted`
- `ToolCallCompleted`
- `ApprovalRequested`
- `ApprovalResolved`
- `SessionInterrupted`
- `SessionSuspended`
- `SessionCompleted`
- `SessionFailed`
- `SessionDeleted`

Runtime-specific events may be projected as diagnostics, but shared consumers should prefer normalized event types.

---

## 9. Durability and payload discipline

The shared contract must keep durable workflow payloads compact.

Durable shared state may include:

- `ManagedAgentSpec` with refs,
- `ManagedSessionBinding`,
- `ManagedSessionObservation`,
- normalized event metadata,
- artifact refs,
- continuity refs,
- bounded runtime diagnostics.

Durable shared state must not include:

- raw provider credentials,
- full auth homes,
- private keys or tokens,
- full transcripts by default,
- large skill bodies,
- large context packs,
- runtime-native cache directories,
- broad worker environment dumps.

When a runtime needs files, credentials, context, or skills, materialization happens at a controlled activity or adapter boundary. Workflow history carries refs and bounded metadata only.

---

## 10. Skill, context, auth, and secret references

Shared managed-agent contracts reference adjacent systems without taking ownership of them.

### 10.1 Skills

`resolvedSkillSetRef` points to an immutable resolved active skill snapshot.

The runtime adapter may materialize that snapshot into the stable runtime-visible path defined by the skill subsystem, such as `.agents/skills`, but the shared managed-agent contract carries the ref rather than embedding skill file contents.

### 10.2 Context

`contextRefs` and `attachmentRefs` point to artifact-backed or retrieval-backed context selected by MoonMind.

Managed sessions consume context assembled by MoonMind. Runtime-local memory is a continuity cache, not the durable context source of truth.

### 10.3 Provider profiles and auth

`providerProfileRef` points to the Provider Profile used for runtime/provider selection, launch shaping, capacity coordination, and credential source selection.

OAuth volumes, generated runtime configs, environment bundles, and other auth materialization details stay behind Provider Profile and runtime adapter boundaries.

### 10.4 Secrets

Secret values are never part of the shared managed-agent contract.

Shared payloads may carry `SecretRef` values only where a downstream controlled resolver boundary requires them. Resolution must happen late and materialize only the minimum runtime-required surface.

---

## 11. Runtime extension rules

New managed runtimes must extend the shared model by adding a runtime-specific managed session plane.

They must not introduce:

- runtime-specific managed-agent root concepts,
- runtime-specific peer architectures,
- direct shared dependencies on native session ids,
- workflow branches that bypass the shared binding, observation, and event model,
- special top-level orchestration paths for one runtime.

Runtime differences belong in:

- the plane implementation,
- capability flags,
- conditions,
- bounded `runtimeDetails`,
- runtime-specific docs.

Codex-specific behavior belongs in Codex-managed-session docs. Claude Code, Gemini CLI, and future runtime docs should describe how their planes realize this contract rather than redefining it.

---

## 12. Required invariants

The following invariants must hold across managed session planes:

1. Managed agents are declarative desired state, not runtime handles.
2. Runtime realization belongs to exactly one owning plane per session.
3. Native session identifiers are opaque outside the owning plane.
4. Reconciliation is idempotent under workflow retries.
5. Session replacement and reset boundaries are explicit.
6. Normalized observations are available for shared routing and UI display.
7. Runtime differences are exposed through capabilities and conditions, not forked abstractions.
8. Large bodies and raw secrets stay out of workflow history and shared durable payloads.
9. Context and skills are referenced and materialized at controlled boundaries.
10. Managed session containers remain continuity caches, not durable truth.

---

## 13. Document relationship

Use the managed-agent docs as follows:

- [`ManagedAgentArchitecture.md`](./ManagedAgentArchitecture.md) is the subsystem architecture entrypoint.
- [`SharedManagedAgentAbstractions.md`](./SharedManagedAgentAbstractions.md) is this runtime-neutral shared contract layer.
- [`CodexManagedSessionPlane.md`](./CodexManagedSessionPlane.md) is the current Codex runtime-specific architecture entrypoint.
- [`CodexCliManagedSessions.md`](./CodexCliManagedSessions.md) defines Codex-specific session details.
- [`LiveLogs.md`](./LiveLogs.md) defines session-aware observability.

Canonical docs should stay focused on target architecture and contracts. Migration notes, rollout sequencing, and incomplete implementation checklists belong under `docs/tmp/`.
