# Claude Code Managed Sessions

**Status:** Draft
**Audience:** Managed Agents platform, runtime integration, client surfaces, security, and enterprise administration
**Related:** `docs/ManagedAgents/CodexManagedSessionPlane.md`

## 1. Executive summary

`Claude Code Managed Sessions` is the Claude Code binding of the shared Managed Session Plane abstraction. It should preserve the same core control-plane model used by the Codex design wherever possible:

- a canonical session record
- append-only event history
- normalized turn lifecycle
- normalized approval / decision points
- runtime adapters
- policy envelopes
- resumability and archiving
- usage and telemetry export

The Claude Code variant diverges from Codex in a few important ways that must be first-class in the design rather than treated as edge cases:

1. **Execution owner and UI surface are decoupled.** A Claude session may run locally while being projected into web or mobile through Remote Control, or it may run in an Anthropic-managed cloud VM for Claude Code on the web.
2. **Managed policy is JSON-first and highest-precedence.** Claude Code has managed settings, server-managed settings, endpoint-managed settings, managed-only flags, and user approval dialogs for certain risky managed controls.
3. **Permissioning is not just “request approval.”** Claude Code combines permission modes, allow/ask/deny rules, protected paths, sandboxing, hooks, and, in auto mode, a classifier.
4. **Context is a structured runtime asset.** `CLAUDE.md`, `CLAUDE.local.md`, managed `CLAUDE.md`, auto memory, rules, skills, output styles, hook-injected context, and compaction all materially affect the session.
5. **Checkpointing is first-class.** Claude automatically creates rewindable checkpoints around edits and user prompts. That belongs in the plane, not as an afterthought.
6. **Child work comes in two shapes.** Claude subagents are child contexts inside one session; agent teams are multiple sessions with direct peer communication.

This design therefore keeps the shared Managed Session Plane concepts intact, but changes the runtime adapter, policy compiler, context model, and child-session model to fit Claude Code.

---

## 2. Design principles

### 2.1 Preserve shared abstractions

The Claude design should keep the same top-level nouns as the Codex design unless Claude’s runtime makes them incorrect. The shared nouns are:

- **SessionPlane**
- **ManagedSession**
- **Turn**
- **WorkItem**
- **DecisionPoint**
- **PolicyEnvelope**
- **RuntimeAdapter**
- **ArtifactStore**
- **UsageLedger**

Where Codex uses `thread`, the Claude plane should still normalize to the same shared `ManagedSession` abstraction, even if the Claude runtime itself exposes the notion as a “session”.

### 2.2 Separate execution from presentation

Claude Code surfaces do not always imply where code actually runs. The plane must never infer execution semantics from UI alone.

- `terminal`, `vscode`, `jetbrains`, and `desktop` usually mean **local execution**.
- `web` may mean **cloud execution** or merely a **projection of a local session** through Remote Control.
- `mobile` may also mean **cloud execution** or **Remote Control projection**.

The plane therefore models:

- **execution owner**
- **surface bindings**
- **projection mode**

as separate fields.

### 2.3 Treat policy as compiled runtime state

Claude Code policy is not a single knob. The runtime behavior depends on:

- managed settings source resolution
- permission mode
- allow / ask / deny rules
- protected path policy
- hook registry
- sandbox configuration
- provider and surface constraints
- optional auto-mode classifier behavior

The plane should compile these into one effective `PolicyEnvelope` that is attached to the session and versioned over time.

### 2.4 Model deterministic and non-deterministic safety controls explicitly

Codex’s public app-server contract centers approvals. Claude requires a richer decision pipeline:

- deterministic deny / ask / allow rules
- deterministic hook-side mutation / deny
- deterministic protected-path handling
- sandbox boundary checks
- classifier-based auto-mode checks
- interactive approval prompts

The plane should normalize all of these into one `DecisionPoint` record with provenance.

### 2.5 Make context lifecycle inspectable

For Claude, session quality depends heavily on what entered context, why, and whether it survives compaction. The plane must record context sources and reinjection rules rather than treating prompt state as opaque.

### 2.6 Keep runtime-specific complexity inside adapters

The plane should not expose Claude-only wire assumptions. Unlike Codex, Claude does not expose one universal public app-server style transport for all local surfaces. The shared plane should define normalized APIs and events; the Claude adapter is responsible for producing them.

---

## 3. Goals

## 3.1 Functional goals

The plane must:

1. Represent every Claude Code run as a canonical `ManagedSession`.
2. Support local, cloud, Remote Control, scheduled, and SDK-hosted session origins.
3. Normalize the turn / tool / approval / hook / compaction / checkpoint lifecycle.
4. Resolve managed policy and settings precedence correctly.
5. Preserve resumability, forkability, rewindability, and archiving.
6. Distinguish subagent child contexts from agent-team sibling sessions.
7. Export enough telemetry for enterprise governance without requiring central storage of source code.
8. Remain structurally compatible with the Codex Managed Session Plane.

## 3.2 Operational goals

The plane should:

- survive client reconnects
- survive multi-surface attachment and detachment
- tolerate best-effort policy fetch with optional fail-closed startup
- support background and headless flows
- handle compaction without losing policy-critical context
- preserve auditable provenance for sensitive decisions

---

## 4. Non-goals

This design does **not** attempt to:

- reimplement Anthropic’s proprietary local protocol
- replace git history with session checkpoints
- centrally store every transcript or file diff by default
- make cloud and local execution indistinguishable when they are not
- simulate Claude features that do not exist natively, such as a true admin-managed default layer that users can later override
- unify all collaboration products into one session type when the runtime semantics differ

---

## 5. Shared abstractions retained from the Codex design

This document intentionally preserves the following control-plane seams from the Codex design.

## 5.1 SessionPlane

The authoritative control-plane service for session truth. Responsibilities:

- session registry
- lifecycle transitions
- append-only event log
- policy attachment
- approval / decision orchestration
- runtime binding
- usage aggregation
- artifact references
- archival and resumption metadata

## 5.2 ManagedSession

The canonical unit of resumable work. A `ManagedSession` is the cross-runtime record that survives across surfaces and reconnects.

## 5.3 Turn

A bounded unit of user or scheduler input processed by the runtime, including context gathering, tool use, verification, interruptions, and completion.

## 5.4 WorkItem

A normalized event-bearing work unit emitted during a turn. In Codex these often map to streamed `item/*` entities. In Claude they may map to:

- tool calls
- hook invocations
- checkpoint creation
- compaction
- subagent execution
- team messaging
- user-input requests
- permission prompts

## 5.5 DecisionPoint

A normalized approval or denial gate. This preserves the Codex abstraction but broadens it for Claude. A `DecisionPoint` may be resolved by:

- policy
- hook
- sandbox
- classifier
- user interaction
- runtime cancellation

## 5.6 PolicyEnvelope

The compiled, versioned session policy that results from all relevant settings sources and runtime constraints.

## 5.7 RuntimeAdapter

The runtime-specific binder that translates between plane events and runtime operations.

## 5.8 ArtifactStore

A store of references to runtime-local artifacts such as summaries, checkpoint references, audit trails, and exported diffs. The design assumes metadata-first storage and pointer-based retrieval.

## 5.9 UsageLedger

A normalized usage and telemetry view across sessions, subagents, teams, and schedules.

---

## 6. Claude-specific deltas from the Codex session plane

| Area | Codex binding | Claude Code binding | Plane decision |
|---|---|---|---|
| Managed policy | `requirements.toml` plus `managed_config.toml` | managed settings JSON, server-managed settings, endpoint-managed settings, managed-only flags | keep shared `PolicyEnvelope`, add `managed_source_kind` and `policy_fetch_state` |
| Managed defaults | native concept | no equivalent native admin “default but user-overridable” tier | preserve abstraction but mark admin-managed defaults unsupported; emulate only via bootstrap templates |
| Transport | public app-server JSON-RPC with approvals and streamed events | no single public universal local control protocol across all local surfaces | keep normalized session APIs; implement Claude adapter per runtime/surface |
| Approval model | command/file approval requests | permission modes, rules, protected paths, hooks, sandboxing, classifier, dialogs | broaden shared `DecisionPoint` model |
| Context model | transcript, config, skills, compaction | `CLAUDE.md`, auto memory, rules, skill descriptions, invoked skill bodies, output styles, hook-injected context, compaction | make `ContextSnapshot` typed and reload-aware |
| Child work | subagents inherited from current policy; explicit workflows | subagents with own context window and independent permissions; agent teams as separate sessions | distinguish `ChildContext` from `SessionGroup` |
| Resume / fork | thread resume and fork | session resume, rewind, summarize-from-here, cloud handoff, Remote Control projection | keep session lineage graph |
| Surface model | local TUI can attach to remote app-server | local terminal/IDE/Desktop, Remote Control projections, cloud web/mobile | split `execution_owner` from `surface_bindings` |
| Checkpointing | not a first-class public session-plane primitive | automatic checkpoint capture and rewind | add `CheckpointLog` as shared plane extension |
| Telemetry | streamed events and app-server metrics | OpenTelemetry metrics, logs/events, optional traces | normalize to common telemetry envelopes |

---

## 7. Runtime model

## 7.1 Canonical runtime axes

Every Claude session is defined by three orthogonal axes:

```yaml
execution_owner:
  - local_process
  - anthropic_cloud_vm
  - sdk_host

surface_kind:
  - terminal
  - vscode
  - jetbrains
  - desktop
  - web
  - mobile
  - scheduler
  - channel
  - sdk

projection_mode:
  - primary
  - remote_projection
  - handoff
```

Interpretation:

- **execution owner** tells us where tools, filesystem access, and command execution actually happen.
- **surface kind** tells us where the human or automation is interacting.
- **projection mode** tells us whether the surface is the primary host, a live window into another host, or a new session derived from another one.

## 7.2 Supported session shapes

| Session shape | execution owner | primary surfaces | Notes |
|---|---|---|---|
| Local interactive | `local_process` | terminal, vscode, jetbrains, desktop | Full local filesystem/tools/MCP semantics |
| Local + Remote Control | `local_process` | web, mobile, terminal, desktop | Same session; web/mobile are projections only |
| Cloud interactive | `anthropic_cloud_vm` | web, mobile, desktop | Separate session running in Anthropic-managed infrastructure |
| Cloud scheduled | `anthropic_cloud_vm` | scheduler, web, desktop | Session instantiated from a schedule template |
| Desktop scheduled | `local_process` | scheduler, desktop | New local session launched on the user’s machine |
| SDK embedded | `sdk_host` | sdk | Same Claude agent loop and tools, but owned by host process |
| Subagent child context | inherits parent execution owner | hidden or runtime-internal | Same session tree, separate context window |
| Agent-team lead | any of the above except subagent | terminal, desktop, web | Own session id, group leader |
| Agent-team teammate | same owner class as created runtime | terminal, desktop, web | Separate session id, grouped under team |

## 7.3 Critical execution distinctions

### Remote Control is not cloud execution

Remote Control attaches a web/mobile surface to a session that continues to run on the user’s own machine. It must not mint a new execution owner. The plane models this as a new `SurfaceBinding` on the existing `ManagedSession`.

### Cloud handoff is not Remote Control

A local-to-cloud handoff should create a **new** session with a new `session_id`, `execution_owner = anthropic_cloud_vm`, and a lineage edge back to the source session. It is a fork / transfer, not a projection.

### Subagents are not separate user-visible sessions

A subagent is a child context that reports back to its caller. It inherits lineage from the parent turn and should be modeled as `ChildContext`, not as a top-level peer session unless runtime behavior requires promotion.

### Agent teams are separate sessions

Agent teammates can communicate directly and have independent state. They should be modeled as sibling `ManagedSession` records under a `SessionGroup`.

---

## 8. High-level architecture

```text
+-------------------------+     +------------------------------+
| Client / Surface Layer  |     | Enterprise Admin Plane       |
|-------------------------|     |------------------------------|
| Terminal                |     | Server-managed settings      |
| VS Code                 |     | Endpoint-managed settings    |
| JetBrains               |     | Policy templates             |
| Desktop                 |     | Audit / compliance           |
| Web / Mobile            |     +---------------+--------------+
| Schedulers / Channels   |                     |
| SDK Hosts               |                     v
+------------+------------+     +------------------------------+
             |                  | ClaudeCodeManagedSessionPlane |
             |                  |------------------------------|
             +----------------->| Session Registry             |
                                | Policy Resolver              |
                                | Decision Engine              |
                                | Hook Dispatcher              |
                                | Context Resolver             |
                                | Checkpoint Service           |
                                | Child Context Coordinator    |
                                | Session Group Coordinator    |
                                | Artifact Index               |
                                | Usage / Telemetry            |
                                +---------------+--------------+
                                                |
                                                v
                                +------------------------------+
                                | Claude Runtime Adapters      |
                                |------------------------------|
                                | Local CLI / IDE adapter      |
                                | Remote Control adapter       |
                                | Cloud session adapter        |
                                | Agent SDK adapter            |
                                +---------------+--------------+
                                                |
                                                v
                                +------------------------------+
                                | Runtime-Owned Data           |
                                |------------------------------|
                                | Local transcripts / caches   |
                                | Managed settings cache       |
                                | Checkpoints                  |
                                | Cloud VM session state       |
                                | OTel export stream           |
                                +------------------------------+
```

---

## 9. Canonical domain model

## 9.1 ManagedSession

```yaml
ManagedSession:
  session_id: string
  runtime_family: "claude_code"
  execution_owner: local_process | anthropic_cloud_vm | sdk_host
  state: creating | starting | active | waiting | compacting | rewinding | archiving | ended | failed
  primary_surface: terminal | vscode | jetbrains | desktop | web | mobile | scheduler | channel | sdk
  surface_bindings:
    - surface_id: string
      surface_kind: enum
      projection_mode: primary | remote_projection
      connection_state: connected | disconnected | reconnecting
      interactive: boolean
  cwd: string?
  repo_ref:
    local_path: string?
    remote_repo: string?
    branch: string?
  permission_mode: default | acceptEdits | plan | auto | dontAsk | bypassPermissions
  policy_envelope_id: string
  context_snapshot_id: string
  active_turn_id: string?
  latest_checkpoint_id: string?
  session_group_id: string?
  parent_session_id: string?
  fork_of_session_id: string?
  handoff_from_session_id: string?
  created_by: user | schedule | channel | sdk | team_lead
  created_at: timestamp
  updated_at: timestamp
  ended_at: timestamp?
```

## 9.2 Turn

```yaml
Turn:
  turn_id: string
  session_id: string
  input_origin: human | schedule | channel | sdk | team_message
  input_payload:
    type: text | structured
    prompt: string
    attachments: []
  state: submitted | gathering_context | pending_decision | executing | verifying | interrupted | completed | failed
  summary: string?
  started_at: timestamp
  completed_at: timestamp?
```

## 9.3 WorkItem

```yaml
WorkItem:
  item_id: string
  turn_id: string
  session_id: string
  kind:
    - context_read
    - context_injection
    - tool_call
    - hook_call
    - approval_request
    - checkpoint
    - compaction
    - rewind
    - subagent
    - team_message
    - summary
    - telemetry_flush
  status: queued | in_progress | completed | failed | declined | canceled
  payload: object
  started_at: timestamp
  ended_at: timestamp?
```

## 9.4 DecisionPoint

```yaml
DecisionPoint:
  decision_id: string
  session_id: string
  turn_id: string?
  target_kind: tool | file_change | network | managed_settings | mcp | hook | user_question
  target_ref: string?
  proposed_action: object
  origin_stage: pretool_hook | permission_rule | protected_path | sandbox | classifier | interactive_prompt
  resolution: allow | ask | deny | defer | cancel
  resolved_by: hook | policy | classifier | user | runtime
  reason: string?
  requested_at: timestamp
  resolved_at: timestamp?
```

## 9.5 PolicyEnvelope

```yaml
PolicyEnvelope:
  policy_envelope_id: string
  session_id: string
  provider_mode: anthropic_api | bedrock | vertex | foundry | custom_gateway
  managed_source_kind: none | server_managed | endpoint_managed
  policy_fetch_state: not_applicable | cache_hit | fetched | fetch_failed | fail_closed
  managed_source_version: string?
  permissions:
    mode: default | acceptEdits | plan | auto | dontAsk | bypassPermissions
    allow: []
    ask: []
    deny: []
    protected_paths: []
    auto_mode_enabled: boolean
    bypass_disabled: boolean
    auto_disabled: boolean
  sandbox:
    enabled: boolean
    filesystem_scope: object
    network_scope: object
  hooks:
    allow_managed_only: boolean
    registry_hash: string
  mcp:
    allowed_servers: []
    denied_servers: []
    allow_managed_only: boolean
  memory:
    include_auto_memory: boolean
    managed_claude_md_enabled: boolean
    excludes: []
  security_dialog_required: boolean
  version: integer
```

## 9.6 ContextSnapshot

```yaml
ContextSnapshot:
  context_snapshot_id: string
  session_id: string
  compaction_epoch: integer
  segments:
    - segment_id: string
      kind:
        - system_prompt
        - output_style
        - managed_claude_md
        - project_claude_md
        - local_claude_md
        - auto_memory
        - path_rule
        - nested_claude_md
        - skill_description
        - invoked_skill_body
        - mcp_tool_manifest
        - hook_injected_context
        - transcript_summary
        - file_read
      source_ref: string
      loaded_at: startup | on_demand | post_compaction
      reinjection_policy: always | on_demand | budgeted | never
      token_budget_hint: integer?
```

## 9.7 Checkpoint

```yaml
Checkpoint:
  checkpoint_id: string
  session_id: string
  turn_id: string
  trigger: user_prompt | pre_edit | manual
  captures:
    code_state: boolean
    conversation_state: boolean
  storage_ref: string
  restorable_modes:
    - code
    - conversation
    - both
    - summarize_from_here
  expires_at: timestamp?
```

## 9.8 ChildContext

```yaml
ChildContext:
  child_context_id: string
  parent_session_id: string
  parent_turn_id: string
  kind: subagent
  runtime_binding: same_execution_owner
  context_isolated: true
  returns: summary_only | summary_plus_metadata
  tool_profile: string
  permission_profile: string
  status: starting | active | returning | completed | failed
```

## 9.9 SessionGroup

```yaml
SessionGroup:
  session_group_id: string
  leader_session_id: string
  member_session_ids: []
  coordination_mode: shared_task_list
  status: active | draining | completed | failed
```

---

## 10. Lifecycle state machines

## 10.1 Session lifecycle

```text
creating
  -> starting
  -> active
  -> waiting        (idle, pending user input, or detached but resumable)
  -> compacting     (context reduction)
  -> rewinding      (checkpoint restore or summarize-from-here)
  -> archiving
  -> ended

Any non-terminal state
  -> failed
```

### State semantics

- **creating**: session record minted, runtime not yet bound.
- **starting**: policy resolution, runtime bootstrap, context bootstrap.
- **active**: session can accept input and/or is executing work.
- **waiting**: no active turn, may be detached from one or more surfaces.
- **compacting**: runtime is replacing transcript history with summary while preserving session identity.
- **rewinding**: runtime is restoring or summarizing from a checkpoint boundary.
- **archiving**: session is being moved out of hot storage / active list.
- **ended**: terminal state with successful shutdown.
- **failed**: terminal or recoverable failure, depending on error class.

## 10.2 Turn lifecycle

```text
submitted
  -> gathering_context
  -> pending_decision   (optional, repeatable)
  -> executing
  -> verifying
  -> completed

submitted | gathering_context | executing | verifying
  -> interrupted

any state
  -> failed
```

## 10.3 Decision lifecycle

```text
proposed
  -> mutated_by_hook   (optional)
  -> denied_by_hook    (optional terminal)
  -> resolved_by_rule  (allow / ask / deny)
  -> resolved_by_protected_path
  -> resolved_by_classifier
  -> awaiting_user
  -> executed | declined | canceled | deferred
```

## 10.4 Checkpoint lifecycle

```text
scheduled
  -> captured
  -> indexed
  -> restorable
  -> restored | summarized | expired
```

## 10.5 Surface binding lifecycle

```text
attaching
  -> connected
  -> disconnected
  -> reconnecting
  -> connected
  -> detached
```

Remote Control uses this lifecycle heavily. A disconnect should not imply session failure.

---

## 11. Policy model

## 11.1 Shared abstraction

To stay compatible with the Codex plane, policy is represented in two conceptual layers:

```yaml
RequiredPolicy:
  hard_constraints: []

BootstrapPreferences:
  startup_defaults: []
```

## 11.2 Claude mapping

### RequiredPolicy

Claude has a strong native fit for `RequiredPolicy`:

- managed settings
- managed-only settings
- managed permission rules
- managed hook policy
- managed MCP policy
- managed environment variables
- auto-mode availability controls
- bypass-permissions availability controls

### BootstrapPreferences

Claude does **not** have an exact native equivalent to Codex’s “managed defaults that users can still change during a session” model.

Design rule:

- preserve `BootstrapPreferences` in the shared plane
- implement it for Claude only as a **session bootstrap template**
- do **not** claim it is equivalent to a native admin-managed tier
- do **not** attempt to force admin defaults into the managed settings tier because Claude managed settings are non-overridable

This is the largest semantic mismatch with the Codex plane.

## 11.3 Managed settings source resolution

Claude-specific policy resolution should follow this order:

```yaml
managed_source_resolution:
  1_server_managed: first
  2_endpoint_managed: second
  source_merge: first_non_empty_source_wins
  local_override: disallowed
```

Interpretation:

- If server-managed settings deliver any non-empty configuration, endpoint-managed settings are ignored.
- If server-managed settings deliver nothing, endpoint-managed settings apply.
- Within endpoint-managed settings, file-based fragments may deep-merge according to Claude’s endpoint rules.
- No lower-priority layer can override managed settings, including CLI arguments.

## 11.4 Effective policy assembly algorithm

```text
1. Identify provider mode and execution owner.
2. Resolve managed source:
   a. attempt server-managed settings if supported
   b. otherwise fall back to endpoint-managed settings
3. Record fetch state:
   - cache_hit
   - fetched
   - fetch_failed
   - fail_closed
4. Load lower scopes for observability only:
   - CLI args
   - local project settings
   - shared project settings
   - user settings
5. Compile effective policy:
   - managed tier first
   - then lower scopes where allowed
6. Expand permission rules into ordered deny / ask / allow matchers.
7. Bind hook registry and MCP policy.
8. Derive startup context policy and compaction policy.
9. Freeze into versioned PolicyEnvelope.
```

## 11.5 Policy handshake state

Claude introduces a startup handshake not present in the same way in Codex.

```yaml
PolicyHandshake:
  states:
    - not_required
    - awaiting_fetch
    - awaiting_user_security_dialog
    - accepted
    - rejected
    - fail_closed
```

Rules:

- risky managed hooks and custom environment variables may require a security dialog
- in interactive runs, a rejection exits startup
- in non-interactive runs, settings apply without the dialog
- if `forceRemoteSettingsRefresh` is enabled and refresh fails, startup fails closed

---

## 12. Decision pipeline

The Claude plane must normalize the actual runtime decision order rather than pretending everything is a simple approval prompt.

## 12.1 Declarative decision flow

```yaml
DecisionPipeline:
  - stage: session_state_guard
  - stage: pretool_hooks
  - stage: permission_rules
  - stage: protected_path_guard
  - stage: permission_mode_baseline
  - stage: sandbox_substitution
  - stage: auto_mode_classifier
  - stage: interactive_prompt_or_headless_resolution
  - stage: runtime_execution
  - stage: posttool_hooks
  - stage: checkpoint_capture
```

## 12.2 Detailed semantics by stage

### session_state_guard

Reject actions invalid for the current state, such as:

- tool use while rewinding
- edits while the session is read-only because of mode or runtime surface
- writes when runtime is detached from required workspace context

### pretool_hooks

A `PreToolUse` hook may:

- mutate input
- return `allow`
- return `ask`
- return `deny`
- return `defer` in headless flows

Important rule: a hook can tighten restrictions but cannot override matching deny or ask rules from policy.

### permission_rules

Compile and evaluate rules in strict order:

```yaml
rule_precedence:
  - deny
  - ask
  - allow
```

The first matching rule wins.

### protected_path_guard

Protected paths are never auto-approved. The plane should normalize these as a specific `origin_stage = protected_path`.

### permission_mode_baseline

Map Claude modes into shared behavior:

| Mode | Plane behavior |
|---|---|
| `default` | reads auto; edits/commands/network usually ask |
| `acceptEdits` | working-directory edits and common FS commands auto |
| `plan` | reads only; no mutation or command execution |
| `auto` | baseline auto-approve path plus classifier checks |
| `dontAsk` | everything not pre-approved is denied |
| `bypassPermissions` | skip permission prompts and safety checks except protected-path handling |

### sandbox_substitution

For Bash, a sandbox may replace the need for per-command approval depending on effective settings. This is not the same thing as an allow rule; it is runtime-technical containment.

### auto_mode_classifier

In `auto`, unsafe or ambiguous actions are checked by a classifier path. The plane should record classifier decisions as distinct from user approvals.

### interactive_prompt_or_headless_resolution

If the decision remains unresolved and the session is interactive, prompt the user. In headless mode, either deny or defer according to policy and hook output.

### runtime_execution

Tool call enters the runtime adapter. The plane records final runtime status and any post-resolution errors separately from policy outcome.

### posttool_hooks

Post-execution hooks are emitted as work items. They may create additional artifacts, notifications, or blocks for subsequent flow.

### checkpoint_capture

If the action is a tracked file edit, a checkpoint edge is recorded.

---

## 13. Context and memory model

Claude Code requires the plane to explicitly understand context composition.

## 13.1 Startup context sources

At session bootstrap, the plane should treat the following as startup context sources:

| Source | Kind | Load timing |
|---|---|---|
| system prompt | `system_prompt` | startup |
| output style | `output_style` | startup |
| managed `CLAUDE.md` | `managed_claude_md` | startup |
| project-root `CLAUDE.md` / `.claude/CLAUDE.md` | `project_claude_md` | startup |
| `CLAUDE.local.md` | `local_claude_md` | startup |
| auto memory | `auto_memory` | startup |
| MCP tool names / manifests | `mcp_tool_manifest` | startup |
| skill descriptions | `skill_description` | startup |
| startup hook injected text | `hook_injected_context` | startup |

## 13.2 On-demand context sources

These are not always present at startup:

| Source | Kind | Load timing |
|---|---|---|
| file reads | `file_read` | on demand |
| nested `CLAUDE.md` in subdirectories | `nested_claude_md` | on demand |
| path-scoped rules | `path_rule` | on demand |
| invoked skill bodies | `invoked_skill_body` | on invocation |
| subagent return summaries | `summary` | on child completion |

## 13.3 Compaction-aware context model

The plane should attach a `reinjection_policy` to each context segment.

Recommended policies:

```yaml
reinjection_policy:
  system_prompt: always
  output_style: always
  managed_claude_md: always
  project_claude_md: always
  local_claude_md: always
  auto_memory: always
  path_rule: on_demand
  nested_claude_md: on_demand
  skill_description: startup_refresh
  invoked_skill_body: budgeted
  file_read: never
  transcript_summary: always
  hook_injected_context: configurable
```

## 13.4 Compaction behavior

When compaction occurs, the plane should produce a new `ContextSnapshot` epoch rather than mutating the old one in place.

Expected effect:

1. conversation history is summarized
2. startup-critical context is reloaded
3. on-demand context is reintroduced only if its trigger recurs
4. invoked skills are reattached subject to budget
5. an explicit `compaction` WorkItem is emitted

## 13.5 Managed memory guidance vs enforcement

A key distinction must be preserved:

- `CLAUDE.md`, auto memory, and rules are **guidance**
- managed settings and permission rules are **enforcement**

The plane must never confuse the two or treat a memory artifact as a hard policy source.

---

## 14. Checkpointing and rewind

Checkpointing is a first-class Claude extension to the shared plane.

## 14.1 Why it belongs in the plane

Checkpointing changes session truth, not just UI:

- every user prompt creates a rewind point
- file edits create restorable code states
- checkpoints persist across resumed sessions
- rewind can mutate both conversation state and code state
- summarize-from-here changes the context history without changing disk state

These are session-plane operations.

## 14.2 Checkpoint capture rules

```yaml
CheckpointCapture:
  on_user_prompt: true
  on_file_edit_tools: true
  on_bash_side_effects: false
  on_external_manual_edits: best_effort_only
```

## 14.3 Supported rewind operations

The plane should expose four normalized rewind actions:

```yaml
RewindOperation:
  - restore_code_and_conversation
  - restore_conversation_only
  - restore_code_only
  - summarize_from_here
```

## 14.4 Lineage implications

A rewind should not destroy provenance. The plane should:

- preserve the pre-rewind event log
- mark a new checkpoint cursor as active
- add `rewound_from_checkpoint_id`
- keep old checkpoints addressable until expiry or GC

This mirrors source-control safety expectations while remaining session-native.

---

## 15. Child work model: subagents and teams

## 15.1 Subagents

Subagents should be modeled as child contexts with the following semantics:

```yaml
SubagentModel:
  parent_scope: single_session
  context_window: isolated
  return_shape: summary_only
  communication: caller_only
  lifecycle_owner: parent_turn
  token_accounting: child_usage rolls up to parent session
```

Plane rules:

- child context gets a `child_context_id`, not a top-level peer session id by default
- permissions may be limited per subagent profile
- child context output should be summarized back into the parent turn
- child work may run in foreground or background but remains parent-owned

## 15.2 Agent teams

Agent teams should be modeled as grouped sibling sessions:

```yaml
AgentTeamModel:
  parent_scope: session_group
  context_window: one per teammate
  return_shape: peer_to_peer plus leader synthesis
  communication: direct peer messaging
  lifecycle_owner: session_group
  token_accounting: per-session plus rolled-up group usage
```

Plane rules:

- each teammate is a `ManagedSession`
- the lead is another `ManagedSession`
- all members share a `session_group_id`
- teardown is group-aware
- team lineage is distinct from subagent lineage

## 15.3 Why the split matters

If the plane collapses subagents and teammates into one abstraction, it will lose:

- direct peer communication semantics
- shared-task-list coordination
- separate resume / archive behavior
- proper usage accounting
- correct surface attachment rules

---

## 16. Multi-surface behavior

## 16.1 SurfaceBinding

A `SurfaceBinding` is the durable representation of a client attachment.

```yaml
SurfaceBinding:
  surface_id: string
  session_id: string
  surface_kind: enum
  projection_mode: primary | remote_projection
  capabilities:
    approvals: boolean
    diff_review: boolean
    notifications: boolean
    qr_connect: boolean
    keyboard_control: boolean
  last_seen_at: timestamp
```

## 16.2 Rules for surface attachment

1. A session may have one primary surface and multiple projections.
2. Remote Control adds projections but does not change `execution_owner`.
3. Surface disconnects must not kill a session unless the runtime itself exits.
4. Cloud handoff creates a new session rather than mutating `execution_owner`.
5. A session may be resumed on a different surface without changing its canonical identity if the execution owner remains the same.

## 16.3 Handoff semantics

Recommended lineage fields:

```yaml
HandoffLineage:
  handoff_from_session_id: string
  handoff_type: local_to_cloud | desktop_to_ide | desktop_to_web_cloud
  seed_artifacts:
    - summary_ref
    - branch_ref
    - diff_ref
```

Remote Control should not use `handoff_type`; it is live projection, not transfer.

---

## 17. API surface of the plane

The plane should keep the same high-level verbs as the Codex plane, with Claude-specific extensions.

## 17.1 Session APIs

```yaml
CreateSession(request)
ResumeSession(session_id)
ForkSession(session_id)
ArchiveSession(session_id)
EndSession(session_id)
AttachSurface(session_id, surface)
DetachSurface(session_id, surface_id)
CreateSessionGroup(request)
```

## 17.2 Turn APIs

```yaml
SubmitTurn(session_id, input)
InterruptTurn(session_id, turn_id)
CompactSession(session_id)
RenameSession(session_id, name)
```

## 17.3 Policy APIs

```yaml
ResolveManagedPolicy(request)
AcknowledgeManagedPolicyDialog(session_id, accept | reject)
SwitchPermissionMode(session_id, mode)
GetEffectivePolicy(session_id)
```

## 17.4 Decision APIs

```yaml
ResolveDecision(decision_id, resolution)
ListPendingDecisions(session_id)
```

## 17.5 Checkpoint APIs

```yaml
ListCheckpoints(session_id)
RestoreCheckpoint(session_id, checkpoint_id, mode)
SummarizeFromCheckpoint(session_id, checkpoint_id, instructions?)
```

## 17.6 Child-work APIs

```yaml
SpawnSubagent(session_id, turn_id, profile, prompt)
CreateTeamTeammate(session_group_id, config)
SendTeamMessage(session_id, peer_session_id, payload)
```

## 17.7 Event subscription

```yaml
SubscribeSessionEvents(session_id)
SubscribeGroupEvents(session_group_id)
SubscribeOrgPolicyEvents(scope)
```

---

## 18. Event model

To stay close to the Codex plane, Claude should emit an append-only event stream with normalized names.

## 18.1 Session events

```yaml
session.created
session.started
session.active
session.waiting
session.compacting
session.rewinding
session.archived
session.ended
session.failed
```

## 18.2 Surface events

```yaml
surface.attached
surface.connected
surface.disconnected
surface.reconnecting
surface.detached
```

## 18.3 Policy events

```yaml
policy.fetch.started
policy.fetch.succeeded
policy.fetch.failed
policy.dialog.required
policy.dialog.accepted
policy.dialog.rejected
policy.compiled
policy.version.changed
```

## 18.4 Turn events

```yaml
turn.submitted
turn.gathering_context
turn.pending_decision
turn.executing
turn.verifying
turn.interrupted
turn.completed
turn.failed
```

## 18.5 Work events

```yaml
work.context.loaded
work.tool.requested
work.tool.executed
work.tool.failed
work.hook.started
work.hook.completed
work.hook.blocked
work.checkpoint.created
work.compaction.started
work.compaction.completed
work.rewind.started
work.rewind.completed
```

## 18.6 Decision events

```yaml
decision.proposed
decision.mutated
decision.allowed
decision.asked
decision.denied
decision.deferred
decision.canceled
decision.resolved
```

## 18.7 Child-work events

```yaml
child.subagent.started
child.subagent.completed
team.group.created
team.member.started
team.message.sent
team.member.completed
team.group.completed
```

---

## 19. Storage model

## 19.1 Storage principle

The plane is control-plane authoritative but payload-light by default.

Recommended split:

- **central plane store**: ids, state, event envelopes, policy versions, usage counters, artifact pointers
- **runtime-local store**: transcripts, full file reads, checkpoint payloads, local caches
- **optional export sinks**: audit logs, OTel backends, compliance archives

This preserves local-code residency expectations for local Claude sessions.

## 19.2 Core stores

### SessionRegistry

Stores canonical session rows and lineage.

### EventLog

Append-only normalized events keyed by `session_id` and optionally `session_group_id`.

### PolicyStore

Stores compiled `PolicyEnvelope` versions and fetch metadata.

### ContextIndex

Stores `ContextSnapshot` metadata and segment pointers.

### CheckpointIndex

Stores checkpoint metadata and restore references.

### ArtifactIndex

Stores references to diffs, summaries, reports, audit records, and exported telemetry.

### UsageStore

Stores usage rollups by session, group, user, workspace, runtime kind, and provider.

## 19.3 Suggested retention classes

```yaml
RetentionClasses:
  hot_session_metadata: 30d
  hot_event_log: 30d
  usage_rollups: 90d
  audit_event_metadata: org_policy
  checkpoint_payloads: runtime_local_default
```

Retention should be policy-driven rather than hard-coded, because cloud and local expectations differ.

---

## 20. Observability

## 20.1 Shared observability contract

The plane should normalize runtime observations into:

- metrics
- logs / events
- optional traces

## 20.2 Claude-specific adapter behavior

Claude exposes telemetry through OpenTelemetry. The plane should map Claude telemetry into the shared schema instead of inventing a parallel export pipeline.

## 20.3 Recommended normalized metrics

| Metric | Dimension examples |
|---|---|
| `managed_sessions_active` | runtime kind, execution owner, surface kind |
| `managed_turn_duration_ms` | model, provider, session kind |
| `managed_decisions_total` | origin stage, resolution |
| `managed_hooks_total` | hook type, outcome |
| `managed_checkpoints_total` | trigger, restore mode |
| `managed_compactions_total` | session kind |
| `managed_subagent_total` | profile, outcome |
| `managed_team_sessions_total` | group size, outcome |
| `managed_policy_fetch_failures_total` | source kind, provider |
| `managed_surface_reconnects_total` | surface kind |
| `managed_usage_tokens` | input/output, model, provider |

## 20.4 Trace boundaries

Recommended spans:

```yaml
TraceSpans:
  session.bootstrap
  policy.resolve
  turn.process
  decision.resolve
  hook.execute
  tool.execute
  checkpoint.capture
  session.compact
  checkpoint.restore
  subagent.run
  team.session.run
```

---

## 21. Security and governance

## 21.1 Control layers

The plane must model Claude’s layered control stack explicitly:

1. managed settings source resolution
2. permission rules
3. permission mode
4. protected paths
5. sandboxing
6. hooks
7. classifier-based auto mode
8. interactive user dialogs
9. runtime-owned cloud or local isolation

## 21.2 Managed settings risk model

Claude server-managed settings are powerful but not equivalent to OS-level immutable enforcement on unmanaged devices. The plane should carry a field such as:

```yaml
PolicyTrustLevel:
  - endpoint_enforced
  - server_managed_best_effort
  - unmanaged
```

This enables downstream governance to distinguish:

- MDM / registry / file-based endpoint policy
- server-delivered policy on unmanaged endpoints
- fully unmanaged user settings

## 21.3 Provider caveats

Provider mode materially changes governance. Some controls are not available in every provider configuration. The plane must record `provider_mode` on every session and policy envelope so downstream policy reporting does not make false assumptions.

## 21.4 Protected-path policy

Protected paths should be normalized into a dedicated policy section rather than left implicit in runtime behavior. This avoids false audit conclusions when a user says “bypass permissions was enabled” but writes still triggered prompts or denials.

## 21.5 Hook governance

Hooks need first-class provenance because they can:

- deny tool execution
- mutate tool arguments
- inject context
- run shell commands
- audit config changes
- alter runtime behavior without model involvement

Every hook execution should therefore emit:

```yaml
HookAudit:
  hook_name: string
  source_scope: managed | user | project | plugin | sdk
  event_type: string
  matcher: string
  outcome: allow | deny | ask | mutate | error | noop
```

## 21.6 Cloud vs local security

The plane must not collapse these modes:

- local execution: code and tools stay on the user’s machine
- Remote Control: still local execution, remote UI only
- cloud execution: code cloned into Anthropic-managed VM with cloud-specific controls

This distinction affects approval semantics, telemetry, incident response, and compliance reporting.

---

## 22. Compatibility strategy with the Codex plane

## 22.1 What remains identical

The following shared interfaces should remain unchanged:

```yaml
ISessionPlane
ISessionRecord
ITurnRecord
IDecisionPoint
IPolicyEnvelope
IRuntimeAdapter
IArtifactStore
IUsageSink
```

## 22.2 What becomes Claude-specific extensions

```yaml
ClaudeExtensions:
  execution_owner
  surface_bindings
  policy_fetch_state
  managed_source_kind
  checkpoint_cursor
  context_snapshot
  child_contexts
  session_group_id
  handoff_from_session_id
  provider_mode
```

## 22.3 Shared event compatibility

Where the Codex plane uses thread / turn / item language, the Claude plane should preserve semantic compatibility by mapping:

| Shared plane | Codex public concept | Claude concept |
|---|---|---|
| `ManagedSession` | thread | session |
| `Turn` | turn | turn |
| `WorkItem` | item | tool/hook/checkpoint/subagent/etc. |
| `DecisionPoint` | approval request | permission / hook / classifier / dialog |
| `SessionGroup` | parallel threads or multi-agent workflows | agent team |

## 22.4 Shared-plane aliases

To reduce churn in upstream systems, the implementation may expose aliases:

```yaml
aliases:
  thread_id -> session_id
  child_thread -> child_context | teammate_session
```

These aliases should exist only at adapter and serialization boundaries, not in the core Claude domain model.

## 22.5 Known semantic mismatch

The Codex abstraction of “managed defaults that can be changed during a session and re-applied next launch” does not map directly to native Claude managed settings. This is the one major place where the shared abstraction should remain, but the Claude implementation must declare limited support.

---

## 23. Rollout plan

## 23.1 Phase 1: metadata-only session registry

Deliver:

- session registry
- runtime kind and surface binding
- turn lifecycle
- basic event log
- resume/fork/archive lineage

Exclude:

- checkpoints
- teams
- full policy handshake

## 23.2 Phase 2: policy and decisions

Deliver:

- managed settings resolution
- permission mode normalization
- rule compilation
- decision-point eventing
- policy fetch state
- security-dialog state

## 23.3 Phase 3: context and checkpoints

Deliver:

- typed `ContextSnapshot`
- compaction epochs
- checkpoint index
- rewind APIs
- artifact pointers for summaries

## 23.4 Phase 4: subagents and teams

Deliver:

- `ChildContext`
- `SessionGroup`
- child usage rollup
- peer message eventing
- background child execution states

## 23.5 Phase 5: remote projection and cloud handoff

Deliver:

- multi-surface bindings
- Remote Control projection semantics
- local-to-cloud handoff lineage
- reconnect handling
- disconnection-resilient waiting state

## 23.6 Phase 6: enterprise telemetry and audits

Deliver:

- OTel normalization
- hook audit stream
- policy trust level
- compliance export views
- provider-mode-aware dashboards

---

## 24. Open questions

1. **Should checkpoint payloads ever leave the runtime host?**
   The default should likely remain “no”, with only metadata in the plane.

2. **How much of server-managed settings fetch state should be user-visible?**
   Security teams want precision; end users usually want minimal noise.

3. **Should a long-running background subagent ever promote to a sibling session?**
   This could simplify resumability but would blur the session model.

4. **Do we need a first-class `ScheduleTemplate` object now, or can schedules remain session progenitors outside the plane?**
   Start outside the plane unless session-group and audit requirements force promotion.

5. **How should cloud handoff summaries be versioned and audited?**
   They are derived artifacts that influence a new session’s behavior and therefore deserve lineage.

6. **Can we safely emulate Codex-style managed defaults for Claude without misleading administrators?**
   Probably only as a clearly labeled bootstrap template, never as an enforced admin tier.

---

## 25. Recommended implementation stance

Build `ClaudeCodeManagedSessionPlane` as a **thin specialization** of the shared Managed Session Plane, not as a fork.

That means:

- keep the shared top-level schema
- isolate runtime differences inside `ClaudeRuntimeAdapter`
- make `DecisionPoint`, `ContextSnapshot`, and `Checkpoint` first-class
- model Remote Control as a surface projection
- model cloud web runs as distinct execution owners
- treat subagents and teams as separate primitives
- preserve cross-runtime compatibility, but do not hide the places where Claude’s semantics genuinely differ from Codex

This produces a plane that is structurally reusable, operationally accurate, and honest about Claude Code’s runtime model.
