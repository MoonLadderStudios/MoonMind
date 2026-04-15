# Story Breakdown: Claude Code Managed Sessions

**Source design:** `docs/ManagedAgents/ClaudeCodeManagedSessions.md`
**Story extraction date:** 2026-04-14T23:03:07Z
**Coverage gate:** PASS - every major design point is owned by at least one story.

## Design Summary

Claude Code Managed Sessions is a Claude-specific binding of the shared Managed Session Plane. It preserves canonical session, turn, work item, decision, policy, artifact, and usage abstractions while adding first-class modeling for execution owner, UI projection, managed settings precedence, richer permission decisions, typed context, checkpoints, subagents, teams, multi-surface behavior, storage, observability, governance, and Codex-plane compatibility. The design is intentionally metadata-first and adapter-oriented: the control plane owns normalized state, lineage, events, policy versions, and artifact pointers while runtime-local systems retain heavyweight transcripts, file reads, checkpoint payloads, and provider-specific details.

## Coverage Points

- **DESIGN-REQ-001 - Preserve shared Managed Session Plane nouns** (requirement, 1 Executive summary; 2.1 Preserve shared abstractions; 5 Shared abstractions): Claude Code must map into SessionPlane, ManagedSession, Turn, WorkItem, DecisionPoint, PolicyEnvelope, RuntimeAdapter, ArtifactStore, and UsageLedger rather than forking the control-plane model.
- **DESIGN-REQ-002 - Separate execution owner from presentation surface** (state-model, 2.2 Separate execution from presentation; 7 Runtime model): Local, cloud, SDK-hosted execution, primary surfaces, projections, and handoffs are independent axes and must not be inferred from UI alone.
- **DESIGN-REQ-003 - Model Claude supported session shapes** (requirement, 7.2 Supported session shapes): The plane must represent local interactive, local Remote Control, cloud interactive, scheduled, desktop scheduled, SDK embedded, subagent, and agent-team session shapes.
- **DESIGN-REQ-004 - Keep Remote Control distinct from cloud handoff** (constraint, 7.3 Critical execution distinctions; 16 Multi-surface behavior): Remote Control adds surface projections to the same session; cloud handoff creates a new session with lineage and a cloud execution owner.
- **DESIGN-REQ-005 - Provide canonical Claude domain records** (state-model, 9 Canonical domain model): ManagedSession, Turn, WorkItem, DecisionPoint, PolicyEnvelope, ContextSnapshot, Checkpoint, ChildContext, and SessionGroup fields must be normalized and persisted as control-plane records.
- **DESIGN-REQ-006 - Implement session, turn, decision, checkpoint, and surface lifecycles** (state-model, 10 Lifecycle state machines): The design defines explicit legal lifecycle transitions for sessions, turns, decisions, checkpoints, and surface bindings.
- **DESIGN-REQ-007 - Compile Claude policy into versioned PolicyEnvelope** (security, 2.3 Treat policy as compiled runtime state; 11 Policy model): Managed settings, permission mode, rules, protected paths, hooks, sandboxing, provider and surface constraints, MCP controls, memory controls, and auto-mode constraints must compile into an effective versioned envelope.
- **DESIGN-REQ-008 - Respect managed settings precedence and fetch states** (security, 11.3 Managed settings source resolution; 11.4 Effective policy assembly algorithm; 11.5 Policy handshake state): Server-managed settings win over endpoint-managed settings; local overrides are disallowed; fetch, cache, fail-closed, and security-dialog states are explicit.
- **DESIGN-REQ-009 - Represent BootstrapPreferences honestly for Claude** (constraint, 11.2 Claude mapping; 22.5 Known semantic mismatch): Claude has no native admin-managed default tier equivalent to Codex defaults, so bootstrap preferences can only be labeled session templates, not enforced managed settings.
- **DESIGN-REQ-010 - Normalize the full Claude decision pipeline** (security, 2.4 Model deterministic and non-deterministic safety controls explicitly; 12 Decision pipeline): Decisions must include session guards, hooks, permission rules, protected paths, permission-mode baseline, sandbox substitution, auto classifier, interactive or headless resolution, runtime execution, posttool hooks, and checkpoint capture.
- **DESIGN-REQ-011 - Track DecisionPoint provenance and outcomes** (observability, 9.4 DecisionPoint; 18.6 Decision events): Each decision records target, action, origin stage, resolution, resolver, reason, timestamps, and normalized events.
- **DESIGN-REQ-012 - Make context lifecycle inspectable** (artifact, 2.5 Make context lifecycle inspectable; 13 Context and memory model): Startup and on-demand context sources, source refs, load timing, reinjection policy, token hints, and compaction epochs must be visible.
- **DESIGN-REQ-013 - Separate memory guidance from enforcement** (security, 13.5 Managed memory guidance vs enforcement): CLAUDE.md, memory, and rules are guidance; managed settings and permission rules are enforcement and must not be conflated.
- **DESIGN-REQ-014 - Model compaction as a new context epoch** (state-model, 13.3 Compaction-aware context model; 13.4 Compaction behavior): Compaction creates a new ContextSnapshot epoch, reloads startup-critical context, selectively reinjects on-demand context, and emits a compaction WorkItem.
- **DESIGN-REQ-015 - Make checkpointing and rewind first-class** (state-model, 14 Checkpointing and rewind): Checkpoint capture, index, restore modes, summarize-from-here, active cursor, lineage, retention, and APIs are session-plane operations.
- **DESIGN-REQ-016 - Distinguish subagents from agent teams** (state-model, 6 Claude-specific deltas; 15 Child work model): Subagents are child contexts inside one session; agent teams are grouped sibling sessions with direct peer messaging and independent state.
- **DESIGN-REQ-017 - Support child work APIs and events** (integration, 17.6 Child-work APIs; 18.7 Child-work events): The plane must expose SpawnSubagent, CreateTeamTeammate, SendTeamMessage, and normalized child/team events.
- **DESIGN-REQ-018 - Support multi-surface attachment and reconnect behavior** (integration, 16 Multi-surface behavior; 17.1 Session APIs): SurfaceBinding records capabilities, connection state, projections, disconnect/reconnect, detach, and resume semantics without killing the underlying session.
- **DESIGN-REQ-019 - Expose high-level session, turn, policy, decision, checkpoint, child-work, and subscription APIs** (integration, 17 API surface of the plane): The Claude plane should keep Codex-like high-level verbs while adding Claude-specific policy, checkpoint, child-work, and event subscription capabilities.
- **DESIGN-REQ-020 - Emit append-only normalized event streams** (observability, 5.1 SessionPlane; 18 Event model): Session, surface, policy, turn, work, decision, and child-work events must be append-only and normalized for shared-plane consumers.
- **DESIGN-REQ-021 - Keep the storage model payload-light** (constraint, 19 Storage model): Central stores keep ids, state, events, policies, usage, and artifact pointers; runtime-local stores keep transcripts, full reads, checkpoint payloads, and caches.
- **DESIGN-REQ-022 - Provide policy-driven retention classes** (migration, 19.3 Suggested retention classes): Hot metadata, event logs, usage, audit metadata, and checkpoint payload retention should be policy-driven, not hard-coded.
- **DESIGN-REQ-023 - Normalize Claude OTel telemetry into shared observability** (observability, 20 Observability): Claude telemetry should map to shared metrics, logs/events, and optional traces with defined metrics and span boundaries.
- **DESIGN-REQ-024 - Model layered security and governance explicitly** (security, 21 Security and governance): Managed settings, rules, modes, protected paths, sandboxing, hooks, classifier, dialogs, runtime isolation, trust levels, provider caveats, and hook audits must be first-class.
- **DESIGN-REQ-025 - Preserve local-code residency expectations** (security, 19.1 Storage principle; 21.6 Cloud vs local security): Local execution and Remote Control should not centrally store source code or checkpoint payloads by default, while cloud execution is explicitly identified.
- **DESIGN-REQ-026 - Maintain Codex plane compatibility without hiding semantic mismatches** (constraint, 22 Compatibility strategy with the Codex plane; 25 Recommended implementation stance): Shared interfaces remain unchanged, Claude-specific extensions are isolated, unified `session_id` naming is used without legacy aliases, and mismatches such as managed defaults are declared.
- **DESIGN-REQ-027 - Keep runtime-specific wire complexity inside adapters** (integration, 2.6 Keep runtime-specific complexity inside adapters; 8 High-level architecture): The shared plane defines normalized APIs and events; ClaudeRuntimeAdapter implementations translate local CLI, IDE, Remote Control, cloud, and SDK specifics.
- **DESIGN-REQ-028 - Follow phased rollout dependencies** (migration, 23 Rollout plan): Rollout begins with metadata-only registry, then policy/decisions, context/checkpoints, child work, remote projection/handoff, and telemetry/audits.
- **DESIGN-REQ-029 - Respect explicit non-goals** (non-goal, 4 Non-goals): The design must not reimplement Anthropic proprietary local protocol, replace git history, store all transcripts/diffs centrally, erase cloud/local distinctions, simulate non-native admin defaults, or collapse all collaboration products into one session type.
- **DESIGN-REQ-030 - Track open questions as downstream clarifications** (constraint, 24 Open questions): Checkpoint payload location, policy fetch visibility, subagent promotion, schedule templates, handoff summary audit, and Claude default emulation remain unresolved.

## Ordered Story Candidates

### STORY-001: Create metadata-only Claude managed session registry

- **Short name:** `claude-session-registry`
- **Why:** This is the first vertical slice because every later policy, context, checkpoint, child-work, and telemetry feature needs canonical session, turn, event, and lineage records.
- **Description:** As an operator, I can create, resume, fork, archive, and inspect Claude Code managed sessions using the same control-plane identity model as other managed runtimes while preserving Claude-specific execution-owner and surface metadata.
- **Independent test:** Create local, cloud, scheduled, SDK, and Remote Control-shaped Claude sessions through the plane API, submit a turn, resume/fork/archive the session, and verify persisted records, lifecycle transitions, lineage, and append-only events without requiring Claude runtime execution.
- **Dependencies:** None
- **Needs clarification:** None
- **Scope:**
  - Canonical ManagedSession, Turn, and WorkItem records for Claude Code
  - execution_owner, primary_surface, surface_bindings, projection_mode, repo/cwd, lineage, and lifecycle fields
  - metadata-only session APIs and append-only session/turn/work events
  - resume, fork, archive, end, and basic lineage behavior
- **Out of scope:**
  - Full policy handshake
  - checkpoint restore payloads
  - subagents and agent teams
  - Remote Control reconnect implementation beyond storing bindings
- **Acceptance criteria:**
  - Claude sessions are persisted as ManagedSession rows with runtime_family = claude_code and required runtime-axis fields.
  - CreateSession, ResumeSession, ForkSession, ArchiveSession, EndSession, SubmitTurn, InterruptTurn, CompactSession, and RenameSession expose Codex-compatible high-level verbs where applicable.
  - Remote Control-shaped input adds a projection SurfaceBinding without changing execution_owner.
  - Cloud handoff-shaped input creates a new session with handoff lineage rather than mutating the source session.
  - Session, turn, work, and surface lifecycle transitions reject illegal transitions and emit normalized append-only events.
- **Owned coverage:**
  - **DESIGN-REQ-001:** Owns preservation of shared nouns at the first session-plane slice.
  - **DESIGN-REQ-002:** Owns execution owner, surface, and projection axes in canonical session records.
  - **DESIGN-REQ-003:** Owns supported session shapes as creation/resume test fixtures.
  - **DESIGN-REQ-004:** Owns Remote Control versus cloud handoff identity behavior.
  - **DESIGN-REQ-005:** Owns initial ManagedSession, Turn, and WorkItem records.
  - **DESIGN-REQ-006:** Owns metadata lifecycle transitions for sessions, turns, work items, and surfaces.
  - **DESIGN-REQ-018:** Owns initial SurfaceBinding persistence.
  - **DESIGN-REQ-019:** Owns metadata-only session and turn APIs.
  - **DESIGN-REQ-020:** Owns append-only session, turn, work, and surface events.
  - **DESIGN-REQ-021:** Owns the payload-light registry and event-log split.
  - **DESIGN-REQ-027:** Owns the boundary where runtime adapters attach to normalized records.
  - **DESIGN-REQ-028:** Owns rollout phase 1.
  - **DESIGN-REQ-029:** Owns non-goals related to not reimplementing proprietary protocol or centralizing all transcripts.
- **Handoff:** Build the metadata-only Claude Code managed session registry as the first one-story spec. Preserve the shared Managed Session Plane nouns, add Claude runtime-axis and lineage fields, expose metadata-level session and turn verbs, and verify lifecycle/event behavior without requiring live Claude execution.

### STORY-002: Compile Claude managed policy and startup handshake state

- **Short name:** `claude-policy-envelope`
- **Why:** Policy must be explicit before decisions can be evaluated safely, and Claude managed settings have precedence and semantics that differ materially from Codex.
- **Description:** As an enterprise administrator, I can see the effective Claude Code policy for a session, including managed source precedence, fetch state, security-dialog state, provider mode, trust level, and unsupported managed-default semantics.
- **Independent test:** Given fixture policy sources for server-managed, endpoint-managed, empty, cache-hit, fetch-failed, and force-refresh-failed cases, resolve policy and assert precedence, fetch state, trust level, dialog state, compiled envelope versioning, and failure behavior.
- **Dependencies:** STORY-001
- **Needs clarification:** How much of server-managed settings fetch state should be user-visible outside admin views?
- **Scope:**
  - PolicyEnvelope compilation for Claude
  - server-managed versus endpoint-managed source resolution
  - policy fetch and fail-closed states
  - PolicyHandshake states and security dialog acknowledgement
  - provider_mode, PolicyTrustLevel, managed_source_kind, and bootstrap-template handling
- **Out of scope:**
  - Per-action decision resolution
  - Hook execution implementation
  - Context compaction behavior
  - Provider-specific settings fetch protocol beyond adapter-bound inputs
- **Acceptance criteria:**
  - Server-managed non-empty settings win over endpoint-managed settings.
  - Endpoint-managed settings apply only when server-managed settings are empty or unsupported.
  - Local project/user settings are visible for observability but cannot override managed settings.
  - forceRemoteSettingsRefresh failure produces fail_closed startup behavior.
  - Risky managed hooks or custom environment variables can require an interactive security-dialog state.
  - BootstrapPreferences are represented only as session bootstrap templates and are not labeled as native Claude managed defaults.
  - provider_mode and PolicyTrustLevel are recorded on every PolicyEnvelope.
- **Owned coverage:**
  - **DESIGN-REQ-005:** Owns PolicyEnvelope record fields.
  - **DESIGN-REQ-007:** Owns compiled effective policy state.
  - **DESIGN-REQ-008:** Owns source precedence, fetch state, and handshake state.
  - **DESIGN-REQ-009:** Owns the BootstrapPreferences semantic mismatch.
  - **DESIGN-REQ-019:** Owns policy APIs.
  - **DESIGN-REQ-020:** Owns policy.fetch.*, policy.dialog.*, and policy.compiled events.
  - **DESIGN-REQ-024:** Owns policy trust level and provider caveats.
  - **DESIGN-REQ-026:** Owns compatibility around Codex managed-default mismatch.
  - **DESIGN-REQ-028:** Owns rollout phase 2 policy foundation.
  - **DESIGN-REQ-030:** Owns open questions about policy fetch visibility and default emulation.
- **Handoff:** Build Claude managed policy compilation as a single story focused on effective PolicyEnvelope output and startup handshake state. Treat policy as control-plane state, not a CLI flag passthrough, and verify precedence and fail-closed behavior with fixture sources.

### STORY-003: Normalize Claude decisions across permissions, hooks, sandboxing, classifier, and prompts

- **Short name:** `claude-decision-pipeline`
- **Why:** Claude permissioning is not just approval prompts; the plane needs one auditable path for deterministic and classifier-based safety controls before tool execution is trustworthy.
- **Description:** As an operator or auditor, I can inspect every Claude Code decision point with its origin stage, proposed action, policy/hook/classifier/user provenance, resolution, and resulting runtime outcome.
- **Independent test:** Feed representative proposed actions through fixture policies and hooks covering deny, ask, allow, protected paths, sandbox substitution, classifier denial, interactive approval, headless defer/deny, and runtime failure; verify DecisionPoint provenance, events, and WorkItem outcomes.
- **Dependencies:** STORY-002
- **Needs clarification:** None
- **Scope:**
  - DecisionPipeline ordering and resolution semantics
  - DecisionPoint creation, mutation, denial, defer, cancel, and resolution records
  - permission modes, allow/ask/deny rule precedence, protected paths, sandbox substitution, auto-mode classifier, headless resolution, and interactive prompts
  - pretool/posttool hook WorkItems and HookAudit metadata
- **Out of scope:**
  - Full hook shell execution sandbox
  - Policy source fetching
  - Checkpoint restore implementation beyond emitting capture edge after tracked edits
- **Acceptance criteria:**
  - Decision stages execute in documented order.
  - deny rules take precedence over ask and allow rules.
  - Hooks can tighten restrictions but cannot override matching deny or ask policy.
  - Protected paths are represented with origin_stage = protected_path and are never auto-approved.
  - Sandbox substitution is recorded separately from an allow rule.
  - Auto-mode classifier decisions are distinguishable from user approvals.
  - Headless unresolved decisions deny or defer according to policy.
  - Hook executions emit auditable WorkItems including hook source scope, matcher, and outcome.
- **Owned coverage:**
  - **DESIGN-REQ-006:** Owns decision lifecycle transitions.
  - **DESIGN-REQ-010:** Owns the full decision pipeline.
  - **DESIGN-REQ-011:** Owns DecisionPoint provenance and decision events.
  - **DESIGN-REQ-020:** Owns decision and hook work event emission.
  - **DESIGN-REQ-024:** Owns layered control stack, protected path policy, classifier, dialogs, and hook governance.
  - **DESIGN-REQ-025:** Owns security distinction between local, Remote Control, and cloud execution during decisions.
  - **DESIGN-REQ-028:** Owns rollout phase 2 decision behavior.
  - **DESIGN-REQ-029:** Owns non-goal of making cloud and local indistinguishable.
- **Handoff:** Build the Claude decision pipeline as a testable control-plane story. The acceptance path should exercise every decision origin stage and prove the resulting DecisionPoint and WorkItem event history is auditable and deterministic where the design says it must be.

### STORY-004: Index Claude context snapshots and compaction epochs

- **Short name:** `claude-context-snapshots`
- **Why:** Claude session quality and safety depend heavily on runtime context. The plane needs typed, reload-aware context metadata before checkpoint, rewind, and audit features can explain behavior.
- **Description:** As an operator, I can inspect what context entered a Claude session, why it was loaded, whether it survives compaction, and which parts are guidance versus enforceable policy.
- **Independent test:** Bootstrap a session with fixture startup context, simulate on-demand file, nested CLAUDE.md, rule, skill, and hook context loads, run compaction, and verify a new ContextSnapshot epoch with expected reinjection policies and payload-light pointers.
- **Dependencies:** STORY-001
- **Needs clarification:** None
- **Scope:**
  - ContextSnapshot and segment metadata
  - startup and on-demand context source kinds
  - source_ref, loaded_at, reinjection_policy, token_budget_hint, and compaction_epoch fields
  - compaction WorkItem and new snapshot epoch creation
  - clear separation of memory guidance from policy enforcement
- **Out of scope:**
  - Storing full file-read or transcript payloads centrally
  - Implementing memory authoring UX
  - Checkpoint restore APIs
- **Acceptance criteria:**
  - Startup context sources include system prompt, output style, managed/project/local CLAUDE.md, auto memory, MCP manifests, skill descriptions, and hook-injected context.
  - On-demand context sources include file reads, nested CLAUDE.md, path rules, and invoked skill bodies.
  - Compaction creates a new ContextSnapshot epoch instead of mutating the old one.
  - Startup-critical context is marked always or startup-refresh as appropriate; file reads default to never reinject.
  - Memory artifacts are never treated as hard policy sources.
  - ContextIndex stores metadata and pointers, not full source payloads by default.
- **Owned coverage:**
  - **DESIGN-REQ-005:** Owns ContextSnapshot record fields.
  - **DESIGN-REQ-012:** Owns inspectable context lifecycle.
  - **DESIGN-REQ-013:** Owns memory guidance versus enforcement.
  - **DESIGN-REQ-014:** Owns compaction epochs and WorkItems.
  - **DESIGN-REQ-020:** Owns work.context.loaded and compaction events.
  - **DESIGN-REQ-021:** Owns ContextIndex metadata-pointer storage.
  - **DESIGN-REQ-025:** Owns local-code residency for context payloads.
  - **DESIGN-REQ-028:** Owns the context half of rollout phase 3.
- **Handoff:** Build ContextSnapshot indexing and compaction epochs as one story. Focus on typed metadata and reinjection semantics, keeping full runtime payloads out of the central store and explicitly separating guidance context from enforceable policy.

### STORY-005: Expose Claude checkpoints and rewind operations

- **Short name:** `claude-checkpoint-rewind`
- **Why:** Claude checkpointing changes session truth and must be represented in the plane rather than treated as a UI-only runtime feature.
- **Description:** As an operator, I can list Claude session checkpoints and restore code, conversation, both, or summarize-from-here without losing checkpoint provenance or event history.
- **Independent test:** Simulate user-prompt and file-edit checkpoint captures, list checkpoints, restore each supported mode, summarize from a checkpoint, and verify event history is preserved, active cursor changes, lineage is recorded, and payloads remain runtime-local by default.
- **Dependencies:** STORY-004
- **Needs clarification:** Should checkpoint payloads ever leave the runtime host by default, or only under explicit compliance export policy?
- **Scope:**
  - Checkpoint record and CheckpointIndex metadata
  - checkpoint capture triggers for user prompts and file edits
  - ListCheckpoints, RestoreCheckpoint, and SummarizeFromCheckpoint APIs
  - rewind lifecycle, active checkpoint cursor, rewound_from_checkpoint_id lineage, artifact pointers, and retention metadata
- **Out of scope:**
  - Replacing git history
  - Centrally storing checkpoint payloads by default
  - Implementing provider-specific restore mechanics beyond adapter-bound request/response
- **Acceptance criteria:**
  - Every user prompt and tracked file edit can create a checkpoint metadata record.
  - Bash side effects and external manual edits follow documented capture limits.
  - The four normalized rewind actions are exposed and validated.
  - Rewind preserves pre-rewind event history and records the new active checkpoint cursor.
  - Checkpoint metadata remains addressable until expiry or garbage collection.
  - Checkpoint payload storage defaults to runtime-local references.
- **Owned coverage:**
  - **DESIGN-REQ-005:** Owns Checkpoint record fields.
  - **DESIGN-REQ-006:** Owns checkpoint lifecycle and session rewinding transitions.
  - **DESIGN-REQ-015:** Owns checkpointing and rewind as first-class operations.
  - **DESIGN-REQ-019:** Owns checkpoint APIs.
  - **DESIGN-REQ-020:** Owns checkpoint and rewind events.
  - **DESIGN-REQ-021:** Owns CheckpointIndex and runtime-local payload references.
  - **DESIGN-REQ-022:** Owns checkpoint retention class behavior.
  - **DESIGN-REQ-025:** Owns default no-central-checkpoint-payload behavior.
  - **DESIGN-REQ-028:** Owns the checkpoint half of rollout phase 3.
  - **DESIGN-REQ-030:** Owns open questions about checkpoint payload export and handoff summary audit.
- **Handoff:** Build checkpoint and rewind support as a single story centered on metadata, APIs, lineage, and events. Preserve source-control safety expectations by keeping event history intact and checkpoint payloads pointer-based unless policy says otherwise.

### STORY-006: Model Claude subagents and agent teams as distinct child-work primitives

- **Short name:** `claude-child-work`
- **Why:** Subagents and teams have different identity, communication, lifecycle, permission, and usage-accounting semantics. Modeling them separately prevents bad resume, archive, billing, and audit behavior.
- **Description:** As an operator, I can distinguish a Claude subagent child context from an agent-team teammate session, inspect child work status and usage, and follow peer messages without collapsing different runtime semantics.
- **Independent test:** Create a parent session, spawn a subagent child context, create a session group with lead and teammate sessions, send a peer message, complete and fail children, and verify identities, lineage, lifecycle ownership, usage rollups, and events remain distinct.
- **Dependencies:** STORY-001
- **Needs clarification:** Should a long-running background subagent ever promote to a sibling session, or should it remain parent-owned?
- **Scope:**
  - ChildContext records for subagents
  - SessionGroup records for agent teams
  - SpawnSubagent, CreateSessionGroup, CreateTeamTeammate, and SendTeamMessage APIs
  - subagent return summaries, child usage rollup, team usage rollup, peer message eventing, teardown state, and background child execution states
- **Out of scope:**
  - A general multi-agent collaboration product
  - Automatic promotion of long-running subagents to sessions
  - Provider-specific team communication transport internals
- **Acceptance criteria:**
  - Subagents receive child_context_id and do not become top-level peer sessions by default.
  - Subagent output returns to the parent turn as summary or summary_plus_metadata.
  - Subagent return summaries are represented as child-work outputs for parent-turn consumption, not as generic context-index inputs.
  - Agent-team members each have ManagedSession ids under a shared session_group_id.
  - Team peer messaging emits direct team.message.sent events.
  - Usage rolls up correctly for child contexts and session groups.
  - Teardown and archival behavior is group-aware for teams and parent-turn-owned for subagents.
- **Owned coverage:**
  - **DESIGN-REQ-005:** Owns ChildContext and SessionGroup records.
  - **DESIGN-REQ-016:** Owns distinction between subagents and teams.
  - **DESIGN-REQ-017:** Owns child-work APIs and events.
  - **DESIGN-REQ-019:** Owns child-work API surface.
  - **DESIGN-REQ-020:** Owns child and team event streams.
  - **DESIGN-REQ-023:** Owns managed_subagent_total and managed_team_sessions_total telemetry inputs.
  - **DESIGN-REQ-026:** Owns SessionGroup compatibility mapping to shared plane.
  - **DESIGN-REQ-028:** Owns rollout phase 4.
  - **DESIGN-REQ-029:** Owns non-goal of unifying all collaboration products into one session type.
  - **DESIGN-REQ-030:** Owns open question about long-running subagent promotion.
- **Handoff:** Build subagents and agent teams as separate child-work primitives. The story should prove identity, lifecycle, communication, and usage semantics differ correctly instead of sharing one overloaded child-session abstraction.

### STORY-007: Implement Claude multi-surface projection and cloud handoff semantics

- **Short name:** `claude-surface-handoff`
- **Why:** The design explicitly decouples execution owner from UI. This story delivers the user-visible surface behavior after the core registry is in place.
- **Description:** As a user, I can attach, detach, reconnect, and hand off Claude sessions across terminal, IDE, desktop, web, mobile, scheduler, and SDK surfaces without losing the distinction between local execution, Remote Control projection, and cloud execution.
- **Independent test:** Attach multiple surfaces to a local session, disconnect and reconnect a projection, resume on another surface, perform a cloud handoff, and assert execution_owner preservation or new-session creation according to the documented cases.
- **Dependencies:** STORY-001
- **Needs clarification:** How should cloud handoff summaries be versioned and audited when they seed a new session?
- **Scope:**
  - SurfaceBinding capabilities and connection state
  - AttachSurface and DetachSurface behavior
  - disconnect, reconnect, waiting, and resume semantics
  - Remote Control projection semantics
  - local-to-cloud and desktop-to-web cloud handoff lineage with seed artifact references
- **Out of scope:**
  - Cloud VM provisioning internals
  - Full mobile/web UI implementation
  - Checkpoint payload movement across handoff beyond seed artifact pointers
- **Acceptance criteria:**
  - A session has one primary surface and may have multiple projections.
  - Remote Control projection never changes execution_owner.
  - Surface disconnect does not fail the session unless the runtime exits.
  - Resume on a different surface preserves canonical identity when execution_owner remains the same.
  - Cloud handoff creates a new session with handoff_from_session_id and seed artifact refs.
  - Handoff lineage does not use Remote Control projection fields.
- **Owned coverage:**
  - **DESIGN-REQ-002:** Owns user-facing separation of execution and presentation.
  - **DESIGN-REQ-003:** Owns local, Remote Control, cloud, scheduled, and SDK shapes at surface level.
  - **DESIGN-REQ-004:** Owns Remote Control versus cloud handoff behavior.
  - **DESIGN-REQ-006:** Owns surface binding lifecycle.
  - **DESIGN-REQ-018:** Owns multi-surface attachment and reconnect behavior.
  - **DESIGN-REQ-019:** Owns AttachSurface and DetachSurface APIs.
  - **DESIGN-REQ-020:** Owns surface events.
  - **DESIGN-REQ-025:** Owns cloud/local distinction for incident response and compliance.
  - **DESIGN-REQ-028:** Owns rollout phase 5.
  - **DESIGN-REQ-030:** Owns open question about handoff summary versioning and audit.
- **Handoff:** Build multi-surface projection and cloud handoff as one story once metadata sessions exist. The core test is that projection preserves session identity while cloud transfer creates lineage to a distinct session.

### STORY-008: Export Claude session telemetry, audits, storage retention, and compatibility views

- **Short name:** `claude-telemetry-audit`
- **Why:** The final rollout phase turns normalized session state into enterprise-visible governance and operations signals while preserving shared-plane compatibility.
- **Description:** As an administrator, I can query Claude managed-session metrics, logs, traces, hook audits, policy trust levels, provider caveats, retention classes, artifact pointers, usage rollups, and Codex-compatible compatibility views.
- **Independent test:** Feed fixture Claude OTel events, hook audit events, policy trust states, checkpoint/context artifact pointers, and usage records into the exporter and verify normalized metrics, spans, audit rows, retention classes, and shared-plane compatibility views use canonical `session_id` naming without full payload storage.
- **Dependencies:** STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007
- **Needs clarification:** None
- **Scope:**
  - OTel-to-shared telemetry normalization
  - recommended metrics and span boundaries
  - HookAudit stream, provider_mode-aware reporting, PolicyTrustLevel, and protected-path reporting
  - ArtifactIndex, UsageStore, retention class metadata, optional export sinks, and compliance archive pointers
  - shared interface compatibility using canonical `session_id` naming
- **Out of scope:**
  - Building a full dashboard UI beyond query/export surfaces
  - Changing core shared interfaces
  - Centralizing full transcripts or source diffs by default
- **Acceptance criteria:**
  - Claude OTel input maps to shared metrics, logs/events, and optional traces.
  - Metrics include active sessions, turn duration, decisions, hooks, checkpoints, compactions, subagents, teams, policy fetch failures, reconnects, and token usage with documented dimensions.
  - Trace spans include session.bootstrap, policy.resolve, turn.process, decision.resolve, hook.execute, tool.execute, checkpoint.capture, session.compact, checkpoint.restore, subagent.run, and team.session.run.
  - HookAudit includes hook name, source scope, event type, matcher, and outcome.
  - Retention classes are policy-driven and represented for session metadata, event logs, usage, audit metadata, and checkpoint payloads.
  - Shared interfaces remain unchanged and follow the unified session_id naming convention; legacy thread_id references are removed entirely per Rule 156.
- **Owned coverage:**
  - **DESIGN-REQ-020:** Owns complete append-only event export views.
  - **DESIGN-REQ-021:** Owns ArtifactIndex and UsageStore reporting surfaces.
  - **DESIGN-REQ-022:** Owns policy-driven retention classes.
  - **DESIGN-REQ-023:** Owns OTel normalization, metrics, and spans.
  - **DESIGN-REQ-024:** Owns hook audit, provider caveats, protected-path reporting, and governance layers.
  - **DESIGN-REQ-025:** Owns reporting distinctions for local, Remote Control, and cloud execution.
  - **DESIGN-REQ-026:** Owns shared interface compatibility with unified session_id naming and no legacy aliases.
  - **DESIGN-REQ-027:** Owns adapter-specific telemetry translation.
  - **DESIGN-REQ-028:** Owns rollout phase 6.
  - **DESIGN-REQ-029:** Owns non-goal of central transcript/diff storage by default.
- **Handoff:** Build enterprise telemetry and audit export as the final story. It should consume the normalized records from earlier stories, produce shared observability outputs, keep payload storage pointer-based, and prove Codex-compatible views remain stable.

## Coverage Matrix

| Coverage point | Owning stories |
|---|---|
| DESIGN-REQ-001 - Preserve shared Managed Session Plane nouns | STORY-001 |
| DESIGN-REQ-002 - Separate execution owner from presentation surface | STORY-001, STORY-007 |
| DESIGN-REQ-003 - Model Claude supported session shapes | STORY-001, STORY-007 |
| DESIGN-REQ-004 - Keep Remote Control distinct from cloud handoff | STORY-001, STORY-007 |
| DESIGN-REQ-005 - Provide canonical Claude domain records | STORY-001, STORY-002, STORY-004, STORY-005, STORY-006 |
| DESIGN-REQ-006 - Implement session, turn, decision, checkpoint, and surface lifecycles | STORY-001, STORY-003, STORY-005, STORY-007 |
| DESIGN-REQ-007 - Compile Claude policy into versioned PolicyEnvelope | STORY-002 |
| DESIGN-REQ-008 - Respect managed settings precedence and fetch states | STORY-002 |
| DESIGN-REQ-009 - Represent BootstrapPreferences honestly for Claude | STORY-002 |
| DESIGN-REQ-010 - Normalize the full Claude decision pipeline | STORY-003 |
| DESIGN-REQ-011 - Track DecisionPoint provenance and outcomes | STORY-003 |
| DESIGN-REQ-012 - Make context lifecycle inspectable | STORY-004 |
| DESIGN-REQ-013 - Separate memory guidance from enforcement | STORY-004 |
| DESIGN-REQ-014 - Model compaction as a new context epoch | STORY-004 |
| DESIGN-REQ-015 - Make checkpointing and rewind first-class | STORY-005 |
| DESIGN-REQ-016 - Distinguish subagents from agent teams | STORY-006 |
| DESIGN-REQ-017 - Support child work APIs and events | STORY-006 |
| DESIGN-REQ-018 - Support multi-surface attachment and reconnect behavior | STORY-001, STORY-007 |
| DESIGN-REQ-019 - Expose high-level session, turn, policy, decision, checkpoint, child-work, and subscription APIs | STORY-001, STORY-002, STORY-005, STORY-006, STORY-007 |
| DESIGN-REQ-020 - Emit append-only normalized event streams | STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008 |
| DESIGN-REQ-021 - Keep the storage model payload-light | STORY-001, STORY-004, STORY-005, STORY-008 |
| DESIGN-REQ-022 - Provide policy-driven retention classes | STORY-005, STORY-008 |
| DESIGN-REQ-023 - Normalize Claude OTel telemetry into shared observability | STORY-006, STORY-008 |
| DESIGN-REQ-024 - Model layered security and governance explicitly | STORY-002, STORY-003, STORY-008 |
| DESIGN-REQ-025 - Preserve local-code residency expectations | STORY-003, STORY-004, STORY-005, STORY-007, STORY-008 |
| DESIGN-REQ-026 - Maintain Codex plane compatibility without hiding semantic mismatches | STORY-002, STORY-006, STORY-008 |
| DESIGN-REQ-027 - Keep runtime-specific wire complexity inside adapters | STORY-001, STORY-008 |
| DESIGN-REQ-028 - Follow phased rollout dependencies | STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008 |
| DESIGN-REQ-029 - Respect explicit non-goals | STORY-001, STORY-003, STORY-006, STORY-008 |
| DESIGN-REQ-030 - Track open questions as downstream clarifications | STORY-002, STORY-005, STORY-006, STORY-007 |

## Dependencies

- **STORY-001** depends on: None
- **STORY-002** depends on: STORY-001
- **STORY-003** depends on: STORY-002
- **STORY-004** depends on: STORY-001
- **STORY-005** depends on: STORY-004
- **STORY-006** depends on: STORY-001
- **STORY-007** depends on: STORY-001
- **STORY-008** depends on: STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007

## Out-of-Scope Items and Rationale

- **Do not reimplement Anthropic proprietary local protocol.** Runtime-specific protocol details belong inside Claude adapters.
- **Do not replace git history with checkpoints.** Checkpoints are session-native rewind points, not source-control replacement.
- **Do not centrally store every transcript or file diff by default.** The storage model is metadata-first and pointer-based to preserve local-code residency.
- **Do not make cloud and local execution indistinguishable.** Execution owner affects security, approvals, telemetry, incident response, and compliance.
- **Do not simulate unsupported native admin defaults.** Claude bootstrap preferences must be labeled templates, not managed settings.
- **Do not collapse subagents and agent teams into one session type.** They differ in identity, communication, lifecycle, permissions, and usage accounting.

## Recommended First Story

`STORY-001` is the recommended first story to run through `/speckit.specify` because it establishes canonical Claude session identity, lifecycle, lineage, and event storage needed by every later story.

## Breakdown Guardrails

- No `spec.md` files were created or modified during this breakdown.
- No directories under `specs/` were created during this breakdown.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- `/speckit.verify` should be run after implementation to compare final behavior against the original design preserved through specify.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
