# MM-317 Story Breakdown: Claude Code Managed Sessions

Source design: `docs/ManagedAgents/ClaudeCodeManagedSessions.md`
Story extraction date: 2026-04-15T07:15:15Z

## Design Summary

The design defines Claude Code as a thin specialization of MoonMind's shared Managed Session Plane. It preserves shared session, turn, work, decision, policy, artifact, and usage abstractions while adding Claude-specific execution ownership, surface projection, managed-policy compilation, richer decision provenance, typed context snapshots, checkpoint and rewind behavior, subagent and team modeling, append-only events, payload-light storage, and governance-grade telemetry. It explicitly avoids reimplementing Anthropic proprietary protocols, hiding cloud/local execution differences, centrally storing all transcripts or diffs by default, or pretending Claude has native admin-managed defaults equivalent to Codex.

## Coverage Points

- **DESIGN-REQ-001 - Preserve shared managed-session abstractions** (requirement, 2.1 Preserve shared abstractions; 5. Shared abstractions retained from the Codex design): Claude Code sessions must use the shared SessionPlane, ManagedSession, Turn, WorkItem, DecisionPoint, PolicyEnvelope, RuntimeAdapter, ArtifactStore, and UsageLedger concepts unless Claude semantics make a shared noun incorrect.
- **DESIGN-REQ-002 - Separate execution owner, surface binding, and projection mode** (state-model, 2.2 Separate execution from presentation; 7.1 Canonical runtime axes): The plane must model where execution happens separately from where humans interact and whether a surface is primary, projected, or a handoff.
- **DESIGN-REQ-003 - Support all declared Claude session shapes** (requirement, 7.2 Supported session shapes): Local interactive, local plus Remote Control, cloud interactive, cloud scheduled, desktop scheduled, SDK embedded, subagent child context, agent-team lead, and agent-team teammate shapes must be representable.
- **DESIGN-REQ-004 - Model Remote Control, cloud handoff, subagents, and teams distinctly** (constraint, 7.3 Critical execution distinctions): Remote Control is a projection of existing local execution, cloud handoff creates a new session with lineage, subagents are child contexts, and teams are grouped sibling sessions.
- **DESIGN-REQ-005 - Provide a Claude Code managed-session plane architecture** (integration, 8. High-level architecture): The design requires a plane with registry, policy resolver, decision engine, hook dispatcher, context resolver, checkpoint service, child/team coordinators, artifact index, telemetry, and runtime adapters.
- **DESIGN-REQ-006 - Persist canonical domain records** (state-model, 9. Canonical domain model): ManagedSession, Turn, WorkItem, DecisionPoint, PolicyEnvelope, ContextSnapshot, Checkpoint, ChildContext, and SessionGroup need typed fields sufficient for Claude semantics.
- **DESIGN-REQ-007 - Implement lifecycle state machines** (state-model, 10. Lifecycle state machines): Session, turn, decision, checkpoint, and surface-binding lifecycles must have explicit state transitions, including waiting, compacting, rewinding, reconnecting, and failed states.
- **DESIGN-REQ-008 - Compile Claude policy into a versioned PolicyEnvelope** (security, 11. Policy model): Required policy, bootstrap preferences, managed settings, permission rules, hooks, MCP policy, sandbox settings, memory policy, and runtime constraints must become a frozen effective policy.
- **DESIGN-REQ-009 - Honor managed settings source precedence and fetch states** (security, 11.3 Managed settings source resolution; 11.4 Effective policy assembly algorithm): Server-managed settings take precedence over endpoint-managed settings, local overrides are disallowed for managed settings, and fetch outcomes such as cache hit, fetched, fetch failed, and fail closed must be recorded.
- **DESIGN-REQ-010 - Support the Claude policy handshake** (security, 11.5 Policy handshake state): Startup must represent not-required, awaiting fetch, awaiting user security dialog, accepted, rejected, and fail-closed outcomes, including interactive and non-interactive behavior.
- **DESIGN-REQ-011 - Normalize the Claude decision pipeline** (security, 12. Decision pipeline): Session guards, pretool hooks, permission rules, protected paths, permission mode baselines, sandbox substitution, auto-mode classifier, prompts or headless resolution, runtime execution, posttool hooks, and checkpoint capture must be represented as provenance-bearing decisions or work items.
- **DESIGN-REQ-012 - Represent permission modes and safety controls accurately** (security, 12.2 Detailed semantics by stage; 21. Security and governance): Permission modes, deny/ask/allow order, protected paths, sandboxing, hooks, classifier checks, user dialogs, and runtime isolation need distinct semantics instead of collapsing into approval prompts.
- **DESIGN-REQ-013 - Track typed context and memory sources** (artifact, 13. Context and memory model): Startup and on-demand context sources such as system prompts, output styles, CLAUDE.md variants, auto memory, MCP manifests, skill descriptions, invoked skill bodies, hooks, file reads, nested rules, and summaries must be typed.
- **DESIGN-REQ-014 - Make compaction context-aware and epoch-based** (state-model, 13.3 Compaction-aware context model; 13.4 Compaction behavior): Context segments need reinjection policies, compaction must create a new ContextSnapshot epoch, and compaction work must be emitted explicitly.
- **DESIGN-REQ-015 - Separate memory guidance from enforceable policy** (constraint, 13.5 Managed memory guidance vs enforcement): CLAUDE.md, auto memory, and rules are guidance, while managed settings and permission rules are enforcement; the plane must not treat memory artifacts as hard policy.
- **DESIGN-REQ-016 - Make checkpointing and rewind first-class** (state-model, 14. Checkpointing and rewind): User prompts and file edits create checkpoint records, rewind can restore code, conversation, both, or summarize from a checkpoint, and lineage must preserve pre-rewind provenance.
- **DESIGN-REQ-017 - Model subagents as child contexts** (state-model, 15.1 Subagents): Subagents are parent-turn-owned child contexts with isolated context windows, inherited execution owner, summary-style return shape, and usage that rolls up to the parent session.
- **DESIGN-REQ-018 - Model agent teams as grouped sibling sessions** (state-model, 15.2 Agent teams; 15.3 Why the split matters): Agent teams use separate ManagedSession records under SessionGroup with direct peer communication, group-aware teardown, and distinct usage and surface behavior.
- **DESIGN-REQ-019 - Support durable multi-surface behavior** (requirement, 16. Multi-surface behavior): SurfaceBinding records must support capabilities, last-seen time, attach/detach rules, reconnects, Remote Control projections, and handoff lineage without mutating execution owner incorrectly.
- **DESIGN-REQ-020 - Expose normalized session, turn, policy, decision, checkpoint, child-work, and event APIs** (integration, 17. API surface of the plane): The plane should keep Codex-like high-level verbs while adding Claude-specific extensions for policy dialog, checkpoint restore, summarize-from-checkpoint, subagents, teams, and group subscriptions.
- **DESIGN-REQ-021 - Emit append-only normalized events** (observability, 18. Event model): Session, surface, policy, turn, work, decision, and child-work events must be normalized and append-only for auditability and cross-runtime compatibility.
- **DESIGN-REQ-022 - Keep storage payload-light and source-residency-aware** (artifact, 19. Storage model; 3.1 Functional goals): The central plane stores ids, envelopes, policy versions, usage counters, and artifact pointers while runtime-local stores retain transcripts, full file reads, checkpoint payloads, and local caches by default.
- **DESIGN-REQ-023 - Normalize telemetry and usage export** (observability, 20. Observability): Claude OpenTelemetry, metrics, logs/events, optional traces, usage rollups, hooks, checkpoints, compactions, policy failures, reconnects, and token usage should map into the shared telemetry schema.
- **DESIGN-REQ-024 - Represent governance provenance and trust levels** (security, 21. Security and governance): Policy trust level, provider mode, protected paths, hook audit provenance, and cloud-versus-local execution distinctions are required for accurate governance and compliance reporting.
- **DESIGN-REQ-025 - Maintain Codex-plane compatibility without misleading aliases** (migration, 22. Compatibility strategy with the Codex plane): Shared interfaces remain compatible, Claude-specific fields are extensions, semantic mismatches are declared, and legacy Codex thread naming must be removed from Claude internal contracts rather than kept as aliases.
- **DESIGN-REQ-026 - Deliver in dependency-aware phases** (migration, 23. Rollout plan): The design proposes six phases from metadata-only registry through policy, context/checkpoints, child work, remote projection/cloud handoff, and enterprise telemetry/audits.
- **DESIGN-REQ-027 - Respect explicit non-goals** (non-goal, 4. Non-goals): The implementation must not reimplement Anthropic proprietary local protocols, replace git history, centrally store all transcripts/diffs by default, hide cloud/local differences, simulate nonexistent managed defaults, or flatten different collaboration products into one session type.
- **DESIGN-REQ-028 - Resolve open product and governance choices transparently** (constraint, 24. Open questions; 25. Recommended implementation stance): Checkpoint payload residency, policy fetch visibility, subagent promotion, schedule templates, cloud handoff summary audit, and Claude managed-default emulation should remain explicit decisions while the implementation stays a thin specialization of the shared plane.

## Ordered Story Candidates

### STORY-001: Register Claude managed sessions with shared lifecycle records

- Short name: `claude-session-registry`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (2.1 Preserve shared abstractions; 5. Shared abstractions retained from the Codex design; 8. High-level architecture; 9. Canonical domain model; 10. Lifecycle state machines; 4. Non-goals)
- Dependencies: None
- Why: This is the foundation that makes a Claude session visible, resumable, auditable, and compatible with existing control-plane concepts before Claude-specific features are added.
- Independent test: Create local, cloud, scheduled, and SDK-hosted Claude session records through the plane boundary, advance a turn through representative states, emit work items, resume and archive the session, and verify the stored records and events use shared session terminology without Codex thread aliases.
- Scope:
  - Persist Claude ManagedSession, Turn, WorkItem, DecisionPoint, PolicyEnvelope, ArtifactStore reference, and UsageLedger-facing metadata.
  - Record append-only event history for core session, turn, and work lifecycle changes.
  - Support resume, fork, archive, ended, failed, waiting, compacting, and rewinding state semantics at the registry boundary.
  - Represent runtime adapter binding without exposing Claude-only wire details.
- Out of scope:
  - Implementing Anthropic proprietary local protocols.
  - Checkpoint restore behavior beyond recording lifecycle placeholders.
  - Remote Control and cloud handoff details, which are owned by STORY-002.
- Acceptance criteria:
  - Given a Claude Code run is created, when the plane stores it, then it has runtime_family `claude_code`, a stable `session_id`, execution metadata, lifecycle state, policy and context references, lineage fields, and usage/artifact references.
  - Given a turn emits work, when lifecycle transitions occur, then Turn and WorkItem records are appended with normalized states and timestamps.
  - Given a session is idle, compacting, rewinding, archiving, ended, or failed, when the state is read, then the state matches the declared session lifecycle semantics.
  - Given an implementation path would require storing full transcripts or file diffs centrally by default, then the registry stores only metadata and artifact pointers.
  - Given internal Claude managed-session contracts are inspected, then legacy `thread_id` and `child_thread` aliases are absent.
- Owned coverage:
  - DESIGN-REQ-001: Owns shared abstraction preservation.
  - DESIGN-REQ-005: Owns core plane architecture scaffolding.
  - DESIGN-REQ-006: Owns canonical record persistence.
  - DESIGN-REQ-007: Owns base lifecycle state transitions.
  - DESIGN-REQ-027: Owns registry-level non-goals.
- Needs clarification: None

### STORY-002: Model Claude execution ownership and multi-surface projection

- Short name: `claude-surface-ownership`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (2.2 Separate execution from presentation; 7. Runtime model; 16. Multi-surface behavior; 21.6 Cloud vs local security)
- Dependencies: STORY-001
- Why: Claude Code surfaces do not reveal where code runs. Accurate execution ownership protects approvals, telemetry, incident response, and user expectations.
- Independent test: Create a local terminal session, attach web and mobile Remote Control projections, disconnect and reconnect a surface, perform a local-to-cloud handoff, and verify the local session retains `execution_owner = local_process` while handoff creates a new cloud session with lineage.
- Scope:
  - Add execution_owner, surface_kind, and projection_mode as independent axes.
  - Represent supported local, cloud, scheduled, SDK, Remote Control, subagent, and team shapes.
  - Persist SurfaceBinding records with capabilities, last seen time, connection state, and projection mode.
  - Model Remote Control as projection on an existing local session and cloud handoff as a new session with lineage.
- Out of scope:
  - Policy resolution details.
  - Team peer-message semantics beyond recognizing team-owned surfaces.
- Acceptance criteria:
  - Given a web or mobile surface attaches through Remote Control, when the session is read, then execution_owner remains `local_process` and a remote_projection SurfaceBinding is added.
  - Given a local-to-cloud handoff occurs, when lineage is inspected, then a new `anthropic_cloud_vm` ManagedSession exists with `handoff_from_session_id` referencing the source session.
  - Given a surface disconnects, when runtime execution remains alive, then the session enters or remains a resumable non-failed state.
  - Given a supported session shape is created, then execution owner, primary surface, projection mode, and created_by are represented without inference from UI alone.
- Owned coverage:
  - DESIGN-REQ-002: Owns orthogonal runtime axes.
  - DESIGN-REQ-003: Owns supported session-shape coverage.
  - DESIGN-REQ-004: Owns Remote Control, handoff, subagent/team distinction at the surface boundary.
  - DESIGN-REQ-019: Owns SurfaceBinding lifecycle and capabilities.
  - DESIGN-REQ-024: Owns cloud-vs-local governance distinction for surfaces.
- Needs clarification: None

### STORY-003: Compile Claude managed policy and startup handshake state

- Short name: `claude-policy-envelope`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (2.3 Treat policy as compiled runtime state; 11. Policy model; 21. Security and governance; 22.5 Known semantic mismatch)
- Dependencies: STORY-001
- Why: Claude has JSON-first managed settings and managed-only controls whose precedence differs from Codex. The plane needs one effective policy record without pretending unsupported managed defaults exist natively.
- Independent test: Run policy resolution cases for server-managed settings, endpoint-managed fallback, fetch failure, force-refresh fail-closed, interactive security-dialog accept/reject, and non-interactive startup, then verify the compiled PolicyEnvelope and policy events record the correct precedence and handshake outcome.
- Scope:
  - Resolve server-managed settings before endpoint-managed settings with first-non-empty-source-wins behavior.
  - Record policy fetch state, managed source kind, provider mode, policy trust level, and security-dialog state.
  - Compile permissions, protected paths, hooks, MCP policy, sandbox settings, memory policy, and availability controls into a frozen PolicyEnvelope version.
  - Represent BootstrapPreferences as Claude bootstrap templates, not a native admin-managed default tier.
- Out of scope:
  - Evaluating individual tool decisions, which belongs to STORY-004.
  - Building compliance dashboards, which belongs to STORY-009.
- Acceptance criteria:
  - Given server-managed settings return a non-empty configuration, when endpoint settings also exist, then endpoint settings do not override managed settings.
  - Given server-managed settings are empty and endpoint settings exist, when resolution runs, then endpoint settings apply according to Claude endpoint merge rules.
  - Given force refresh is required and fetch fails, when startup proceeds, then policy_fetch_state is `fail_closed` and startup fails closed.
  - Given risky managed hooks or custom environment variables require a user dialog, when an interactive user rejects them, then startup exits with rejected handshake state.
  - Given BootstrapPreferences are supplied for Claude, then they are represented as bootstrap templates and not labeled as a native non-overridable managed tier.
- Owned coverage:
  - DESIGN-REQ-008: Owns effective PolicyEnvelope compilation.
  - DESIGN-REQ-009: Owns managed settings precedence and fetch state.
  - DESIGN-REQ-010: Owns handshake state.
  - DESIGN-REQ-012: Owns policy-level safety controls.
  - DESIGN-REQ-024: Owns trust and provider governance fields.
  - DESIGN-REQ-025: Owns Codex semantic mismatch handling.
  - DESIGN-REQ-027: Owns no simulated admin default tier.
  - DESIGN-REQ-028: Owns explicit managed-default and fetch-visibility choices.
- Needs clarification: None

### STORY-004: Normalize Claude decision provenance across permissions, hooks, sandbox, and classifier

- Short name: `claude-decision-pipeline`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (2.4 Model deterministic and non-deterministic safety controls explicitly; 12. Decision pipeline; 21. Security and governance)
- Dependencies: STORY-001, STORY-003
- Why: Claude permissioning is more than a user prompt. Without explicit provenance, governance and operator UX will misreport why an action was allowed, asked, denied, deferred, or canceled.
- Independent test: Submit representative tool/file/network actions under each permission mode with combinations of deny, ask, allow, protected paths, hooks, sandbox availability, auto classifier outcomes, and headless mode, then verify the first matching stage and emitted DecisionPoint provenance match the design.
- Scope:
  - Evaluate session state guards, pretool hooks, permission rules, protected paths, permission mode baseline, sandbox substitution, auto-mode classifier, interactive/headless resolution, runtime execution, posttool hooks, and checkpoint-capture edges in declared order.
  - Emit DecisionPoint records with target kind/ref, origin stage, proposed action, resolution, resolved_by, timestamps, and reason.
  - Emit hook and work events for pretool and posttool stages.
  - Ensure hooks can tighten restrictions but cannot override policy deny or ask rules.
- Out of scope:
  - Policy source resolution.
  - Actual checkpoint restore implementation.
- Acceptance criteria:
  - Given matching deny, ask, and allow rules, when a proposed action is evaluated, then deny wins before ask before allow.
  - Given a protected path write with bypass permissions enabled, when evaluated, then the decision origin records protected-path handling rather than claiming a simple bypass allow.
  - Given a PreToolUse hook returns a mutation or denial, when the decision is stored, then the hook outcome and provenance are visible and cannot override a stricter managed policy rule.
  - Given auto mode sees an unsafe or ambiguous action, when classifier review runs, then classifier resolution is recorded distinctly from user approval.
  - Given a headless session cannot prompt, when a decision remains unresolved, then the policy-defined deny or defer outcome is recorded.
- Owned coverage:
  - DESIGN-REQ-011: Owns decision pipeline ordering and semantics.
  - DESIGN-REQ-012: Owns permission modes and safety-control accuracy.
  - DESIGN-REQ-021: Owns decision/work event emission for this pipeline.
  - DESIGN-REQ-024: Owns hook/protected-path governance provenance.
- Needs clarification: None

### STORY-005: Track Claude context snapshots and compaction epochs

- Short name: `claude-context-compaction`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (2.5 Make context lifecycle inspectable; 13. Context and memory model)
- Dependencies: STORY-001, STORY-003
- Why: Claude session quality depends on runtime context sources such as CLAUDE.md, memory, rules, skills, hooks, file reads, and compaction. The control plane needs metadata and reinjection rules rather than opaque prompt blobs.
- Independent test: Bootstrap a Claude session with managed/project/local CLAUDE.md, output style, auto memory, MCP manifests, skill descriptions, hook-injected text, file reads, and invoked skill bodies; run compaction and verify a new ContextSnapshot epoch with correct segment kinds, reinjection policies, and guidance-vs-enforcement labels.
- Scope:
  - Create ContextSnapshot records with compaction_epoch and typed segments.
  - Capture startup and on-demand segment kinds, source references, load timing, token budget hints, and reinjection policy.
  - On compaction, create a new ContextSnapshot epoch, preserve transcript summary metadata, reload startup-critical context, and emit compaction work/events.
  - Mark CLAUDE.md, auto memory, and rules as guidance, not enforcement.
- Out of scope:
  - Central storage of full file-read content by default.
  - Skill materialization mechanics outside context metadata.
- Acceptance criteria:
  - Given startup context is loaded, when ContextSnapshot is read, then each startup segment has kind, source_ref, loaded_at, and reinjection_policy.
  - Given an invoked skill body or file read enters context on demand, then it is recorded with on-demand timing and budgeted or never reinjection policy as appropriate.
  - Given compaction occurs, then the old snapshot remains immutable, a new epoch is created, transcript summary is recorded, startup-critical context is reloaded, and a compaction WorkItem/event is emitted.
  - Given a memory or CLAUDE.md artifact contains behavioral guidance, then it is not represented as enforceable managed policy.
- Owned coverage:
  - DESIGN-REQ-013: Owns context-source typing.
  - DESIGN-REQ-014: Owns compaction epochs and reinjection policies.
  - DESIGN-REQ-015: Owns guidance versus enforcement distinction.
  - DESIGN-REQ-021: Owns context and compaction event emission.
  - DESIGN-REQ-022: Owns pointer-based storage for context payloads.
- Needs clarification: None

### STORY-006: Index Claude checkpoints and rewind operations with lineage

- Short name: `claude-checkpoint-rewind`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (14. Checkpointing and rewind; 17.5 Checkpoint APIs; 18.5 Work events; 19. Storage model)
- Dependencies: STORY-001, STORY-005
- Why: Claude checkpoints are first-class runtime behavior. The plane must expose checkpoint metadata and operations while keeping checkpoint payload residency policy-safe.
- Independent test: Create checkpoints around a user prompt and file edit, list them, perform conversation-only, code-only, combined restore, and summarize-from-here requests through the plane API, then verify active checkpoint cursor, `rewound_from_checkpoint_id`, preserved pre-rewind events, and runtime-local payload references.
- Scope:
  - Record checkpoint metadata for user prompts, pre-edit captures, manual captures, storage refs, restorable modes, and expiry.
  - Expose list, restore, and summarize-from-checkpoint operations.
  - Emit checkpoint capture, rewind start, and rewind complete work/events.
  - Preserve pre-rewind event logs and old checkpoint addresses until expiry or garbage collection.
- Out of scope:
  - Replacing git history with session checkpoints.
  - Mandating central storage for checkpoint payloads.
- Acceptance criteria:
  - Given a user prompt or tracked file edit occurs, then a checkpoint WorkItem and Checkpoint record are created with trigger, captures, storage_ref, restorable modes, and expiry metadata.
  - Given summarize-from-here is requested, then conversation history lineage changes without claiming disk state was restored.
  - Given a rewind occurs, then the previous event log remains available, active cursor changes, and `rewound_from_checkpoint_id` is recorded.
  - Given checkpoint payload storage policy defaults are inspected, then payloads are referenced as runtime-local by default rather than centrally stored.
- Owned coverage:
  - DESIGN-REQ-016: Owns checkpoint and rewind behavior.
  - DESIGN-REQ-020: Owns checkpoint API verbs.
  - DESIGN-REQ-021: Owns checkpoint/rewind event emission.
  - DESIGN-REQ-022: Owns metadata-first checkpoint storage.
  - DESIGN-REQ-027: Owns non-goal of replacing git history.
  - DESIGN-REQ-028: Owns checkpoint payload residency as an explicit choice.
- Needs clarification: None

### STORY-007: Represent Claude subagents and agent teams without collapsing their semantics

- Short name: `claude-child-work`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (15. Child work model: subagents and teams; 17.6 Child-work APIs; 18.7 Child-work events)
- Dependencies: STORY-001, STORY-002
- Why: Subagents and teams both look like child work, but they have different context, communication, lifecycle, usage, and session identity semantics.
- Independent test: Spawn a subagent during a parent turn and create an agent team with leader and teammates; verify the subagent receives a child_context_id rather than a top-level session, the team creates sibling ManagedSession records with one session_group_id, peer messages are recorded, and usage rolls up according to each model.
- Scope:
  - Create ChildContext records for subagents with parent session/turn, inherited execution owner, isolated context, return shape, tool and permission profiles, and status.
  - Create SessionGroup records for agent teams with leader, members, coordination mode, group status, peer messages, and group-aware teardown.
  - Roll subagent usage into parent sessions and team usage by member plus group.
  - Emit child and team events and expose spawn/create/send APIs.
- Out of scope:
  - Promotion of long-running subagents to sibling sessions unless later product decisions require it.
  - Team scheduling policies outside session grouping.
- Acceptance criteria:
  - Given a Claude subagent is spawned, then it is modeled as ChildContext with parent turn ownership, isolated context, summary-style return, and parent usage rollup.
  - Given an agent team is created, then each teammate is a ManagedSession under a SessionGroup with direct peer-message support.
  - Given group teardown occurs, then the SessionGroup lifecycle changes without treating subagents as peer sessions.
  - Given usage is reported, then subagent usage rolls into the parent session while team usage is available per session and group.
- Owned coverage:
  - DESIGN-REQ-004: Owns subagent/team distinction from critical execution distinctions.
  - DESIGN-REQ-017: Owns ChildContext semantics.
  - DESIGN-REQ-018: Owns SessionGroup/team semantics.
  - DESIGN-REQ-020: Owns child-work API verbs.
  - DESIGN-REQ-021: Owns child-work event emission.
  - DESIGN-REQ-023: Owns usage telemetry for child and team work.
- Needs clarification: None

### STORY-008: Publish Claude plane APIs, append-only events, and payload-light storage contracts

- Short name: `claude-plane-contracts`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (17. API surface of the plane; 18. Event model; 19. Storage model; 22. Compatibility strategy with the Codex plane)
- Dependencies: STORY-001
- Why: Once the core model exists, clients and adapters need contract-level guarantees around verbs, events, and storage boundaries to validate behavior independently of concrete runtime execution.
- Independent test: Run contract tests that exercise every declared API verb against a fake Claude runtime adapter, subscribe to the session and group streams, and verify emitted event names, payload shapes, metadata-only storage, artifact references, and shared-plane naming.
- Scope:
  - Expose normalized session, turn, policy, decision, checkpoint, child-work, and event-subscription API contracts.
  - Emit append-only normalized events for session, surface, policy, turn, work, decision, child, and team categories.
  - Define central stores for registry, event log, policy, context, checkpoint, artifact, and usage metadata.
  - Preserve shared interface naming and remove Claude-internal legacy thread aliases.
- Out of scope:
  - Concrete UI dashboards.
  - Provider-specific cloud execution adapters.
- Acceptance criteria:
  - Given each declared API verb is invoked with valid inputs, then a normalized response or expected validation failure is returned without requiring Claude-specific transport details.
  - Given state changes occur, then append-only event names match the declared taxonomy and are queryable by session or group.
  - Given generated artifacts, summaries, checkpoint refs, or audit records exist, then the plane stores references and metadata rather than raw full payloads by default.
  - Given shared interfaces are inspected, then Claude extensions are additive fields rather than incompatible forks of shared contracts.
- Owned coverage:
  - DESIGN-REQ-020: Owns complete API-surface coverage.
  - DESIGN-REQ-021: Owns normalized append-only event taxonomy.
  - DESIGN-REQ-022: Owns storage split and artifact pointer contracts.
  - DESIGN-REQ-025: Owns shared-interface compatibility and naming.
  - DESIGN-REQ-027: Owns non-goals around central storage and protocol reimplementation.
- Needs clarification: None

### STORY-009: Normalize Claude telemetry, audit, and governance exports

- Short name: `claude-governance-telemetry`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (3.1 Functional goals; 20. Observability; 21. Security and governance; 19. Storage model)
- Dependencies: STORY-001, STORY-003, STORY-004, STORY-007, STORY-008
- Why: Claude exposes OpenTelemetry and governance-sensitive runtime facts. MoonMind must normalize them for enterprise reporting while preserving local-code residency expectations.
- Independent test: Feed representative Claude adapter telemetry, hook executions, policy fetch failures, decisions, reconnects, checkpoint actions, subagent/team usage, and cloud/local sessions into the observability path, then verify normalized metric dimensions, trace names, hook audit records, usage rollups, and absence of raw source payloads in central exports.
- Scope:
  - Map Claude OpenTelemetry metrics, logs/events, and optional traces to shared telemetry envelopes.
  - Record normalized metrics for sessions, turns, decisions, hooks, checkpoints, compactions, subagents, teams, policy fetch failures, reconnects, and token usage.
  - Emit trace boundaries for bootstrap, policy, turns, decisions, hooks, tools, checkpoints, compaction, restore, subagents, and teams.
  - Produce hook audit records with source scope, event type, matcher, and outcome.
  - Report provider mode, policy trust level, protected-path outcomes, and cloud/local execution mode.
- Out of scope:
  - Mandating a specific external OTel backend.
  - Exporting full source files, transcripts, or diffs centrally by default.
- Acceptance criteria:
  - Given Claude OTel observations arrive, then they are mapped to shared metrics, events, and optional traces instead of a parallel telemetry pipeline.
  - Given hook execution occurs, then HookAudit includes hook name, source scope, event type, matcher, outcome, and error/noop status where relevant.
  - Given provider mode or policy trust level differs, then governance exports preserve that dimension rather than assuming uniform enforcement.
  - Given local execution or Remote Control is used, then exports identify local execution and do not imply code ran in the cloud.
  - Given telemetry is exported, then raw source code, full transcript, and full diff payloads are not required in central storage by default.
- Owned coverage:
  - DESIGN-REQ-022: Owns observability payload-light boundaries.
  - DESIGN-REQ-023: Owns telemetry metrics, logs, traces, and usage rollups.
  - DESIGN-REQ-024: Owns governance dimensions, hook audit, provider caveats, and cloud/local distinctions.
- Needs clarification: None

### STORY-010: Sequence Claude managed-session delivery with compatibility guardrails

- Short name: `claude-rollout-compatibility`
- Source reference: `docs/ManagedAgents/ClaudeCodeManagedSessions.md` (22. Compatibility strategy with the Codex plane; 23. Rollout plan; 24. Open questions; 25. Recommended implementation stance; 4. Non-goals)
- Dependencies: STORY-001
- Why: The design spans many platform areas. A rollout story keeps specify/plan work aligned with the desired order and prevents compatibility-breaking shortcuts or misleading abstractions.
- Independent test: Review downstream generated plans/tasks for Claude managed sessions and verify they follow phase dependencies, preserve shared interfaces, remove legacy thread naming, keep open questions explicit, and do not include non-goal behavior such as proprietary protocol reimplementation or central transcript storage by default.
- Scope:
  - Define implementation sequencing from metadata-only registry through policy/decisions, context/checkpoints, subagents/teams, remote projection/cloud handoff, and enterprise telemetry/audits.
  - State shared interfaces that must remain unchanged and Claude extensions that may be added.
  - Reject legacy thread aliases, hidden compatibility shims, and misleading default-policy emulation.
  - Document open questions as explicit decisions or deferred choices when they are not story-critical.
- Out of scope:
  - Implementing the functional stories themselves.
  - Creating specs during breakdown; downstream specify owns spec generation.
- Acceptance criteria:
  - Given the work is planned, then metadata-only registry precedes policy/decisions, context/checkpoints, child work, surface handoff, and enterprise telemetry phases unless a documented dependency justifies a different order.
  - Given shared plane contracts are touched, then unchanged interfaces and Claude-specific extensions are called out explicitly.
  - Given legacy Codex naming appears in Claude contracts, then it is removed rather than aliased.
  - Given an open question affects implementation behavior, then the plan records the choice, deferral, or reason it is not story-critical.
  - Given a downstream spec is produced, then it preserves `docs/ManagedAgents/ClaudeCodeManagedSessions.md` as the source design reference.
- Owned coverage:
  - DESIGN-REQ-025: Owns compatibility strategy and naming guardrails.
  - DESIGN-REQ-026: Owns phased rollout sequencing.
  - DESIGN-REQ-027: Owns explicit non-goal enforcement.
  - DESIGN-REQ-028: Owns open-question and thin-specialization guardrails.
- Needs clarification: None

## Coverage Matrix

- **DESIGN-REQ-001** -> STORY-001
- **DESIGN-REQ-002** -> STORY-002
- **DESIGN-REQ-003** -> STORY-002
- **DESIGN-REQ-004** -> STORY-002, STORY-007
- **DESIGN-REQ-005** -> STORY-001
- **DESIGN-REQ-006** -> STORY-001
- **DESIGN-REQ-007** -> STORY-001
- **DESIGN-REQ-008** -> STORY-003
- **DESIGN-REQ-009** -> STORY-003
- **DESIGN-REQ-010** -> STORY-003
- **DESIGN-REQ-011** -> STORY-004
- **DESIGN-REQ-012** -> STORY-003, STORY-004
- **DESIGN-REQ-013** -> STORY-005
- **DESIGN-REQ-014** -> STORY-005
- **DESIGN-REQ-015** -> STORY-005
- **DESIGN-REQ-016** -> STORY-006
- **DESIGN-REQ-017** -> STORY-007
- **DESIGN-REQ-018** -> STORY-007
- **DESIGN-REQ-019** -> STORY-002
- **DESIGN-REQ-020** -> STORY-006, STORY-007, STORY-008
- **DESIGN-REQ-021** -> STORY-004, STORY-005, STORY-006, STORY-007, STORY-008
- **DESIGN-REQ-022** -> STORY-005, STORY-006, STORY-008, STORY-009
- **DESIGN-REQ-023** -> STORY-007, STORY-009
- **DESIGN-REQ-024** -> STORY-002, STORY-003, STORY-004, STORY-009
- **DESIGN-REQ-025** -> STORY-003, STORY-008, STORY-010
- **DESIGN-REQ-026** -> STORY-010
- **DESIGN-REQ-027** -> STORY-001, STORY-003, STORY-006, STORY-008, STORY-010
- **DESIGN-REQ-028** -> STORY-003, STORY-006, STORY-010

## Dependencies

- **STORY-001** depends on: None
- **STORY-002** depends on: STORY-001
- **STORY-003** depends on: STORY-001
- **STORY-004** depends on: STORY-001, STORY-003
- **STORY-005** depends on: STORY-001, STORY-003
- **STORY-006** depends on: STORY-001, STORY-005
- **STORY-007** depends on: STORY-001, STORY-002
- **STORY-008** depends on: STORY-001
- **STORY-009** depends on: STORY-001, STORY-003, STORY-004, STORY-007, STORY-008
- **STORY-010** depends on: STORY-001

## Out-of-Scope Items and Rationale

- Reimplementing Anthropic proprietary local protocols is excluded so MoonMind remains an orchestration layer with runtime-specific adapter boundaries.
- Replacing git history with session checkpoints is excluded because checkpoints are session-native recovery points, not source-control substitutes.
- Central storage of every transcript, file read, checkpoint payload, or diff by default is excluded to preserve local-code residency and payload-light control-plane storage.
- Making cloud and local execution indistinguishable is excluded because execution location changes approvals, telemetry, compliance, and incident response.
- Simulating a native Claude admin-managed default layer is excluded; Claude bootstrap preferences are represented only as bootstrap templates.
- Collapsing subagents and agent teams into one child-work abstraction is excluded because their communication, lifecycle, resume, teardown, surface, and usage semantics differ.

## Coverage Gate

PASS - every major design point is owned by at least one story.
