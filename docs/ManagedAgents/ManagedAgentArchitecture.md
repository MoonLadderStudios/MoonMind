# Managed Agent Architecture

- **Status:** Desired state
- **Audience:** Contributors, operators, runtime authors, and integration authors
- **Purpose:** Subsystem architecture entrypoint for MoonMind managed agents and managed sessions
- **Last updated:** 2026-04-14

**Related:**
- [`docs/MoonMindArchitecture.md`](../MoonMindArchitecture.md)
- [`docs/ManagedAgents/SharedManagedAgentAbstractions.md`](./SharedManagedAgentAbstractions.md)
- [`docs/ManagedAgents/CodexManagedSessionPlane.md`](./CodexManagedSessionPlane.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/LiveLogs.md`](./LiveLogs.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/ManagedAgents/OAuthTerminal.md`](./OAuthTerminal.md)
- [`docs/Security/ProviderProfiles.md`](../Security/ProviderProfiles.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/Tasks/AgentSkillSystem.md`](../Tasks/AgentSkillSystem.md)
- [`docs/Memory/MemoryArchitecture.md`](../Memory/MemoryArchitecture.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

## 1. Summary

MoonMind's managed-agent architecture is now **managed-session first**.

A managed agent is not a directly launched process handle and it is not a collection of competing legacy runtime-management strategies. A managed agent is the **declarative desired state** for a long-lived, resumable, observable work session. That desired state is realized by a runtime-specific **managed session plane** that owns session creation, reuse, observation, control, and replacement decisions.

The managed-session model is the only long-term direction for this subsystem. Legacy framings that treat managed agents as imperative runtime objects, terminal sessions, or alternative top-level orchestration models should be considered migration debt rather than parallel architecture.

At the system level:

- **Temporal** remains the durable outer orchestrator.
- **Managed runtimes** run in **separate task-scoped containers** launched from runtime-specific images.
- **Artifacts and bounded workflow metadata** remain durable truth.
- **Session containers** are continuity and performance caches, not durable truth.
- **Context** is assembled by MoonMind and delivered into the session through artifact refs, workspace materialization, and runtime-specific input delivery.
- **Live Logs** is a MoonMind-owned, artifact-first, session-aware observability surface, not an embedded terminal.
- **Authentication** is owned by Provider Profiles plus OAuth and secret-resolution subsystems.
- **Secrets** are provided through references and launch-time materialization, never as raw durable payloads.

Current maturity remains **Codex-first**. Codex is the live managed-session implementation and reference plane. Claude Code, Gemini CLI, and future managed runtimes should be added by implementing additional session planes against the same shared model rather than inventing new top-level managed-agent abstractions.

---

## 2. Scope and non-goals

### 2.1 In scope

This document defines the subsystem architecture for:

- managed agents as declarative desired state,
- runtime-specific managed session planes,
- task-scoped managed session containers,
- session lifecycle and reconciliation boundaries,
- context assembly and runtime delivery,
- session-aware observability and Live Logs,
- provider-profile-driven authentication and launch shaping,
- secret reference, resolution, and materialization boundaries,
- the relationship between managed sessions and adjacent workload/auth containers.

### 2.2 Out of scope

This document does **not** redefine in full detail:

- the exact shared `ManagedAgentSpec` and shared session contracts,
- the detailed Codex session protocol,
- the full OAuth terminal transport protocol,
- the full Secrets System schema or backend implementation,
- the full skill-resolution algorithm,
- the generic Docker workload contract,
- external delegated agent execution.

Those deeper details belong in the related subsystem documents.

---

## 3. Role in the documentation hierarchy

This document is the **Managed Agents subsystem architecture entrypoint**.

It sits between the top-level platform architecture and the more detailed contract documents.

### 3.1 What this document owns

This document owns the architectural answer to questions like:

- What is the managed-agents subsystem in MoonMind?
- Why are managed sessions the core model?
- How do managed sessions fit with Temporal, artifacts, context, observability, auth, and secrets?
- Why are runtime containers, workload containers, and auth containers different things?
- How should future runtimes fit the same architecture?

### 3.2 What `SharedManagedAgentAbstractions.md` owns

`SharedManagedAgentAbstractions.md` is the **runtime-neutral shared contract layer**.

It should define the shared conceptual model and normative abstractions such as:

- managed agent desired state,
- managed session references and bindings,
- normalized observations and events,
- shared conditions and capability flags,
- reconciliation semantics.

This document should reference those shared contracts, not duplicate or replace them.

### 3.3 What runtime-specific docs own

Runtime-specific docs such as `CodexManagedSessionPlane.md` and `CodexCliManagedSessions.md` should explain how one concrete runtime realizes the shared model.

They should not redefine the top-level subsystem architecture and they should not introduce new peer abstractions to the shared managed-session model.

---

## 4. Core architectural model

MoonMind should reason about managed agents through four layers.

### 4.1 Declarative managed-agent intent

A managed agent is a durable logical identity plus desired state.

It describes things such as:

- the target runtime,
- the workspace scope,
- the selected provider profile,
- the skill and context inputs,
- permissions and observability policy,
- whether the session should be present, suspended, or absent.

Higher layers ask for the managed agent to be reconciled into a healthy session. They do not ask to directly launch a runtime process.

### 4.2 Runtime-specific managed session plane

A managed session plane is the runtime-specific control and reconciliation layer.

It owns:

- create, resume, adopt, and replacement decisions,
- session binding,
- runtime-specific control actions,
- event capture and normalization,
- session-state observation,
- compatibility and drift evaluation.

The session plane is the place where runtime-specific behavior belongs. The rest of MoonMind should depend on shared contracts and normalized status first.

### 4.3 Temporal orchestration layer

Temporal remains the durable orchestrator.

The intended workflow shape is:

- `MoonMind.Run` owns the task envelope and step ordering,
- `MoonMind.AgentSession` owns one task-scoped managed session container,
- `MoonMind.AgentRun` owns one true agent execution step that attaches to or uses that session,
- `MoonMind.ManagedSessionReconcile` performs bounded reconciliation work,
- `MoonMind.ProviderProfileManager` coordinates provider-profile capacity and cooldown,
- `MoonMind.OAuthSession` owns interactive OAuth auth flows where required.

This keeps orchestration truth in Temporal while allowing runtime-native continuity inside the managed session container.

### 4.4 Artifact-first durability

Managed sessions may keep runtime-native local state, but MoonMind remains authoritative through:

- artifacts,
- bounded workflow metadata,
- execution and observability read models,
- session continuity projections.

Managed sessions are therefore allowed to be long-lived and stateful, but they are **not** the system of record.

---

## 5. Managed sessions run in separate containers

### 5.1 Container model

Managed runtimes should run in **runtime-specific session containers** launched on demand by MoonMind.

Each managed session container is a task-scoped environment that can hold:

- the runtime's native session loop,
- runtime-local configuration,
- provider-specific auth state or mounted auth home,
- workspace bindings,
- runtime caches,
- task session scratch/spool state,
- runtime-local session memory.

MoonMind worker images should remain generic and lightweight. They should orchestrate managed runtimes, not embed every managed runtime binary.

### 5.2 Task scope and reuse

The default architectural unit is a **task-scoped managed session**.

A task-scoped session may:

- serve one step,
- serve multiple ordered steps in the same task,
- be cleared or reset between steps,
- be torn down and recreated if policy requires replacement,
- coexist with other managed sessions in the same task when isolation demands it.

The session container may persist across multiple steps, but step boundaries remain first-class and must still produce step-scoped durable evidence.

### 5.3 Session identity and reset boundaries

The bounded identity of a managed session is session-plane specific, but the architectural pattern is stable:

- one logical `session_id`,
- one `session_epoch` per continuity interval,
- one runtime-native container/session/thread identity behind the plane,
- one active turn identity when relevant.

A clear or reset operation starts a new continuity interval. In the Codex reference model that means:

- write a control event,
- write a reset-boundary artifact,
- increment `session_epoch`,
- begin a new runtime thread or equivalent,
- preserve the logical session identity while making the boundary explicit.

### 5.4 Separate container types with separate roles

MoonMind should treat these as different container roles:

1. **Managed session container**
   - long-lived for the duration of the task/session policy,
   - owns runtime-native session continuity.

2. **Specialized workload container**
   - bounded tool/build/test/helper execution,
   - launched through MoonMind control-plane tools and approved runner profiles,
   - not itself a managed session by default.

3. **OAuth auth container**
   - short-lived,
   - used only for interactive runtime authentication flows,
   - writes credentials into an auth volume or equivalent profile backing store,
   - not a task execution session.

These container classes should stay separate in both docs and code.

### 5.5 No direct Docker control from the managed session by default

A managed session may discover that it needs a specialized toolchain, but the session container should not receive unrestricted Docker daemon access by default.

Instead:

- the session requests a MoonMind-owned capability,
- MoonMind launches an approved workload container through its controlled Docker boundary,
- workload results return as normal tool outputs and artifacts,
- workload identity remains separate from session identity.

This preserves the security boundary between session continuity and generalized container launching.

### 5.6 Managed sessions are continuity caches, not durable truth

A session container may cache local state for efficiency and continuity, but the authoritative system surfaces are:

- task and step state in Temporal-backed execution records,
- artifacts and observability blobs,
- continuity artifacts and bounded metadata,
- provider-profile and policy records.

Any state needed for recovery, audit, rerun, operator understanding, or UI presentation must be materialized outside the container.

---

## 6. Context architecture

### 6.1 MoonMind owns context assembly

Managed sessions should **consume** context assembled by MoonMind rather than becoming the primary owner of execution context.

For task and step execution, MoonMind may assemble a context pack from sources such as:

- planning state,
- prior task/run history,
- long-term memory and conventions,
- document retrieval and design docs,
- task attachments and instruction bundles,
- resolved skill snapshots,
- operator-supplied objective or steering input.

This lets MoonMind provide more relevant context than any single session window can safely carry on its own.

### 6.2 Durable context sources vs session-local memory

Managed session containers may maintain runtime-local context and memory as a convenience and continuity cache.

That local state is useful, but it is not durable truth. Durable context sources remain:

- artifact-backed instruction and context bundles,
- execution history and read models,
- retrieval indexes and memory systems,
- session summaries, checkpoints, and continuity artifacts,
- resolved skill manifests and runtime materialization metadata.

This means a reset, resume, or recovery path can rebuild enough execution context without requiring container-local state to be authoritative.

### 6.3 Skill resolution and delivery

Agent skills are part of execution context, but they are their own subsystem.

The canonical direction is:

1. select skill intent at the task and step level,
2. resolve an immutable `ResolvedSkillSet` before runtime launch,
3. keep large skill bodies out of workflow history,
4. pass compact refs into the agent-run path,
5. let the runtime adapter materialize the resolved snapshot for the runtime.

For managed runtimes, the recommended default is **hybrid** materialization:

- a compact prompt index or summary is delivered into runtime input,
- the full resolved active skill set is materialized into workspace-visible files.

The stable runtime-visible path for managed runtimes is:

- `.agents/skills` for the active resolved skill set,
- `.agents/skills/local` as a local overlay/input area when policy allows it.

The runtime should see a stable active skill view, not a mutable rewrite of checked-in user-authored skill files.

### 6.4 Context packaging and payload discipline

Context should be packaged with the same artifact discipline used elsewhere in MoonMind:

- large bodies belong in artifacts or blob-backed refs,
- workflow payloads remain small,
- runtime adapters fetch or materialize the required context near launch or turn execution,
- retries and reruns reuse the same resolved context snapshot by default unless re-resolution is explicitly requested.

This keeps the orchestration model deterministic and auditable.

### 6.5 Context resets and session epochs

A session clear or reset should not be confused with deleting MoonMind's durable context.

When a new session epoch begins, MoonMind may:

- provide a new compact objective bundle,
- re-materialize the active skill view,
- attach or reference the latest continuity summary,
- let retrieval and memory subsystems rebuild just-in-time context for the next turn.

Resetting the runtime thread is therefore a continuity boundary, not an architectural loss of system-owned context.

### 6.6 Secret and trust rules for context

Context-bearing surfaces such as skill bodies, manifests, and prompt bundles are **not** a valid place to hide secrets.

MoonMind should not:

- encourage secrets in skills,
- log full skill or context bodies by default,
- materialize provider credentials into skill or context artifacts for convenience.

Context remains a controlled execution input, not a secret-storage channel.

---

## 7. Live Logs and observability

### 7.1 Live Logs is not a terminal

For ordinary managed task execution, **Live Logs** should be a MoonMind-native, session-aware observability surface rather than an embedded terminal.

The primary model is:

- durable stdout/stderr capture,
- structured diagnostics,
- session continuity artifacts,
- bounded session snapshot metadata,
- optional live follow delivered by MoonMind.

`xterm.js` is reserved for OAuth and other explicitly interactive terminal flows. It should not be the primary observability surface for managed runs.

### 7.2 Artifact-first, session-aware timeline

Live Logs should present a merged timeline over normalized observability events, including:

- `stdout` output,
- `stderr` output,
- MoonMind `system` annotations,
- session lifecycle milestones,
- turn lifecycle milestones,
- approval events,
- continuity publication events such as summaries, checkpoints, and reset boundaries.

This lets operators understand both raw runtime output and the session-continuity story in one place.

### 7.3 Session snapshot in the viewer

When a managed session plane is active, the Live Logs surface should expose the latest known session snapshot, including when available:

- `session_id`,
- `session_epoch`,
- `container_id`,
- `thread_id`,
- `active_turn_id`,
- latest continuity artifact refs,
- bounded approval summary.

Reset boundaries and epoch changes should be rendered as explicit timeline boundaries rather than buried in plain text output.

### 7.4 Durable truth and optional live delivery

Live follow is useful, but it is not authoritative.

The durable source of truth remains:

- artifacts,
- bounded workflow metadata,
- MoonMind-owned structured observability records.

Initial panel content should load from MoonMind APIs and durable artifacts. Live streaming is an optional upgrade path for active runs.

### 7.5 Cross-process live transport

If live follow is supported, the live publication path must cross the producer/API boundary through a shared MoonMind transport such as a shared append-only spool or similar cross-process mechanism.

An API-local in-memory publisher is not the architectural boundary.

### 7.6 Observation and intervention stay separate

Live Logs is passive observation.

Intervention such as:

- pause,
- resume,
- clear,
- approve,
- reject,
- operator messaging,
- cancel,
- terminate

belongs in separate control surfaces backed by Temporal signals, updates, and runtime adapter control actions.

### 7.7 Continuity drill-down remains complementary

MoonMind may also provide a dedicated continuity drill-down view for session summaries, checkpoints, control events, and reset boundaries.

That drill-down is complementary. Live Logs should still inline the most important session milestones so operators can understand a run without jumping across unrelated surfaces.

---

## 8. Authentication architecture

### 8.1 Provider Profiles own runtime/provider selection and launch shaping

Authentication for managed runtimes should be mediated by **Provider Profiles**, not by ad hoc runtime flags or undocumented per-runtime environment handling.

A Provider Profile is the semantic owner of:

- runtime selection,
- provider selection,
- credential source class,
- runtime materialization mode,
- default model intent,
- command shaping,
- concurrency and cooldown policy,
- auth volume or secret references required for launch.

This keeps authentication strategy aligned with runtime selection and execution policy.

### 8.2 Provider-profile capacity remains a first-class orchestration concern

Managed runs should not compete for provider access through informal best effort.

The `MoonMind.ProviderProfileManager` workflow should remain the durable coordinator for:

- active profile leases,
- slot assignment,
- cooldown windows,
- waiting for compatible profiles,
- release on completion or cancellation.

This is especially important for OAuth-backed accounts and provider-rate-limited profiles.

### 8.3 OAuth-backed runtime authentication

For runtimes that authenticate through interactive CLI login flows, MoonMind should use a dedicated OAuth architecture:

- Mission Control renders `xterm.js`,
- MoonMind exposes a PTY/WebSocket bridge,
- a short-lived auth container runs the login CLI,
- a dedicated auth volume is mounted at the runtime-appropriate path,
- MoonMind verifies the resulting credential material,
- MoonMind creates or updates the Provider Profile,
- the auth container and terminal bridge are torn down.

This is a short-lived, tightly scoped auth flow. It is not a general terminal or session-attachment mechanism for normal managed runs.

### 8.4 Auth volumes are durable profile backing, not terminal-session truth

For OAuth-backed profiles, durable credential state should live in a runtime-specific auth volume or equivalent backing store.

The terminal session itself is transient. The durable launch target is the Provider Profile plus the mounted auth volume.

### 8.5 Secret-ref backed runtime authentication

For non-OAuth profiles, the expected model is:

- Provider Profiles store secret references, not raw values,
- MoonMind resolves those references at launch time,
- the runtime receives only the narrow materialization it requires.

Depending on the runtime, launch shaping may use:

- `api_key_env`,
- `env_bundle`,
- `config_bundle`,
- `oauth_home`,
- `composite` materialization.

Runtime-specific shaping is allowed, but it should remain behind the provider-profile and adapter boundary.

### 8.6 Auth isolation and competing variable cleanup

Before launch, MoonMind should clear competing credential variables that could cause accidental provider fallback or ambiguous runtime behavior.

OAuth and secret-backed profiles must also remain isolated by runtime and account unless a sharing model is explicitly designed and documented.

---

## 9. Secrets architecture

### 9.1 Secret references, not raw durable secrets

MoonMind should store and transport **secret references** across durable contracts rather than raw secret values.

That rule applies to:

- provider profiles,
- runtime launch requests,
- workflow payloads,
- task definitions,
- logs,
- artifacts,
- run metadata.

Only a `SecretRef` or equivalent durable identifier should appear in those surfaces.

### 9.2 Local-first encrypted-at-rest baseline

The baseline MoonMind secret path should support local-first deployments while still being secure enough for multi-runtime orchestration.

The default managed-secret posture should therefore be:

- application-layer encryption for MoonMind-managed secrets,
- encrypted ciphertext in the database,
- root key custody outside the main application database,
- optional operator-selected stronger backends later.

This keeps first-run setup simple without normalizing unsafe plaintext durable storage.

### 9.3 Multiple backend classes behind one resolver boundary

MoonMind may support multiple secret backend classes behind a common resolver contract, including:

- `env`,
- `db_encrypted`,
- `exec`,
- `oauth_volume`.

The architectural contract is that all of them remain reference-based in durable state and resolve only through controlled MoonMind resolution paths.

### 9.4 Resolve late, scope narrowly

Secret values should be resolved only at controlled execution boundaries such as:

- managed runtime launch,
- MoonMind-owned provider calls,
- approved tool or integration calls,
- explicit validation flows.

When a third-party runtime truly requires credentials in-process, MoonMind should materialize only the minimum required secret surface for the narrowest feasible scope.

### 9.5 Proxy first when MoonMind owns the outbound call path

When MoonMind itself owns a provider request path, the preferred model is:

- resolve the secret inside MoonMind,
- perform the outbound call inside MoonMind,
- expose only MoonMind-controlled capabilities or results to the caller.

This reduces the number of runtimes that ever need raw provider credentials.

### 9.6 Launch-only runtime materialization

When managed runtimes require direct credentials, materialization should be launch-time and runtime-scoped.

Acceptable materialization paths include:

- a single environment variable,
- a small environment bundle,
- generated runtime-specific config files,
- mounted OAuth home/volume state,
- a composite of these.

Generated config files that contain secrets are sensitive runtime files. They are not durable artifacts by default.

### 9.7 Redaction and artifact hygiene

MoonMind must keep secret values out of durable operator-facing surfaces.

Logs, artifacts, diagnostics, summaries, and run metadata should redact:

- secret-like strings,
- resolved environment values,
- generated config contents that include provider credentials.

### 9.8 Secrets in managed sessions and workload containers

Managed session containers and specialized workload containers must receive secrets only through explicit MoonMind policy.

MoonMind should not:

- pass through the full worker environment wholesale,
- copy broad session environments into workload containers,
- let workload containers inherit unrelated auth state by default.

Every credential mount or environment injection should be declared, scoped, and justified.

---

## 10. Relationship to specialized workload containers

Managed sessions may cooperate with specialized workload containers, but the architecture must keep them distinct.

A managed session step may:

- inspect the repository,
- decide that it needs a specialized toolchain,
- request a MoonMind tool such as a build/test workload,
- receive artifacts/results back,
- continue the managed session afterward.

But:

- the workload container is not the session,
- workload artifacts are not session continuity artifacts by default,
- workload identity remains separate from session identity,
- `tool.type = "agent_runtime"` remains reserved for true long-lived agent runtimes.

This distinction is especially important as MoonMind evolves richer build/test/container execution paths alongside managed sessions.

---

## 11. Current maturity and runtime evolution

### 11.1 Codex is the current reference plane

Codex is the current concrete managed-session implementation and reference plane.

The current live task-scoped session plane is therefore Codex-first. Runtime-specific details such as Codex App Server protocol, thread/turn semantics, and session reset mechanics remain documented in the Codex-specific docs.

### 11.2 Future runtimes should extend the same model

Claude Code, Gemini CLI, and future managed runtimes should be added by implementing new session planes against the same shared model.

They should not add new top-level abstractions such as:

- runtime-specific managed-agent root concepts,
- parallel orchestration models,
- new peer architectures to the shared managed-session model.

The allowed variation is inside runtime-specific planes and capability flags.

### 11.3 Contract extraction rule

While Codex remains the only real managed-session implementation, some activity and session contracts may stay Codex-shaped internally.

When a second managed-session runtime becomes real, MoonMind should extract or harden the runtime-neutral `ManagedSession*` contract layer more explicitly at the activity boundary.

This document does not block that evolution. It provides the architectural direction that such extraction should serve.

---

## 12. Recommended document map for `docs/ManagedAgents`

The intended document roles should be:

- `ManagedAgentArchitecture.md`
  - subsystem architecture entrypoint for managed agents,
  - explains how managed sessions fit into MoonMind.

- `SharedManagedAgentAbstractions.md`
  - normative shared runtime-neutral contract layer,
  - defines common managed-agent/session/plane abstractions.

- `CodexManagedSessionPlane.md`
  - current runtime-specific architecture entrypoint,
  - explains how the Codex plane fits the shared model.

- `CodexCliManagedSessions.md`
  - detailed Codex contract and protocol shape.

- `LiveLogs.md`
  - session-aware observability and Live Logs architecture.

- `OAuthTerminal.md`
  - interactive OAuth terminal transport for auth flows only.

- `DockerOutOfDocker.md`
  - specialized workload-container architecture and its boundary from managed sessions.

This keeps architecture, shared contracts, and runtime-specific detail clearly separated.

---

## 13. Final desired-state statement

MoonMind's desired managed-agent architecture is:

- **managed-session first,**
- **Temporal-orchestrated,**
- **artifact-first,**
- **session-container based,**
- **context-assembled by MoonMind,**
- **observed through a session-aware Live Logs surface,**
- **authenticated through Provider Profiles and OAuth/session-auth subsystems,**
- **secured through secret references and narrow launch-time materialization,**
- **extensible by adding new runtime-specific session planes rather than new top-level models.**

In that desired state:

- managed sessions run in **separate runtime containers**,
- specialized workload execution remains in **separate workload containers**,
- interactive auth remains in **separate short-lived auth containers**,
- container-local state remains a **cache**, not durable truth,
- MoonMind-owned artifacts, metadata, and orchestration remain authoritative,
- and the Managed Agents subsystem converges on one coherent managed-session architecture rather than preserving multiple legacy strategies.
