# Omnigent Bridge

Status: Proposed design  
Owners: MoonMind Platform  
Last updated: 2026-08-07

**Implementation tracking:** rollout notes, spikes, and temporary handoffs belong under `docs/tmp/` or gitignored local-only artifacts, not as mutable checklists in this canonical design document.

## Related docs

- [`docs/UI/WorkflowChatPanel.md`](../UI/WorkflowChatPanel.md)
- [`docs/Omnigent/AgentProfiles.md`](./AgentProfiles.md)
- [`docs/Omnigent/CodexCreateToHostContract.md`](./CodexCreateToHostContract.md)
- [`docs/Omnigent/OmnigentAdapter.md`](./OmnigentAdapter.md)
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](../ManagedAgents/CodexCliManagedSessions.md)
- [`docs/Observability/LiveLogs.md`](../Observability/LiveLogs.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](../Temporal/WorkflowArtifactSystemDesign.md)
- [`docs/Temporal/VisibilityAndUiQueryModel.md`](../Temporal/VisibilityAndUiQueryModel.md)
- [`docs/ExternalAgents/AddingExternalProvider.md`](../ExternalAgents/AddingExternalProvider.md)

---

## 1. Purpose and authority

This document defines the **MoonMind Omnigent Bridge**: a MoonMind API capability that exposes Omnigent-shaped session, event, stream, resource, and control communication while preserving MoonMind as the durable orchestration, authorization, policy, and artifact authority.

The bridge supports an unchanged Omnigent host whenever deployment topology and upstream compatibility allow it.

```text
MoonMind Workflow / UI
  -> MoonMind Omnigent Bridge
      -> authorized Omnigent-shaped session/event/resource/control surface
          -> stock Omnigent Server or embedded-compatible surface
              -> unchanged Omnigent host / runner
                  -> Codex, Claude, or another supported harness
```

MoonMind remains responsible for:

- Temporal workflow orchestration,
- Workflow, Step Execution, and AgentRun identity,
- durable Workflow-to-provider session binding,
- authorization of every browser and service request,
- effective capability and approval policy,
- outbound security scans,
- artifact refs and artifact authorization,
- diagnostics and operator audit evidence,
- Step Execution evidence.

The native Omnigent web application remains responsible for the Workflow Detail Chat interaction and presentation model: transcript, composer, queue, tool and reasoning views, approvals, files, terminals, agents, tasks, and session-lifecycle affordances.

The host/runtime remains responsible for:

- live runtime execution,
- harness launch and lifecycle inside its environment,
- transcript deltas and runtime events,
- host-side resource discovery,
- changed-file and session-resource reporting.

The native UI is a provider-maintained presentation client behind the MoonMind bridge. It is not an independent control plane, source of workflow authority, or credential boundary.

---

## 2. Design principles

### 2.1 Use Omnigent names at the bridge boundary

The external bridge boundary uses Omnigent-style nouns and operations:

- `session`,
- `event`,
- `stream`,
- `host`,
- `runner`,
- `resource`,
- `snapshot`,
- `interrupt`,
- `stop_session`.

Do not introduce a parallel MoonMind protocol or new product vocabulary unless a concern is genuinely MoonMind-only.

### 2.2 Keep MoonMind artifact authority

The bridge may observe live Omnigent resources, but MoonMind artifacts remain the durable evidence boundary.

Provider-native ids, URLs, host paths, and file ids may appear in redacted diagnostics or server-side mapping metadata. They must not replace MoonMind artifact refs in workflow evidence, Step evidence, or terminal `AgentRunResult.outputRefs`.

### 2.3 Keep the host unchanged

A successful design supports a stock Omnigent host. No custom host image or source patch is required.

Deployment configuration may specify server URL, host authentication, endpoint refs, network routing, and standard Omnigent host settings. A custom MoonMind-specific host build is out of scope.

### 2.4 Prefer proxy-first compatibility

The bridge supports:

```yaml
hostProtocolMode:
  - upstream_omnigent_server_proxy
  - embedded_omnigent_compatible_server
```

`upstream_omnigent_server_proxy` is the preferred default because stock Omnigent Server already owns the host/runner tunnel.

`embedded_omnigent_compatible_server` implements enough compatible server behavior for an unchanged host to connect directly to MoonMind. Its exact auth and protocol contract is owned by `EmbeddedHostAuthCompatibility.md` and remains gated by conformance evidence.

### 2.5 Preserve native UI, centralize authority

MoonMind should embed or proxy the native Omnigent web application instead of reproducing its interaction components.

All native application requests still cross a binding-scoped MoonMind boundary. Client-side hiding or disabling is an affordance only. The bridge independently authenticates, authorizes, capability-checks, scans, audits, rewrites, and forwards every request.

---

## 3. Topologies

### 3.1 Proxy mode

```text
MoonMind UI / API
  -> MoonMind Omnigent Bridge
      -> stock Omnigent Server
          -> unchanged Omnigent Host / Runner
              -> Codex / Claude / other harness
```

Responsibilities:

- MoonMind exposes the browser-safe native UI and bridge facade.
- The bridge persists MoonMind session bindings, idempotency state, event refs, and artifact refs.
- The bridge calls stock Omnigent session/event/resource/control APIs.
- Stock Omnigent Server owns the host/runner tunnel.
- The unchanged host continues to speak its native protocol.

### 3.2 Embedded compatibility mode

The version, route, authentication, lifecycle, evidence, failure, upgrade, and rollback contract is authoritative in [`EmbeddedHostAuthCompatibility.md`](EmbeddedHostAuthCompatibility.md).

```text
MoonMind UI / API
  -> MoonMind Omnigent Bridge
      -> embedded Omnigent-compatible server surface
          -> unchanged Omnigent Host / Runner
              -> Codex / Claude / other harness
```

Embedded mode must preserve the same browser binding, authorization, capability, scan, audit, and artifact boundaries as proxy mode.

### 3.3 Direct Codex compatibility during migration

```text
MoonMind UI / API
  -> MoonMind Omnigent Bridge event model
      -> direct Codex managed-session adapter
          -> Codex managed runtime
```

This is a temporary migration and historical-read path. It emits bridge-shaped diagnostic evidence but does not become a second primary Workflow Chat renderer. Retirement remains governed by [`CodexSupportAndCutover.md`](./CodexSupportAndCutover.md).

---

## 4. Protocol surfaces

### 4.1 Provider/service session API facade

MoonMind services and compatibility clients may use these Omnigent-shaped routes behind the bridge:

| Purpose | Route |
|---|---|
| List available agents | `GET /api/agents` |
| Create session | `POST /v1/sessions` |
| Get session snapshot | `GET /v1/sessions/{session_id}` |
| Post session event | `POST /v1/sessions/{session_id}/events` |
| Stream session events | `GET /v1/sessions/{session_id}/stream` |
| Resolve elicitation | `POST /v1/sessions/{session_id}/elicitations/{elicitation_id}/resolve` |
| List changed files | `GET /v1/sessions/{session_id}/resources/environments/default/changes` |
| List workspace files | `GET /v1/sessions/{session_id}/resources/environments/default/filesystem` |
| Get workspace file content | `GET /v1/sessions/{session_id}/resources/environments/default/filesystem/{path}` |
| Get workspace file diff | `GET /v1/sessions/{session_id}/resources/environments/default/diff/{path}` |
| List session files | `GET /v1/sessions/{session_id}/resources/files` |
| Get session file content | `GET /v1/sessions/{session_id}/resources/files/{file_id}/content` |

#### `omnigent.server.v1` compatibility matrix

All session-scoped operations resolve the durable MoonMind binding and authorize the requested operation before touching upstream state.

| Operation | Facade route | Stock operation | Policy |
|---|---|---|---|
| Agent discovery | `GET /api/agents` | `GET /v1/agents` | Authenticated bounded catalog; no credential or host authority. |
| Host readiness | `GET /api/hosts` | Same | Bounded readiness only; managed routing never accepts caller-selected host identity. |
| Create/reuse | `POST /v1/sessions` | Same | Workflow-owned idempotency, profile/policy resolution, and first-message reconciliation. |
| Snapshot | `GET /v1/sessions/{id}` | Same | Bound and authorized provider sessions only. |
| Attach/reconcile | `POST /v1/sessions/{id}/attach` | Snapshot probe plus durable attach | Existing owned binding required; conflicting attachment fails closed. |
| Message/interrupt/stop | `POST /v1/sessions/{id}/events` | Same | Effective capability, expected-state, audit, and outbound-scan enforcement. |
| Delete | `DELETE /v1/sessions/{id}` | Same | Terminal cleanup capability and lease ownership required. |
| Provider stream | `GET /v1/sessions/{id}/stream` | Same | Per-connect authorization; reconnect reauthorizes. |
| Resolve elicitation | `POST /v1/sessions/{id}/elicitations/{eid}/resolve` | Same | Caller approval authority, expected request state, idempotency, and durable audit required. |
| File indexes | `GET .../changes`, `GET .../filesystem` | Same | Bound, authorized, bounded, and path-safe. |
| File content/diff | `GET .../filesystem/{path}`, `GET .../diff/{path}` | Same | One decode/encode boundary, traversal rejection, response limit, read capability. |
| Session files | `GET .../resources/files*` | Same | Bound, authorized, bounded, capability-gated. |

These are transport results. Raw provider paths and identifiers never become authoritative workflow evidence.

### 4.2 Workflow-bound native Chat facade

The browser does not call the public provider facade with a caller-selected provider session id. It receives an opaque MoonMind `chatBindingId` and uses:

```text
GET /omnigent-ui/workflow-chat/{chatBindingId}
*   /api/workflow-chat-bindings/{chatBindingId}/omnigent/{path}
```

The scoped facade virtualizes provider session identity for the native application and maps requests to the one durable server-side binding.

For every HTML/bootstrap, HTTP, SSE, WebSocket, resource, message, approval, terminal, reconnect, and control request, including every reconnect, the facade must:

1. authenticate the MoonMind caller,
2. load the durable `chatBindingId`,
3. authorize the caller against the Workflow Execution and requested operation,
4. verify that any route, query, or payload session reference maps to the bound provider session,
5. reject caller-supplied endpoint, alternate session, host, runner, workspace, profile, or credential identity,
6. recompute effective capabilities from upstream support, immutable Agent Profile snapshot, Provider Profile and launch policy, Workflow/Step/session state, and caller permission,
7. validate expected workflow, run, Step Execution, bridge session, provider session, session epoch, active turn, or elicitation state where relevant,
8. run required outbound security scans before provider sends,
9. record mutation audit evidence,
10. strip MoonMind credentials and forward only to the server-resolved upstream target.

The full-page **Open in Omnigent** experience uses this same scoped facade. It must not navigate directly to an upstream server and bypass MoonMind authority.

### 4.3 Host/runner channel

The host/runner channel is the persistent bidirectional control and event channel used by a host to register, advertise capabilities, heartbeat, and deliver runtime events.

In proxy mode:

```text
unchanged host -> stock Omnigent Server host/runner channel
MoonMind Bridge -> stock Omnigent Server public session API
```

In embedded mode:

```text
unchanged host -> MoonMind embedded-compatible host/runner channel
MoonMind Bridge -> local session/event/resource state
```

The bridge treats the host channel as a versioned compatibility profile, not an ad hoc MoonMind protocol.

---

## 5. Bridge component model

```text
MoonMind Omnigent Bridge
  ├─ Session API Facade
  ├─ Workflow Chat Binding / Policy Facade
  ├─ Host Protocol Facade / Proxy
  ├─ Bridge Session Store
  ├─ Event Normalizer
  ├─ Resource Harvester
  ├─ Artifact Publisher
  ├─ Diagnostic Chat Projection
  └─ Direct Codex Compatibility Producer (temporary)
```

### 5.1 Session API Facade

Owns Omnigent-shaped service routes and provider compatibility.

### 5.2 Workflow Chat Binding / Policy Facade

Resolves opaque browser-safe bindings, serves/proxies the native application, virtualizes provider session identity, projects filtered capabilities, and enforces per-request MoonMind authority.

### 5.3 Host Protocol Facade / Proxy

Forwards to stock Omnigent Server in proxy mode or implements the compatible host-facing surface in embedded mode.

### 5.4 Bridge Session Store

Persists MoonMind-to-provider session bindings, browser binding identity, immutable profile and launch refs, first-message idempotency, event refs, terminal refs, snapshots, and diagnostics refs.

### 5.5 Event Normalizer

Converts provider events into MoonMind-safe normalized records while preserving redacted raw events in artifact-backed JSONL.

### 5.6 Resource Harvester

Copies changed files, workspace files, diffs, session files, child-session snapshots, and diagnostics into MoonMind artifacts.

### 5.7 Artifact Publisher

Publishes bridge evidence through the MoonMind artifact system and returns only MoonMind artifact refs to workflow-visible results.

### 5.8 Diagnostic Chat Projection

Feeds the read-only compatibility/debug transcript from normalized bridge events before falling back to legacy managed-run logs. It is not the primary Workflow Detail Chat renderer when a native binding is available.

---

## 6. Declarative bridge configuration

```yaml
schemaVersion: moonmind.omnigent_bridge.v1

enabled: true

authority:
  temporal: moonmind
  artifacts: moonmind
  workflowChatRequests: moonmind_bridge
  liveExecution: omnigent_host

compatibility:
  profile: omnigent.server.v1
  hostUnchanged: true
  hostProtocolMode: upstream_omnigent_server_proxy

publicApi:
  mountPath: /api/omnigent
  exposeOmnigentCompatibleRoutes: true
  routes:
    agents: /api/agents
    hosts: /api/hosts
    createSession: /v1/sessions
    getSession: /v1/sessions/{session_id}
    attachSession: /v1/sessions/{session_id}/attach
    deleteSession: /v1/sessions/{session_id}
    postEvent: /v1/sessions/{session_id}/events
    streamEvents: /v1/sessions/{session_id}/stream
    changedFiles: /v1/sessions/{session_id}/resources/environments/default/changes
    workspaceFiles: /v1/sessions/{session_id}/resources/environments/default/filesystem
    workspaceFile: /v1/sessions/{session_id}/resources/environments/default/filesystem/{path:path}
    workspaceDiffs: /v1/sessions/{session_id}/resources/environments/default/diff/{path:path}
    sessionFiles: /v1/sessions/{session_id}/resources/files
    sessionFile: /v1/sessions/{session_id}/resources/files/{file_id}/content

workflowChat:
  presentation: native_omnigent
  uiMountPath: /omnigent-ui/workflow-chat/{chat_binding_id}
  scopedApiMountPath: /api/workflow-chat-bindings/{chat_binding_id}/omnigent
  exposeProviderSessionId: false
  authorizeEveryRequest: true
  stripMoonMindCredentialsUpstream: true
  injectUpstreamCredentialsServerSide: true
  effectiveCapabilities: immutable_policy_intersection
  highSecurityOutboundScan: inherit
  diagnosticProjectionEnabled: true

hostConnection:
  mode: upstream_omnigent_server_proxy
  upstreamServerUrlRef: default
  embedded:
    bindAddress: 0.0.0.0
    port: 8000
    authMode: upstream_runner_tunnel
    protocolProfile: omnigent.runner_tunnel.983c93c6

sessionDefaults:
  hostType: managed
  deleteProviderSessionAfterHarvest: false
  capture:
    stream: true
    snapshots: true
    changedFiles: true
    workspaceFiles: true
    workspaceDiffs: capability_probe
    sessionFiles: true
    childSessions: true

idempotency:
  firstMessageStateMachine:
    - not_prepared
    - prepared
    - posting
    - posted
    - terminal
  includeIdempotencyMarker: true
  reconcilePostingState: true

observability:
  writeRawEventJournal: true
  writeNormalizedEventJournal: true
  feedWorkflowChat: true # diagnostic/compatibility projection, not primary native UI
  feedAgentRunObservability: true
  fallbackToLegacyManagedRunLogs: true
```

The Session API Facade resolves this document from `OMNIGENT_BRIDGE_CONFIG_PATH` before registering routes. An unreadable path or invalid document fails fast. Environment configuration cannot disable request authorization, credential separation, or required high-security scanning for an otherwise enabled browser-facing native Chat surface.

---

## 7. Durable data model

### 7.1 `omnigent_bridge_sessions`

```text
omnigent_bridge_sessions
  bridge_session_id text primary key
  chat_binding_id text unique null
  provider text not null
  compatibility_profile text not null
  moonmind_workflow_id text not null
  moonmind_run_id text null
  moonmind_agent_run_id text not null
  step_execution_id text null
  idempotency_key text not null unique

  agent_profile_snapshot_ref text null
  provider_profile_ref text null
  effective_launch_snapshot_ref text null
  policy_snapshot_ref text null

  omnigent_endpoint_ref text not null
  omnigent_session_id text null
  omnigent_host_id text null
  omnigent_runner_id text null
  omnigent_agent_id text null
  omnigent_agent_name text null

  host_type text not null
  workspace text null
  status text not null

  first_message_state text not null
  first_message_digest text null
  first_message_marker text null
  first_message_post_attempted_at timestamptz null
  first_message_posted_at timestamptz null
  first_message_pending_id text null
  first_message_item_id text null

  raw_events_ref text null
  normalized_events_ref text null
  initial_snapshot_ref text null
  final_snapshot_ref text null
  capture_manifest_ref text null
  diagnostics_ref text null
  external_state_ref text null

  terminal_refs jsonb not null default '{}'
  metadata jsonb not null default '{}'
  created_at timestamptz not null
  updated_at timestamptz not null
```

`chat_binding_id` is an opaque authorization lookup key, not a bearer capability. The provider session id and upstream endpoint remain server-side. Caller permissions and effective capabilities are recomputed per request rather than trusted from mutable browser state.

`omnigent_bridge_sessions` is the single canonical Omnigent session and idempotency store. It supersedes the removed `omnigent_external_runs` mapping without a parallel table or compatibility wrapper.

The coarse session `status` preserves `declared`, `creating`, `active`, `completed`, `failed`, `canceled`, and `timed_out`. Full non-lossy provider state remains per event.

### 7.2 `omnigent_bridge_session_events`

```text
omnigent_bridge_session_events
  event_id text primary key
  bridge_session_id text not null
  sequence bigint not null
  timestamp timestamptz not null
  direction text not null
  event_type text not null
  normalized_status text null
  text_preview text null
  artifact_ref text null
  metadata jsonb not null default '{}'
```

DB rows are a bounded index. Full redacted raw and normalized event bodies live in MoonMind artifacts:

```text
runtime.omnigent.sse.raw.jsonl
runtime.omnigent.sse.normalized.jsonl
```

### 7.3 Mutation audit evidence

Every message/control/approval/terminal/workspace mutation records or references:

```text
actor
operation
idempotency_key
workflow_id / run_id / step_execution_id
bridge_session_id / provider_session_id
session_epoch / active_turn_id / elicitation_id as applicable
agent_profile_snapshot_ref / policy_snapshot_ref
normalized_outcome
upstream_correlation
created_at
audit_artifact_ref
```

A normalized provider event without this evidence may be displayed diagnostically but cannot become authoritative approval, control, or side-effect evidence.

---

## 8. Session creation contract

### 8.1 Request

```json
{
  "agent_id": "ag_abc123",
  "title": "Implement auth fix",
  "labels": {
    "moonmind.workflow_id": "mm:...",
    "moonmind.agent_run_id": "ar_...",
    "moonmind.step_execution_id": "step:...",
    "moonmind.idempotency_key": "..."
  },
  "host_type": "managed",
  "workspace": "https://github.com/org/repo#main",
  "model_override": null,
  "reasoning_effort": "high",
  "terminal_launch_args": []
}
```

### 8.2 Bridge behavior

1. Validate the MoonMind principal and workflow authority.
2. Resolve immutable Agent Profile, Provider Profile, policy, and launch snapshots.
3. Create or reuse the bridge row by `idempotency_key`.
4. Resolve endpoint and target agent server-side.
5. Forward to stock Omnigent Server or the embedded-compatible backend.
6. Persist provider session identity before preparing or posting the first message.
7. Allocate the opaque `chat_binding_id` only after the durable binding exists.
8. Emit `session.created` into the bridge journal.
9. Return an Omnigent-shaped service response or browser-safe binding projection as appropriate.

### 8.3 Managed/external host validation

For `host_type = managed`:

- caller-authored `host_id` is forbidden,
- repository workspace intent may be a repository URL with optional branch,
- local absolute paths are invalid.

For `host_type = external`:

- an authorized existing host binding is required,
- workspace must satisfy the external-host locator contract,
- browser-authored absolute paths remain forbidden on Workflow Chat routes.

---

## 9. Event posting contract

### 9.1 First message event

```json
{
  "type": "message",
  "data": {
    "content": [
      {"type": "text", "text": "Implement the auth fix and open a PR."}
    ]
  },
  "metadata": {
    "moonmindFirstMessageDigest": "sha256:...",
    "moonmindIdempotencyKey": "..."
  }
}
```

### 9.2 First-message marker

```text
MoonMind-Omnigent-Run:
  correlationId: <correlation_id>
  idempotencyKey: <idempotency_key>
  firstMessageDigest: <sha256>
```

### 9.3 State transitions

```text
not_prepared -> prepared -> posting -> posted -> terminal
```

Rules:

1. Compute the canonical digest before any provider POST.
2. Persist `prepared` after digest calculation.
3. Persist `posting` immediately before forwarding.
4. Persist `posted` immediately after confirmed success.
5. Store provider pending/item identity when available.
6. A retry that sees `posted` skips the POST.
7. A retry that sees `posting` reconciles snapshots, pending inputs, item ids, stream events, and marker evidence.
8. If absence cannot be proven, fail closed instead of reposting.
9. A digest conflict under one idempotency key fails fast.

### 9.4 Native follow-up messages

Every native composer message passes through the binding-scoped facade.

Before forwarding, the bridge validates request authority and expected session state, assigns or validates a MoonMind idempotency key, and applies `MOONMIND_HIGH_SECURITY_MODE` to canonical extracted text.

The scan covers text-bearing message fields, supported slash-command arguments, approval response text, and textual attachment content forwarded by MoonMind. A blocked scan prevents the upstream request and reports only redacted finding category and location.

When high-security mode is enabled, an unknown or unparsable native event schema, unavailable scanner, or required textual payload that cannot be inspected fails closed. The bridge never forwards first and diagnoses later.

---

## 10. Stream and event normalization

### 10.1 Raw event retention

Provider frames are redacted and copied into:

```text
runtime.omnigent.sse.raw.jsonl
```

### 10.2 Normalized event shape

```json
{
  "schemaVersion": "moonmind.omnigent_bridge.event.v1",
  "sequence": 42,
  "timestamp": "2026-07-08T12:00:00Z",
  "bridgeSessionId": "brs_...",
  "omnigentSessionId": "sess_...",
  "moonmindWorkflowId": "mm:...",
  "moonmindAgentRunId": "ar_...",
  "direction": "host_to_moonmind",
  "type": "response.delta",
  "normalizedStatus": "running",
  "data": {"text": "Editing the auth callback..."},
  "artifactRefs": {},
  "metadata": {
    "moonmind": {
      "diagnosticChatVisible": true,
      "source": "omnigent_stream"
    }
  }
}
```

### 10.3 Recognized inbound event classes

Minimum classes include:

- `session.created`,
- `session.started`,
- `session.item.*`,
- `session.input.*`,
- `response.delta`,
- `response.output`,
- `response.completed`,
- `response.failed`,
- `response.elicitation_request`,
- `session.child.*`,
- `resource.changed_file`,
- `resource.session_file`,
- `host.heartbeat`,
- `host.capabilities`.

Unsupported event types are captured in diagnostics. Execution-critical drift fails closed; optional resource drift may degrade explicitly.

---

## 11. Control and capability contract

The bridge uses Omnigent-style controls while retaining MoonMind authority.

| Intent | Bridge control | Required authority |
|---|---|---|
| Send message | `type=message` | `sendMessage`, expected session state, outbound scan, audit. |
| Interrupt turn | `type=interrupt` | `interruptTurn`, expected active turn, idempotency, audit. |
| Stop session | `type=stop_session` | `stopSession`, expected session, workflow policy, audit. |
| Resolve elicitation | native resolve route | Caller approval authority, expected elicitation, policy, audit. |
| Request harvest | bridge-local `harvest_session` | Artifact/diagnostic authority; no provider mutation implied. |
| Clear/reset | capability-mapped native or new-session action | Explicit clear/reset policy and session-boundary evidence. |
| Create/write terminal | native terminal route | Separate terminal create/input capability, caller role, audit. |
| Read/mutate workspace | resource/file route | Separate read/write capability, path containment, audit for mutation. |
| Change model/effort/goal | native configuration route | Only when immutable profile and launch policy permit per-session change. |

The effective capability set is:

```text
upstream capability
∩ immutable Agent Profile snapshot
∩ Provider Profile and effective launch policy
∩ Workflow / Step / session state
∩ caller permission
```

The native client receives the filtered set to preserve a clean UX. The server recomputes it on every request. Upstream support never grants MoonMind permission.

---

## 12. Resource harvesting contract

### 12.1 Changed files

```http
GET /v1/sessions/{id}/resources/environments/default/changes
GET /v1/sessions/{id}/resources/environments/default/filesystem/{path}
```

Store:

```text
output.omnigent.changed_files.index.json
output.omnigent.changed_files/<path>
```

### 12.2 Workspace files

```http
GET /v1/sessions/{id}/resources/environments/default/filesystem
GET /v1/sessions/{id}/resources/environments/default/filesystem/{path}
```

Store:

```text
output.omnigent.workspace_files.index.json
output.omnigent.workspace_files/<path>
```

### 12.3 Workspace diffs

```http
GET /v1/sessions/{id}/resources/environments/default/diff/{path}
```

Diff capture is capability-probed. If unavailable, publish diagnostics instead of failing solely because optional patch capture is unavailable.

```text
output.omnigent.workspace_diffs/<path>.diff
output.omnigent.patch_unavailable.json
```

### 12.4 Session files

```http
GET /v1/sessions/{id}/resources/files
GET /v1/sessions/{id}/resources/files/{file_id}/content
```

```text
output.omnigent.session_files.index.json
output.omnigent.session_files/<file_id>/<filename>
output.omnigent.session_files/<file_id>/metadata.json
```

### 12.5 Child sessions

```text
runtime.omnigent.child_sessions.jsonl
runtime.omnigent.child_sessions/<child_session_id>.json
```

Live resource browsing through native Chat remains bound, authorized, bounded, and non-authoritative. Artifact publication creates the durable workflow evidence.

---

## 13. Artifact outputs

Each terminal bridge session produces at least:

```text
runtime.omnigent.sse.raw.jsonl
runtime.omnigent.sse.normalized.jsonl
runtime.omnigent.snapshot.initial.json
output.omnigent.snapshot.final.json
output.omnigent.capture_manifest.json
runtime.omnigent.diagnostics.json
checkpoint.omnigent.external_state.json
runtime.omnigent.control_audit.jsonl
```

Optional resource artifacts include changed-file, workspace-file, session-file, diff, patch-unavailable, and child-session indexes.

---

## 14. AgentRun integration

### 14.1 Request shape

```json
{
  "agentKind": "external",
  "agentId": "omnigent",
  "parameters": {
    "omnigent": {
      "endpointRef": "default",
      "session": {
        "hostType": "managed",
        "workspace": "https://github.com/org/repo#main"
      },
      "capture": {
        "stream": true,
        "snapshots": true,
        "changedFiles": true,
        "sessionFiles": true
      }
    }
  }
}
```

A direct managed runtime that temporarily emits bridge events declares the mode under `parameters.communication`; it does not invent a top-level field outside the request schema.

The direct compatibility producer publishes typed runtime-neutral events before terminal synthesis. Approval and control events are rejected unless actor, idempotency, expected session/epoch/turn, outcome, and durable audit evidence are retained.

For parity validation, `communication.comparisonMode = dual_write` compares independently persisted event evidence. The primary Workflow Detail Chat still renders the native Omnigent application. Comparison and diagnostic projections never create a second primary timeline or claim host identity.

The direct producer may be removed only after production coverage, conformance parity, historical retention, and durable Temporal-history gates pass.

### 14.2 Terminal result

```json
{
  "outputRefs": [
    "art_final_snapshot",
    "art_normalized_stream",
    "art_capture_manifest"
  ],
  "summary": "Implemented the change and opened PR ...",
  "diagnosticsRef": "art_diagnostics",
  "failureClass": null,
  "metadata": {
    "providerName": "omnigent",
    "normalizedStatus": "completed",
    "captureManifestRef": "art_capture_manifest",
    "externalStateRef": "art_external_state"
  }
}
```

Provider-native refs must not appear in top-level `outputRefs`.

---

## 15. Native Workflow Chat integration

The product contract is owned by [`docs/UI/WorkflowChatPanel.md`](../UI/WorkflowChatPanel.md).

The backend resolves the authoritative session server-side from Workflow, run, Step Execution, AgentRun, and bridge state. It returns a browser-safe binding:

```ts
type WorkflowChatBinding = {
  chatBindingId: string;
  workflowId: string;
  runId: string;
  logicalStepId?: string;
  stepExecutionId?: string;
  chatUrl: string;
  apiBase: string;
  state: 'starting' | 'available' | 'ended' | 'unavailable';
  readOnly: boolean;
  capabilities: Record<string, boolean>;
  unavailableReason?: string;
};
```

Provider session, bridge session, endpoint, host, runner, credential, and immutable profile identities remain server-side unless an authorized diagnostic endpoint exposes bounded safe refs.

The native Omnigent application is the primary Workflow Chat renderer. Ordinary messages use the binding-scoped facade and do not pass through Temporal or `SubmitChatInstruction`.

The existing bridge-event routes remain canonical read-only diagnostic/compatibility APIs:

| Purpose | Route |
|---|---|
| List bridge events | `GET /api/omnigent/bridge-sessions/{bridge_session_id}/events` |
| Stream bridge events | `GET /api/omnigent/bridge-sessions/{bridge_session_id}/stream` |

They support diagnostics, historical fallback, evidence inspection, and runtimes without a native session. They do not define a second ordinary composer or replace the native primary surface.

For terminal work, the native transcript is read-only. MoonMind may expose **Continue in a new workflow** using pinned source identity and authorized artifact refs; this is an explicit Workflow action, not a native message or chat-instruction compatibility path.

### 15.1 Serving the native UI through MoonMind-scoped routes

MoonMind serves the provider-maintained native Omnigent web application through its own origin at the binding-scoped route `GET /omnigent-ui/workflow-chat/{chatBindingId}[?embedded=1]` (and its SPA sub-paths). It reverse-proxies the stock UI assets from the upstream server, serves the SPA document with an injected browser-safe bootstrap, and never copies the native React source or lets the browser connect directly to the upstream server. The same scoped surface backs both the embedded Workflow Detail view and the full-page **Open in Omnigent** view; the full-page view drops only the `embedded` presentation flag and uses the same binding, facade, credentials, and policy.

The served document injects `window.__MOONMIND_OMNIGENT_CHAT__`, a browser-safe bootstrap carrying only: the opaque `chatBindingId`; the scoped API/WebSocket base (`/api/workflow-chat-bindings/{chatBindingId}/omnigent`); the presentation mode (`embedded`/`full_page`); read-only state; the filtered effective capability manifest with disabled reasons; safe display labels; and a stable compatibility version. It never carries a raw provider session id, upstream URL, host/runner id, credential, profile ref, launch policy, or workspace authority. Root-absolute asset URLs are rewritten onto the scoped route so every asset, API, SSE, and WebSocket request stays on the MoonMind origin.

Security policy for the served responses is explicit: embedded documents allow `frame-ancestors 'self'` (`X-Frame-Options: SAMEORIGIN`); the full-page view refuses framing (`frame-ancestors 'none'` / `DENY`). Documents carry the caller's bootstrap and are never cached (`Cache-Control: no-store` + `Vary: Cookie`), so one binding's bootstrap or private session state cannot leak to another caller; hashed assets carry no binding data and are privately cacheable. Upstream redirects are re-based inside the scoped route and never expose upstream topology.

Serving is gated on a known-compatible native UI/server version. An unknown or unsupported version fails with a stable, actionable `omnigent_native_chat_unavailable` state rather than partially bypassing the scoped facade. Operator configuration is namespaced and safe-by-default: `OMNIGENT_NATIVE_UI_ENABLED` (default enabled) toggles serving, and `OMNIGENT_NATIVE_UI_VERSION` pins the running upstream build (default: the single upstream source pin MoonMind is verified against). Readiness reports native-UI serving, the compatibility version, the scoped HTTP/SSE/WebSocket routes, and credential separation under `compatibilityDiagnostics.nativeUi`.

---

## 16. Security and authentication

Rules:

1. Every browser and service request is authenticated and authorized against its Workflow, AgentRun, bridge session, and requested operation.
2. `chatBindingId` is an opaque lookup key, not a bearer capability.
3. Every HTTP, SSE, and WebSocket connect or reconnect repeats authorization and binding validation.
4. Any session id in route, query, or payload must map exactly to the bound provider session; substitution fails closed.
5. Effective capabilities are recomputed server-side from immutable snapshots and current caller/workflow/session state.
6. Model, effort, goal, terminal, browser, file mutation, approval, interrupt, stop, clear, cleanup, and other controls cannot exceed MoonMind policy even when upstream supports them.
7. Mutations retain actor, idempotency, expected identities/state, normalized outcome, upstream correlation, and durable audit evidence.
8. Text-bearing provider sends run the canonical outbound secret scan when high-security mode is enabled; unavailable enforcement fails closed.
9. Omnigent endpoint credentials are service-side secrets and never enter browser or Temporal payloads.
10. MoonMind cookies, bearer tokens, CSRF tokens, and internal auth headers are stripped before upstream forwarding. Only allowlisted transport headers and server-injected upstream credentials are forwarded.
11. Raw provider events are redacted before persistence.
12. MoonMind artifact refs remain the durable evidence boundary.
13. Embedded mode must pass stock-host auth conformance before production enablement.

```text
MoonMind user -> MoonMind request authorization
MoonMind scoped facade -> server-side Omnigent credential
Omnigent host -> host/runner auth profile
```

No configuration may turn a direct upstream browser URL into an authority bypass for native Workflow Chat.

---

## 17. Error classification

| Failure | Failure class / response posture | Notes |
|---|---|---|
| Binding absent or stale | typed unavailable/retry state | Never select another session implicitly. |
| Caller unauthorized | `403` / audit | Do not reveal whether an alternate provider session exists. |
| Session or endpoint substitution | `403` or `409` / audit | Fail closed before upstream. |
| Capability or immutable-policy denial | typed policy rejection | Upstream support does not override policy. |
| High-security scan blocked | typed security rejection | Redacted category/location only. |
| Scan/parser unavailable in high-security mode | typed fail-closed rejection | No provider send occurs. |
| Upstream unavailable before create | `integration_error` | Retry according to transport policy. |
| Host cannot register/connect | `integration_error` or `system_error` | Preserve redacted diagnostics. |
| Upstream authentication failure | `integration_error` | Non-retryable until configuration is corrected. |
| Invalid create payload | `user_error` | Reject caller-selected managed host/path authority. |
| First-message digest mismatch | `user_error` | Conflicting replay. |
| Ambiguous `posting` reconciliation | `integration_error` | Fail closed instead of duplicate post. |
| Stream disconnect while session active | `integration_error` | Reauthorize and reconcile. |
| Runtime/harness failure | `execution_error` | Preserve provider evidence. |
| Session/host timeout | `system_error` | Keep `timed_out` distinct. |
| Optional resource harvest failure | primary result plus diagnostics | Unless policy requires full evidence. |
| Required artifact persistence failure | `system_error` | MoonMind evidence authority failed. |

---

## 18. Target module boundaries

The disposable delivery sequence lives in [`docs/tmp/OmnigentBridgeRollout.md`](../tmp/OmnigentBridgeRollout.md).

### 18.1 Canonical package placement

Bridge code lives in the existing `moonmind/omnigent/` package. A parallel package would duplicate ownership.

### 18.2 Component-to-module ownership

| Component | Owning module | Notes |
|---|---|---|
| Bridge configuration | `moonmind/omnigent/bridge_config.py` | Parses §6. |
| Bridge Session Store | `moonmind/omnigent/bridge_store.py` | Canonical binding/idempotency store. |
| Bridge schemas | `moonmind/schemas/omnigent_bridge_models.py` | Session/event/config/binding models. |
| Durable ORM/migration | `api_service/db/models.py` plus migrations | §7 persistence. |
| Session and Workflow Chat facades | `api_service/api/routers/omnigent_bridge.py`, `moonmind/omnigent/bridge_proxy.py` | Provider compatibility plus binding-scoped native UI/API. |
| Event Normalizer | `moonmind/omnigent/bridge_events.py` | §10. |
| Artifact Publisher / Harvester | `moonmind/omnigent/bridge_artifacts.py` | §12–13. |
| Native Workflow Chat presentation | stock Omnigent web application | Primary interaction UI under the MoonMind scoped facade. |
| Diagnostic Chat projection | API + MoonMind diagnostic UI | Read-only bridge-event and legacy fallback surface. |

### 18.3 Superseded patterns

`bridge_store.py` and `omnigent_bridge_sessions` supersede the former `omnigent_external_runs` mapping without an alias or parallel table.

The custom MoonMind transcript/composer/session-controls combination is superseded as the primary Chat product when a native binding is available. The projection remains only for diagnostics, history, and explicit compatibility fallback.

---

## 19. Acceptance criteria

A successful implementation satisfies:

1. A stock Omnigent host participates without a custom build.
2. MoonMind creates or attaches an Omnigent-shaped session with first-message idempotency.
3. Workflow Detail opens the native Omnigent application through an opaque authorized binding.
4. Every native UI, HTTP, SSE, WebSocket, resource, message, approval, terminal, and control request is reauthorized against that binding.
5. Substituting provider session or upstream identity fails closed.
6. Native controls cannot exceed immutable Agent Profile, Provider Profile, launch, approval, billing, workflow-state, or caller policy.
7. High-security mode scans outbound native text before send and fails closed when enforcement is unavailable.
8. MoonMind credentials never reach upstream and upstream credentials never reach the browser.
9. MoonMind streams and captures provider events while keeping the native UI as the primary renderer.
10. The event projection remains read-only diagnostic/compatibility evidence.
11. MoonMind copies snapshots/resources/audits into artifacts.
12. First-message retries do not duplicate the prompt.
13. Failed launch paths create visible diagnostics and a fallback timeline.
14. Provider-native refs do not replace MoonMind artifact refs.
15. Terminal native Chat is read-only and linked continuation is an explicit Workflow action.

---

## 20. Testing strategy

### 20.1 Unit and policy tests

- bridge config validation,
- binding allocation and lookup,
- per-request authorization across HTTP/SSE/WebSocket reconnects,
- alternate session/endpoint/host/runner substitution rejection,
- effective capability intersection,
- pinned model/effort and approval-policy denial,
- actor/idempotency/expected-state/outcome/audit validation,
- MoonMind header stripping and upstream credential injection,
- high-security message extraction, block, and fail-closed behavior,
- first-message state transitions and reconciliation,
- event normalization and redaction,
- artifact ref validation,
- diagnostic projection resolution.

### 20.2 Fake Omnigent server tests

Cover successful native UI bootstrap, message/stream flow, queued/steered turns, approvals, resource reads, terminal controls, stream reconnect, provider-session substitution attempts, policy-denied controls, security-scan blocks, first-message retries, child sessions, and terminal evidence.

### 20.3 Browser journey tests

Prove that:

- `/workflows/{workflowId}/chat` opens the bound native session,
- native composer, transcript, queue, approvals, and workspace rail function through the scoped facade,
- hidden controls are also rejected by direct API invocation,
- full-page Open in Omnigent retains the scoped boundary,
- an unauthorized user or altered binding cannot read or control another session,
- terminal Chat is read-only,
- diagnostic fallback does not expose a second composer.

### 20.4 Stock host smoke tests

Verify host registration, heartbeat/capabilities, session creation, harness launch, message posting, stream capture, final snapshot, resource harvest, credential separation, no duplicate first message, and durable cleanup/audit evidence against a real unchanged host.

---

## 21. Open questions

1. Which upstream WebSocket paths require explicit rewriting in each compatibility profile?
2. Which upstream host auth modes are supported in embedded mode?
3. Which binary attachment types are allowed under high-security mode when their content cannot be text-scanned?
4. What is the minimum stock-host conformance suite before embedded mode is enabled?
5. Which native clear/reset operations require a new session and explicit reset-boundary artifact?

---

## 22. Non-goals

This design does not:

- fork or custom-build the Omnigent host,
- recreate the native Omnigent Chat UI in MoonMind,
- expose the upstream Omnigent server directly to bypass MoonMind authorization,
- make provider session ids become Workflow ids,
- expose raw provider session states as Workflow states,
- make provider resources authoritative over MoonMind artifacts,
- allow native controls to override immutable MoonMind policy,
- bypass outbound secret scanning for native messages,
- blindly repost first messages on retry,
- require embedded host mode before proxy mode proves the contract,
- replace all direct Codex compatibility code in the first bridge slice.
