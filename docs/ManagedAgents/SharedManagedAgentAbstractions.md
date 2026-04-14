# Shared Managed Agent Abstractions

## Status

This document describes the **new desired state** for managed agents.

- **Codex runtime:** implemented in the project and is the reference implementation for this model.
- **Claude Code runtime:** not yet implemented, but should fit the same abstraction.
- **Gemini CLI runtime:** not yet implemented, but should fit the same abstraction.

This document supersedes the older, more imperative framing where a “managed agent” was treated as a runtime-specific thing to start, stop, and hold directly. The new model is **declarative** and **session-plane based**.

## Executive summary

A managed agent is no longer the runtime object.

A managed agent is now the **declarative intent** that some long-lived, resumable, observable work session should exist for a given runtime, workspace, role, and policy.

The runtime-specific owner of that session is a **managed session plane**.

A managed session plane is responsible for:

- reconciling desired state into actual runtime sessions,
- resuming or adopting existing sessions when appropriate,
- creating new sessions when necessary,
- observing session lifecycle and streamed events,
- surfacing normalized status back to the rest of the system,
- handling runtime-specific control operations such as interrupt, resume, steer, or delete.

This gives us one shared abstraction for Codex now and for Claude Code and Gemini CLI later, without forcing higher layers to care about runtime-specific session mechanics.

## Why the abstraction changed

The earlier abstraction worked only as long as the project effectively had one concrete runtime model in mind.

That model breaks down when we want to support multiple agent runtimes that differ in how they:

- start and resume sessions,
- persist transcripts and state,
- expose streaming events,
- request approvals,
- support workspace isolation,
- handle subagents or delegated work,
- represent interruption, completion, and failure.

Trying to model all of that directly in a top-level “managed agent” abstraction causes three problems:

1. **Runtime leakage**
   Higher layers become full of Codex-specific, Claude-specific, or Gemini-specific branching.

2. **Imperative coupling**
   The system starts reasoning in terms of “launch this agent process now” instead of “reconcile this desired capability into an owned session.”

3. **Poor portability**
   Every new runtime needs a new family of top-level concepts instead of one shared contract.

The session-plane model fixes this by making the shared layer own **intent, policy, identity, and status**, while each runtime plane owns **session realization and runtime control**.

## The new conceptual model

There are four key concepts:

### 1. Managed agent

A **managed agent** is a logical, durable workload identity.

It describes:

- who or what the session is for,
- which runtime should back it,
- which workspace and profile it should use,
- what policies apply,
- what state is desired.

A managed agent is **not** a process handle, thread handle, CLI process id, or runtime session id.

### 2. Managed session

A **managed session** is the concrete runtime-owned session that does the work.

Examples:

- a Codex thread/session bound to a workspace and agent profile,
- a future Claude Code session,
- a future Gemini CLI session.

A managed session is always owned by exactly one session plane.

### 3. Managed session plane

A **managed session plane** is the runtime-specific control and reconciliation layer.

It is responsible for translating a shared managed-agent spec into runtime-native session operations.

A plane owns:

- create / resume / adopt decisions,
- session binding,
- event subscription,
- status observation,
- lifecycle control,
- runtime capability reporting.

### 4. Desired state + observed state

The system should think in terms of reconciliation:

- **desired state** says what should exist,
- **observed state** says what the runtime currently has,
- the plane closes the gap.

That means higher layers stop asking “how do I launch Codex?” and instead ask “ensure this managed agent is present and healthy.”

## Shared abstraction boundaries

### Shared layer responsibilities

The shared abstraction layer owns:

- the managed-agent identity model,
- desired-state shape,
- normalized status and conditions,
- reconciliation contract,
- runtime capability contract,
- opaque session references,
- event normalization model,
- policy surfaces that higher layers care about.

### Session-plane responsibilities

A runtime plane owns:

- runtime-specific session creation and resumption,
- adoption/import of existing sessions when supported,
- mapping shared policies to runtime-native settings,
- streaming and event translation,
- handling runtime quirks,
- determining whether a change is in-place, deferred, or replacement-requiring.

### Runtime adapter responsibilities

Inside a plane, a lower-level adapter may still exist for concrete API or CLI calls. That is an implementation detail.

The rest of the system should depend on the plane contract, not on raw runtime adapters.

## Core shared abstractions

The names below are conceptual. Exact type names can vary, but the shape should remain the same.

### ManagedAgentSpec

A managed agent spec is the desired-state object.

```text
ManagedAgentSpec
  id
  runtime
  desiredState            # present | suspended | absent
  workspace
  profile
  bootstrap
  sessionPolicy
  permissions
  observability
  metadata
```

Recommended contents:

- `id`: stable logical identity.
- `runtime`: `codex`, `claude-code`, `gemini-cli`, or another supported runtime kind.
- `desiredState`:
  - `present` means a session should exist and be available for work.
  - `suspended` means no active session is required, but resumable state may be retained.
  - `absent` means the binding should be removed and retention policy applied.
- `workspace`: repo root, worktree, sandbox root, or equivalent scope.
- `profile`: role, skill bundle, template, or policy profile.
- `bootstrap`: initial instructions, attachments, references, or setup guidance.
- `sessionPolicy`: reuse, retention, replacement, isolation, resume, and steering policies.
- `permissions`: approval, sandbox, network, filesystem, and escalation defaults.
- `observability`: whether to retain transcripts, stream events, publish deltas, etc.
- `metadata`: labels and non-runtime business metadata.

### ManagedSessionRef

A managed session ref is an **opaque reference** to a concrete runtime session.

```text
ManagedSessionRef
  runtime
  planeId
  nativeSessionId
```

Important rule: higher layers may store and pass this reference around, but should not depend on the structure or semantics of `nativeSessionId`.

### ManagedSessionBinding

A binding connects a logical managed agent to a concrete session.

```text
ManagedSessionBinding
  managedAgentId
  sessionRef
  bindingMode             # created | resumed | adopted
  createdAt
  updatedAt
  compatibilityHash
```

The compatibility hash is useful when deciding whether an existing session still satisfies the current desired state.

### ManagedSessionObservation

This is the normalized snapshot of what the plane currently knows.

```text
ManagedSessionObservation
  sessionRef
  phase
  readiness
  conditions
  lastActivityAt
  lastTurnSummary
  pendingApproval
  terminalReason
  runtimeDetails
```

Recommended normalized phases:

- `pending`
- `reconciling`
- `ready`
- `running`
- `waitingForInput`
- `awaitingApproval`
- `interrupted`
- `completed`
- `failed`
- `deleted`

Planes can retain richer runtime-native detail under `runtimeDetails`, but the shared layer should prefer the normalized fields.

### ManagedSessionPlaneCapabilities

Capabilities are how runtimes differ **without** changing the top-level abstraction.

```text
ManagedSessionPlaneCapabilities
  supportsResume
  supportsAdoptExisting
  supportsInterrupt
  supportsSteer
  supportsStructuredEvents
  supportsApprovalCallbacks
  supportsWorkspaceOverride
  supportsFork
  supportsSubagents
  supportsEphemeralSessions
```

This is the main extension mechanism for runtime differences.

### ManagedSessionPlane

This is the core runtime-owned contract.

```text
ManagedSessionPlane
  runtimeKind
  capabilities

  reconcile(spec) -> ManagedSessionBinding + ManagedSessionObservation
  get(sessionRef) -> ManagedSessionObservation
  watch(sessionRef) -> stream<ManagedSessionEvent>
  sendInput(sessionRef, input)
  interrupt(sessionRef)
  suspend(spec or sessionRef)
  delete(spec or sessionRef)
```

Exact method names are not important. The important thing is that the shared layer talks to a plane in terms of **reconciliation and observation**, not raw process management.

## Desired-state examples

### Minimal example

```yaml
managedAgents:
  - id: backend-implementer
    runtime: codex
    desiredState: present
    workspace:
      root: ./src/MyService
    profile: implementation
```

### More complete example

```yaml
managedAgents:
  - id: backend-implementer
    runtime: codex
    desiredState: present
    workspace:
      root: ./src/MyService
      isolation: worktree
    profile: implementation
    bootstrap:
      instructions: |
        Work only in the service and tests projects.
        Keep diffs scoped.
        Run relevant tests before marking work complete.
      references:
        - docs/Architecture/ServiceBoundaries.md
        - docs/ManagedAgents/CodexCliManagedSessions.md
    sessionPolicy:
      reuse: resume-or-create
      retention: keep-history
      replacement: compatible-only
    permissions:
      approvalPolicy: project-default
      sandboxPolicy: workspace-write
      networkPolicy: restricted
    observability:
      publishEvents: true
      retainTranscript: true
    metadata:
      team: payments
      purpose: feature-delivery
```

This same shape should remain valid when `runtime` becomes `claude-code` or `gemini-cli`.

That is the point of the abstraction.

## Reconciliation model

The shared system should treat session management as a reconciliation loop.

### Reconcile algorithm at a high level

For a given `ManagedAgentSpec`:

1. Select the plane using `spec.runtime`.
2. Check whether a compatible binding already exists.
3. If a compatible session exists:
   - reuse it,
   - resume or attach if necessary,
   - refresh observation.
4. Otherwise, if the plane supports adoption and an adoptable session exists:
   - adopt it,
   - record binding mode as `adopted`.
5. Otherwise:
   - create a new runtime-native session,
   - bind it,
   - publish observation.
6. Subscribe to runtime events and normalize them.
7. Persist updated binding and status.

### Idempotency requirement

`reconcile(spec)` must be safe to call repeatedly.

Repeated reconciliation with no meaningful spec drift should not create duplicate sessions.

### Compatibility and replacement

Some spec changes can be applied in place. Others require replacement.

Typical examples:

- **Usually replacement-requiring**
  - runtime kind
  - workspace root or isolation mode
  - identity-defining profile changes
- **Potentially in-place or next-turn only**
  - approval policy
  - model or reasoning defaults
  - steering instructions
  - observability toggles

Each plane decides this using its runtime semantics, but the result should be surfaced consistently through shared conditions.

Suggested shared conditions:

- `Bound`
- `Compatible`
- `RequiresReplacement`
- `AwaitingApproval`
- `Suspended`
- `Terminal`
- `DriftDetected`

## Session lifecycle semantics

The shared layer should reason about lifecycle in a runtime-neutral way.

### Present

The session should exist and be available.

- The plane may create, resume, or adopt.
- The session may be idle or active.
- Higher layers care that it is bound and usable.

### Suspended

The session does not need to be actively loaded or executing.

- Binding and transcript may be retained.
- The plane may unload, detach, or simply mark the session as not expected to be active.
- Resume semantics remain runtime-specific.

### Absent

The session should no longer be managed.

- The plane removes the binding.
- Runtime-native deletion, archival, or transcript retention is controlled by policy.

## Normalized event model

Planes should translate runtime-specific streams into a shared event surface where possible.

Suggested normalized events:

- `SessionCreated`
- `SessionResumed`
- `SessionAdopted`
- `SessionReady`
- `TurnStarted`
- `MessageDelta`
- `ToolCallStarted`
- `ToolCallCompleted`
- `ApprovalRequested`
- `ApprovalResolved`
- `SessionInterrupted`
- `SessionCompleted`
- `SessionFailed`
- `SessionDeleted`

Raw runtime-native events can still be attached as extended details for diagnostics.

## Codex as the reference implementation

Codex is the first implemented runtime for this model and should be treated as the reference session-plane implementation, not as a special-case top-level abstraction.

### Codex mapping

In the Codex runtime, the plane maps the shared abstractions to Codex-native session and turn concepts.

Conceptually:

- the managed agent becomes desired state for a Codex-backed session,
- the plane owns create / resume / attach decisions,
- a bound session is represented by a Codex-native session or thread reference,
- user-visible work is driven through turns,
- streaming runtime activity is normalized into shared observation and events,
- interrupt, steer, approval, and completion flow through the plane contract.

### What the Codex plane should own

The Codex plane should own all Codex-specific concerns, including:

- how a session is created or resumed,
- how workspace and policy settings are mapped,
- how event streams are subscribed to,
- how approval and interruption are bridged,
- how compatibility is determined,
- how runtime-native ids are persisted and recovered.

Higher layers should not need to know whether Codex realizes this through app-server threads, CLI session artifacts, or another Codex-specific substrate.

### Subagents in Codex

Codex has runtime-native support for delegated or parallel agent work. That does **not** change the top-level abstraction.

The shared model should treat runtime-native subagents as one of two things:

1. **internal runtime behavior** of a managed session, or
2. **explicit child sessions** only if the plane intentionally projects them outward.

Do not make subagents part of the base shared managed-agent abstraction.

## Planned Claude Code and Gemini CLI support

Claude Code and Gemini CLI should be added by implementing new planes, not by inventing new top-level managed-agent abstractions.

### Required design rule

Do **not** introduce concepts like:

- `ClaudeManagedAgent`
- `GeminiManagedAgent`
- `ClaudeSessionManager` as a new top-level abstraction
- `GeminiManagedRuntime` as a peer to the shared managed-agent model

Instead, add runtime-specific managed session planes documented under the `*ManagedSessions.md` naming convention, for example:

- [`docs/ManagedAgents/ClaudeCodeManagedSessions.md`](./ClaudeCodeManagedSessions.md)
- `docs/ManagedAgents/GeminiCliManagedSessions.md`

Both should consume the same `ManagedAgentSpec` shape and return the same shared binding, observation, and event model.

### What is allowed to vary by runtime

Claude Code and Gemini CLI will vary in:

- how sessions are started and resumed,
- what can be controlled mid-session,
- what event structure is available,
- how approvals and permissions behave,
- whether adoption or fork is possible,
- how persistent state is stored locally or remotely.

Those differences belong behind the plane contract and capability flags.

### Declarative-first requirement

For future runtimes, implementation should start from the same desired-state workflow:

1. define a shared spec,
2. select a plane,
3. reconcile to a runtime session,
4. normalize observation,
5. expose runtime-specific gaps through capabilities and conditions.

That is the intended long-term architecture.

## What should no longer be modeled at the shared layer

The following patterns are now considered the wrong abstraction direction:

- treating a managed agent as a directly launched runtime process,
- exposing runtime-native session ids as first-class shared identifiers,
- baking Codex-specific lifecycle calls into top-level abstractions,
- requiring separate orchestration paths for each runtime,
- coupling shared policy code to runtime-specific event payloads,
- assuming one managed agent must always equal one always-live process.

## Recommended invariants

These invariants should hold across all implementations.

1. **Managed agents are declarative.**
   They describe desired capability, not runtime handles.

2. **Planes own runtime realization.**
   Create, resume, adopt, attach, interrupt, and delete are plane responsibilities.

3. **Bindings are opaque.**
   Only the owning plane interprets native session ids.

4. **Reconciliation is idempotent.**
   Repeated reconcile calls should converge, not duplicate work.

5. **Status is normalized.**
   Higher layers consume shared phases and conditions first.

6. **Runtime differences use capabilities, not forks in the abstraction model.**
   New runtimes should extend the same contract.

7. **Replacement is explicit.**
   Incompatible drift should surface clearly rather than silently mutating sessions in undefined ways.

## Migration guidance from the previous model

When updating code or docs that still use the older framing, apply the following rewrite mentally:

### Old framing

- “A managed agent is a runtime-specific managed process or session.”
- “The manager launches the agent.”
- “The system stores the agent handle.”
- “Supporting a new runtime needs a new top-level abstraction.”

### New framing

- “A managed agent is declarative desired state.”
- “A session plane reconciles that desired state into a runtime-owned session.”
- “The system stores a binding and normalized observation.”
- “Supporting a new runtime means implementing a new plane.”

## Relationship to other docs

This document is the shared, runtime-neutral abstraction layer.

It should be read together with runtime-specific documents such as:

- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/ClaudeCodeManagedSessions.md`](./ClaudeCodeManagedSessions.md)

Future runtime docs should follow the same pattern:

- `docs/ManagedAgents/GeminiCliManagedSessions.md`

Those runtime docs should explain **how** their plane realizes the shared contract, not redefine the shared contract itself. Each runtime doc should include an explicit mapping from `ManagedAgentSpec`, `ManagedSessionBinding`, `ManagedSessionObservation`, normalized phases, and shared plane verbs to that runtime's native concepts.

## Final desired-state statement

The project’s desired state is:

- **managed agents are modeled declaratively,**
- **managed sessions are owned by runtime-specific managed session planes,**
- **Codex is the first concrete implementation of that model,**
- **Claude Code and Gemini CLI should be added by implementing additional planes against the same shared abstractions.**

That is the architecture this document defines.
