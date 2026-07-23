# Omnigent Bridge

Status: Proposed design  
Owners: MoonMind Platform  
Last updated: 2026-07-08

**Implementation tracking:** rollout notes, spikes, and temporary handoffs should live under `docs/tmp/` or gitignored local-only artifacts, not as mutable checklists in this canonical design document.

## Related docs

- [`docs/Omnigent/CodexCreateToHostContract.md`](./CodexCreateToHostContract.md)
- [`docs/Omnigent/OmnigentAdapter.md`](./OmnigentAdapter.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](../ManagedAgents/CodexCliManagedSessions.md)
- [`docs/Observability/LiveLogs.md`](../Observability/LiveLogs.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)
- [`docs/Temporal/WorkflowArtifactSystemDesign.md`](../Temporal/WorkflowArtifactSystemDesign.md)
- [`docs/Temporal/VisibilityAndUiQueryModel.md`](../Temporal/VisibilityAndUiQueryModel.md)
- [`docs/ExternalAgents/AddingExternalProvider.md`](../ExternalAgents/AddingExternalProvider.md)

---

## 1. Purpose

This document defines the design for a **MoonMind Omnigent Bridge**: a MoonMind API capability that exposes Omnigent-shaped session, event, stream, and resource communication while preserving MoonMind as the durable orchestration and artifact authority.

The bridge exists to move MoonMind toward the Omnigent communication model without requiring an immediate full cutover of every managed runtime. In particular, it should allow MoonMind to communicate with an **unchanged Omnigent host** whenever deployment topology and upstream compatibility allow it.

The target direction is:

```text
MoonMind UI / API / Temporal
  -> MoonMind Omnigent Bridge
      -> Omnigent-shaped session/event/resource surface
          -> unchanged Omnigent host / runner
              -> Codex, Claude, or another host-supported harness
```

MoonMind remains responsible for:

- Temporal workflow orchestration
- AgentRun identity
- Workflow Chat presentation
- artifact refs and artifact authorization
- diagnostics and operator audit evidence
- step execution evidence

The host/runtime remains responsible for:

- live runtime execution
- harness launch and lifecycle inside its environment
- transcript deltas and runtime events
- host-side resource discovery
- changed-file/session-resource reporting

---

## 2. Design principles

### 2.1 Use Omnigent names at the bridge boundary

The bridge boundary should use Omnigent-style nouns and operations:

- `session`
- `event`
- `stream`
- `host`
- `runner`
- `resource`
- `snapshot`
- `interrupt`
- `stop_session`

Do not introduce a new product vocabulary such as `runtime bus`, `agent socket`, or `conversation broker` for the external contract unless there is a clear MoonMind-only internal concern.

### 2.2 Keep MoonMind artifact authority

The bridge may observe Omnigent host resources, but MoonMind artifacts remain the durable evidence boundary.

Provider-native ids, URLs, host paths, and file ids may appear in diagnostics or mapping metadata. They must not replace MoonMind artifact refs in workflow evidence, step evidence, or terminal `AgentRunResult.outputRefs`.

### 2.3 Keep the host unchanged

A successful design must support a stock Omnigent host. No custom host image or source patch should be required for the bridge contract.

Deployment configuration is allowed, including:

- the server/base URL the host points at;
- host authentication configuration;
- endpoint refs;
- network routing;
- standard Omnigent host settings.

A custom MoonMind-specific host build is out of scope.

### 2.4 Prefer proxy-first compatibility

The bridge has two compatibility modes:

```yaml
hostProtocolMode:
  - upstream_omnigent_server_proxy
  - embedded_omnigent_compatible_server
```

`upstream_omnigent_server_proxy` is the preferred first implementation because a stock Omnigent Server already owns the host/runner tunnel and is the lowest-risk way to keep hosts unchanged.

`embedded_omnigent_compatible_server` is a later mode where MoonMind API directly implements enough of the Omnigent-compatible host/session server surface for an unchanged host to connect to MoonMind without a separate Omnigent Server process.

The declarative contract should support both modes.

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

- MoonMind exposes the bridge to MoonMind workflows and UI.
- The bridge persists MoonMind session bindings, idempotency state, event refs, and artifact refs.
- The bridge calls the stock Omnigent Server session/event/resource API.
- The stock Omnigent Server owns the host/runner tunnel.
- The unchanged host continues to speak its native Omnigent host protocol.

This mode is recommended for the first production slice.

### 3.2 Embedded compatibility mode

```text
MoonMind UI / API
  -> MoonMind Omnigent Bridge
      -> embedded Omnigent-compatible server surface
          -> unchanged Omnigent Host / Runner
              -> Codex / Claude / other harness
```

Responsibilities:

- MoonMind exposes Omnigent-compatible session/event/resource APIs.
- MoonMind also implements or embeds the host-facing protocol expected by an unchanged Omnigent host.
- The host points directly at MoonMind's bridge endpoint.

This mode should only be enabled after proxy mode has conformance coverage and live smoke-test evidence.

### 3.3 Direct Codex compatibility during migration

```text
MoonMind UI / API
  -> MoonMind Omnigent Bridge event model
      -> direct Codex managed-session adapter
          -> Codex managed runtime
```

This is a temporary migration path. The direct Codex adapter can emit Omnigent-shaped session events while MoonMind continues to support current managed-session execution. Once Codex execution moves behind Omnigent, this compatibility producer can be retired.

---

## 4. Protocol surfaces

### 4.1 Public session API surface

The bridge should expose or proxy these Omnigent-shaped HTTP/SSE routes:

| Purpose | Route |
|---|---|
| List available agents | `GET /api/agents` |
| Create session | `POST /v1/sessions` |
| Get session snapshot | `GET /v1/sessions/{session_id}` |
| Post session event | `POST /v1/sessions/{session_id}/events` |
| Stream session events | `GET /v1/sessions/{session_id}/stream` |
| List changed files | `GET /v1/sessions/{session_id}/resources/environments/default/changes` |
| List workspace files | `GET /v1/sessions/{session_id}/resources/environments/default/filesystem` |
| Get workspace file content | `GET /v1/sessions/{session_id}/resources/environments/default/filesystem/{path}` |
| Get workspace file diff | `GET /v1/sessions/{session_id}/resources/environments/default/diff/{path}` |
| List session files | `GET /v1/sessions/{session_id}/resources/files` |
| Get session file content | `GET /v1/sessions/{session_id}/resources/files/{file_id}/content` |

#### `omnigent.server.v1` proxy compatibility matrix

This versioned profile is the facade contract implemented for
MoonLadderStudios/MoonMind#3361. All session-scoped operations first resolve the
durable MoonMind binding and authorize its workflow owner. Optional resource
operations return the stable `omnigent_bridge_capability_unavailable` code when
the stock server does not provide them.

| Operation | Facade route | Stock-server operation | Policy |
|---|---|---|---|
| Agent discovery/selection | `GET /api/agents` | `GET /api/agents` | Authenticated catalog; session creation resolves the selected/default agent. |
| Host readiness | `GET /api/hosts` | `GET /api/hosts` | Bounded readiness metadata only; no caller-selected host is accepted for managed profile routing. |
| Create/reuse | `POST /v1/sessions` | `POST /v1/sessions` | Workflow-owned idempotency binding and first-message reconciliation. |
| Snapshot | `GET /v1/sessions/{id}` | Same | Owned provider sessions only. |
| Attach/reconcile | `POST /v1/sessions/{id}/attach` | Snapshot probe, then durable attach | Requires an existing owned idempotency binding and rejects conflicting attachment. |
| Stop/interrupt/message | `POST /v1/sessions/{id}/events` | Same | Canonical Omnigent event vocabulary. |
| Delete | `DELETE /v1/sessions/{id}` | Same | Capability-gated by `deleteProviderSessionAfterHarvest`. |
| Provider stream | `GET /v1/sessions/{id}/stream` | Same | Owner-authorized SSE pass-through; execution-critical drift fails closed. |
| Resolve elicitation | `POST /v1/sessions/{id}/elicitations/{eid}/resolve` | Same | Owner-authorized control. |
| Changed/workspace file indexes | `GET .../changes`, `GET .../filesystem` | Same | Lists are bounded by the facade. |
| Workspace content/diff | `GET .../filesystem/{path}`, `GET .../diff/{path}` | Same | One decode/encode boundary, traversal rejection, and bounded response bytes; diff is optional. |
| Session file index/content | `GET .../resources/files`, `GET .../resources/files/{file_id}/content` | Same | Owner-authorized, bounded, and capability-gated. |

Workflow-visible durable evidence continues through the Artifact Publisher;
these facade responses are transport results and do not make raw upstream paths
or identifiers authoritative workflow evidence.

The public Omnigent-compatible API usually lives on the Omnigent server API/UI port. In MoonMind deployments this may be:

```text
host port 7000 -> MoonMind API app port 8000
```

or a dedicated bridge hostname/path.

### 4.2 Host/runner channel

The host/runner channel is the persistent bi-directional control and event channel used by an Omnigent host to register, advertise capabilities, heartbeat, and deliver session/runtime events.

In proxy mode:

```text
unchanged host -> stock Omnigent Server host/runner channel
MoonMind Bridge -> stock Omnigent Server public session API
```

In embedded mode:

```text
unchanged host -> MoonMind embedded Omnigent-compatible host/runner channel
MoonMind Bridge -> local session/event/resource state
```

The exact tunnel port and transport are deployment/profile-specific. The bridge must treat the host channel as a compatibility profile, not an ad hoc MoonMind protocol.

---

## 5. Bridge component model

```text
MoonMind Omnigent Bridge
  ├─ Session API Facade
  ├─ Host Protocol Facade / Proxy
  ├─ Bridge Session Store
  ├─ Event Normalizer
  ├─ Resource Harvester
  ├─ Artifact Publisher
  ├─ Workflow Chat Projection
  └─ Direct Codex Compatibility Producer (temporary)
```

### 5.1 Session API Facade

Owns Omnigent-shaped routes used by MoonMind code and, in embedded mode, by Omnigent-compatible clients.

### 5.2 Host Protocol Facade / Proxy

In proxy mode, forwards to stock Omnigent Server.

In embedded mode, implements enough of the Omnigent-compatible host-facing protocol for an unchanged host to register and exchange session events.

### 5.3 Bridge Session Store

Persists MoonMind-to-provider session bindings, first-message idempotency, event refs, terminal refs, snapshots, and diagnostics refs.

### 5.4 Event Normalizer

Converts provider/host events into MoonMind-safe normalized event records while preserving raw events in artifact-backed JSONL.

### 5.5 Resource Harvester

Copies changed files, workspace files, diffs, session files, child-session snapshots, and diagnostics into MoonMind artifacts.

### 5.6 Artifact Publisher

Publishes all bridge evidence through the MoonMind artifact system and returns only MoonMind artifact refs to workflow-visible results.

### 5.7 Workflow Chat Projection

Feeds Workflow Chat from bridge session events before falling back to legacy managed-run logs.

---

## 6. Declarative bridge configuration

```yaml
schemaVersion: moonmind.omnigent_bridge.v1

enabled: true

authority:
  temporal: moonmind
  artifacts: moonmind
  liveExecution: omnigent_host

compatibility:
  profile: omnigent.server.v1
  hostUnchanged: true
  hostProtocolMode: upstream_omnigent_server_proxy
  # Alternative later:
  # hostProtocolMode: embedded_omnigent_compatible_server

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

hostConnection:
  mode: upstream_omnigent_server_proxy
  upstreamServerUrlRef: default
  embedded:
    bindAddress: 0.0.0.0
    port: 8000
    authMode: upstream_runner_tunnel
    protocolProfile: omnigent.runner_tunnel.538494ff

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
  feedWorkflowChat: true
  feedAgentRunObservability: true
  fallbackToLegacyManagedRunLogs: true
```

The Session API Facade resolves this document from `OMNIGENT_BRIDGE_CONFIG_PATH`
before it registers routes, so the operator-declared `enabled` flag, host
protocol mode, and `publicApi.mountPath`/routes are honored (a disabled or
custom-mounted bridge is not overridden by the proxy-first defaults). When the
variable is unset the safe proxy-first defaults apply; an unreadable path or an
invalid document fails fast rather than silently mounting the default surface.

---

## 7. Durable data model

### 7.1 `omnigent_bridge_sessions`

```text
omnigent_bridge_sessions
  bridge_session_id text primary key
  provider text not null                         # omnigent
  compatibility_profile text not null            # omnigent.server.v1
  moonmind_workflow_id text not null
  moonmind_run_id text null
  moonmind_agent_run_id text not null
  step_execution_id text null
  idempotency_key text not null unique

  omnigent_endpoint_ref text not null
  omnigent_session_id text null
  omnigent_host_id text null
  omnigent_runner_id text null
  omnigent_agent_id text null
  omnigent_agent_name text null

  host_type text not null                        # managed | external
  workspace text null
  status text not null                           # declared | creating | active | completed | failed | canceled | timed_out

  first_message_state text not null              # not_prepared | prepared | posting | posted | terminal
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

`omnigent_bridge_sessions` is the single canonical Omnigent session and
idempotency store. It replaces the existing `omnigent_external_runs` mapping
owned by `OmnigentRunStore` in `moonmind/omnigent/store.py`: that mapping is
migrated into `omnigent_bridge_sessions` and the superseded store is removed in
the same cohesive change, with no parallel table, alias, or compatibility
wrapper. This guarantees that retries and Workflow Chat always read one durable
store rather than diverging depending on which table a caller reads.

`status` is a terminal-safe mapping (a coalescence, not a superset) of the
normalized statuses already produced by `moonmind/omnigent/execute.py`. The
explicit lifecycle-to-normalized-status mapping is:

- `declared` and `creating` are bridge lifecycle states that cover session
  registration and setup before the provider reports a normalized status.
- The non-terminal normalized statuses — `created`, `launching`,
  `provisioning`, `running`, `waiting`, `idle`, `awaiting_approval`, and
  `intervention_requested` — all coalesce into the single `active` value.
- The terminal normalized statuses map straight through: `completed`, `failed`,
  `canceled`, and `timed_out`.

`timed_out` is preserved as a distinct terminal status (mapped to the
`system_error` failure class, matching the existing normalization) so timeouts
are never collapsed into `failed`. The session-level `status` column is
intentionally coarse; the full, non-lossy normalized status stream is preserved
per event on `omnigent_bridge_session_events.normalized_status` (§7.2), so
retry, operator, and Workflow Chat/diagnostics decisions still see every
non-terminal state across the provider path. See §17 for the full failure-class
mapping.

### 7.2 `omnigent_bridge_session_events`

```text
omnigent_bridge_session_events
  event_id text primary key
  bridge_session_id text not null
  sequence bigint not null
  timestamp timestamptz not null
  direction text not null                         # moonmind_to_host | host_to_moonmind | system
  event_type text not null
  normalized_status text null
  text_preview text null
  artifact_ref text null
  metadata jsonb not null default '{}'
```

The DB event rows are an index. The full raw and normalized event bodies live in MoonMind artifacts:

```text
runtime.omnigent.sse.raw.jsonl
runtime.omnigent.sse.normalized.jsonl
```

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

1. Validate the MoonMind principal and workflow ownership.
2. Create or reuse `omnigent_bridge_sessions` by `idempotency_key`.
3. Resolve endpoint and target agent.
4. Forward to the stock Omnigent Server or embedded compatibility backend.
5. Persist `omnigent_session_id` before preparing or posting the first message.
6. Emit `session.created` into the bridge event journal.
7. Return an Omnigent-shaped session response.

### 8.3 Managed/external host validation

For `host_type = managed`:

- `host_id` must be absent.
- `workspace` may be a repository URL with optional `#branch`.
- local absolute paths are invalid.
- repository-edit tasks should provide a repository workspace unless explicitly opting into an empty workspace.

For `host_type = external`:

- `host_id` is required.
- `workspace` must be an absolute host path.
- repository URLs are invalid.

---

## 9. Event posting contract

### 9.1 First message event

```json
{
  "type": "message",
  "data": {
    "content": [
      {
        "type": "text",
        "text": "Implement the auth fix and open a PR."
      }
    ]
  },
  "metadata": {
    "moonmindFirstMessageDigest": "sha256:...",
    "moonmindIdempotencyKey": "..."
  }
}
```

### 9.2 First-message marker

When enabled, append a compact non-secret marker to the first message body:

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

1. Compute the canonical first-message digest before any HTTP POST.
2. Persist `prepared` after the digest is computed.
3. Persist `posting` immediately before forwarding `POST /events`.
4. Persist `posted` immediately after a successful response.
5. Store provider `pending_id` or `item_id` when available.
6. If retry sees `posted`, skip the first POST.
7. If retry sees `posting`, reconcile before deciding whether to repost.
8. Reconciliation checks snapshots, pending inputs, item ids, stream events, and the idempotency marker.
9. If absence cannot be proven, fail closed instead of blindly reposting.
10. If the digest differs for an existing idempotency key, fail fast.

---

## 10. Stream and event normalization

### 10.1 Raw event retention

All provider stream frames should be copied into:

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
  "data": {
    "text": "Editing the auth callback..."
  },
  "artifactRefs": {},
  "metadata": {
    "moonmind": {
      "workflowChatVisible": true,
      "source": "omnigent_stream"
    }
  }
}
```

### 10.3 Recognized inbound event classes

Minimum recognized inbound events:

- `session.created`
- `session.started`
- `session.item.*`
- `session.input.*`
- `response.delta`
- `response.output`
- `response.completed`
- `response.failed`
- `response.elicitation_request`
- `session.child.*`
- `resource.changed_file`
- `resource.session_file`
- `host.heartbeat`
- `host.capabilities`

Unsupported event types should be captured in diagnostics. Contract-drift behavior is policy-controlled: fail closed for execution-critical events, degrade for optional resource events.

---

## 11. Control vocabulary

MoonMind should prefer Omnigent-style controls at the bridge boundary:

| MoonMind intent | Bridge event/control | Notes |
|---|---|---|
| Send operator/user turn | `POST /v1/sessions/{id}/events` with `type=message` | Primary send path. |
| Interrupt active turn | `type=interrupt` | Soft cancel. |
| Stop session | `type=stop_session` | Harder stop after interrupt grace. |
| Resolve elicitation | `POST /v1/sessions/{id}/elicitations/{eid}/resolve` or equivalent | Exact route depends on compatibility profile. |
| Request harvest | bridge-local `harvest_session` action | MoonMind artifact action, not necessarily host-native. |
| Clear/reset context | bridge-local policy mapped to host/session capability | May require a new session when the unchanged host does not expose Codex-like `clear_session`. |

The bridge must not assume that every direct Codex control has an exact host-native equivalent. Where no equivalent exists, it should use explicit policy and diagnostics rather than silently pretending the operation was identical.

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

Diff capture is capability-probed. If unavailable, store diagnostics instead of failing the entire run solely because patch capture is unavailable.

Store:

```text
output.omnigent.workspace_diffs/<path>.diff
output.omnigent.patch_unavailable.json
```

Each `<path>.diff` holds the unified diff returned by the host, matching the
single-file `.diff` layout already produced by `moonmind/omnigent/execute.py`.

### 12.4 Session files

```http
GET /v1/sessions/{id}/resources/files
GET /v1/sessions/{id}/resources/files/{file_id}/content
```

Store:

```text
output.omnigent.session_files.index.json
output.omnigent.session_files/<file_id>/<filename>
output.omnigent.session_files/<file_id>/metadata.json
```

### 12.5 Child sessions

If child sessions are emitted, store:

```text
runtime.omnigent.child_sessions.jsonl
runtime.omnigent.child_sessions/<child_session_id>.json
```

---

## 13. Artifact outputs

Each completed or failed bridge session should produce at least:

```text
runtime.omnigent.sse.raw.jsonl
runtime.omnigent.sse.normalized.jsonl
runtime.omnigent.snapshot.initial.json
output.omnigent.snapshot.final.json
output.omnigent.capture_manifest.json
runtime.omnigent.diagnostics.json
checkpoint.omnigent.external_state.json
```

Optional resource artifacts include:

```text
output.omnigent.changed_files.index.json
output.omnigent.workspace_files.index.json
output.omnigent.session_files.index.json
output.omnigent.workspace_diffs/*
output.omnigent.patch_unavailable.json
runtime.omnigent.child_sessions.jsonl
```

---

## 14. AgentRun integration

### 14.1 Request shape

MoonMind workflows can select the bridge with:

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

If a direct managed runtime uses the bridge event model during migration, it
declares the mode under the supported `parameters` field. `AgentExecutionRequest`
in `moonmind/schemas/agent_runtime_models.py` sets `extra="forbid"` and has no
top-level `communication` field, so the mode must live inside `parameters`
(a free-form mapping) rather than as a new top-level key:

```json
{
  "agentKind": "managed",
  "agentId": "codex_cli",
  "parameters": {
    "communication": {
      "mode": "omnigent_bridge",
      "compatibilityProfile": "moonmind.codex_direct_compat.v1"
    }
  }
}
```

Introducing a first-class top-level `communication` field would instead require
an explicit `AgentExecutionRequest` schema change with worker-boundary tests; it
must not be assumed by the contract until that change lands.

The `moonmind.codex_direct_compat.v1` producer is disabled unless that explicit
mode is selected. It publishes the active session/start, submitted user message,
and turn-start records before the direct turn runs. Typed, runtime-neutral
`observabilityEvents` are appended through a separate `active` phase before
terminal/resource synthesis; assistant, tool, approval, control, turn, and reset
observations therefore do not derive their authority from a terminal summary.
Approval and control observations are rejected unless they retain actor,
idempotency, expected session/epoch/turn, outcome, and durable audit evidence.
Activity retries deduplicate source event IDs against the durable bridge index;
raw stdout/stderr and live-log lines remain artifact-backed and are referenced as
resources instead of copied into the chat event stream.

For internal parity validation, deployments may set
`communication.comparisonMode` to `dual_write`. Workflow Detail still renders
only the bridge projection. Comparison reads the independently persisted
non-compatibility producer stream from the durable journal; callers do not pass
an expected event list. Diagnostics report unavailable comparison evidence,
missing, duplicate, dropped, or reordered records, plus terminal/resource/control
semantic mismatches. They never create a second visible timeline or claim an
Omnigent host identity.

This producer may be removed only when all of the following are true:

- production Codex executions have profile-bound Omnigent coverage;
- direct and Omnigent conformance fixtures have projection parity;
- the historical direct-session reader has served its configured retention
  window; and
- no active Temporal history can schedule or retry the compatibility write
  activity.

Until then, existing direct-session durability and historical reads remain the
source evidence behind this temporary projection. A checkpoint is session-state
continuity evidence and is never presented as workspace capture/restore proof.

### 14.2 Terminal result

The bridge returns a normal MoonMind `AgentRunResult`:

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
    "omnigentSessionId": "sess_...",
    "omnigentAgentId": "ag_...",
    "captureManifestRef": "art_capture_manifest",
    "externalStateRef": "art_external_state"
  }
}
```

Provider-native refs such as `omnigent://...` must not appear in top-level `outputRefs`.

---

## 15. Workflow Chat integration

Workflow Chat should resolve session evidence in this order:

1. explicit `execution.agentRunId`;
2. step-scoped `agentRunId`;
3. bridge session by `workflow_id` and latest run;
4. bridge session by `idempotency_key`;
5. legacy managed-run observability;
6. legacy merged logs.

When bridge session events are present, Workflow Chat should render:

- sent messages;
- assistant deltas/output;
- tool/session item events;
- elicitation/approval requests;
- interruptions and stop events;
- changed-file/resource notices;
- completion/failure events;
- diagnostics and artifact links.

Terminal workflows should not show “managed runtime observability record missing” until the bridge session store and step-scoped agent-run bindings have also been checked.

The Workflow Chat projection reads bridge session events through these canonical
MoonMind API routes (owned by the Session API Facade in §18.2):

| Purpose | MoonMind route |
|---|---|
| List bridge session events | `GET /api/omnigent/bridge-sessions/{bridge_session_id}/events` |
| Stream bridge session events | `GET /api/omnigent/bridge-sessions/{bridge_session_id}/stream` |

These routes are the durable projection-API contract. The disposable
[`docs/tmp/OmnigentBridgeRollout.md`](../tmp/OmnigentBridgeRollout.md) only owns
the phase in which they are delivered, not the contract itself.

---

## 16. Security and authentication

Rules:

1. MoonMind authenticates and authorizes access to the workflow, AgentRun, and bridge session.
2. Omnigent endpoint credentials are activity-side or service-side secrets.
3. No Omnigent API token, host runner secret, bearer token, cookie, or raw credential may enter Temporal workflow payloads.
4. Session labels may include MoonMind ids and idempotency keys, not secrets.
5. Raw provider events must be redacted before artifact persistence when they contain secret-like fields.
6. MoonMind artifact refs remain the evidence boundary.
7. Proxy mode must not leak MoonMind internal auth headers to upstream Omnigent Server unless explicitly configured.
8. Embedded mode must pass upstream host auth conformance tests before being enabled in production.

Auth topology:

```text
MoonMind user/operator -> MoonMind API authorization
MoonMind Bridge -> Omnigent Server/API credentials
Omnigent Host -> Omnigent host/runner auth profile
```

In proxy mode, the host still authenticates to stock Omnigent Server. In embedded mode, MoonMind must implement the host-facing auth expected by the unchanged host.

---

## 17. Error classification

| Failure | MoonMind failure class | Notes |
|---|---|---|
| Upstream Omnigent Server unreachable before session create | `integration_error` | Retryable according to transport policy. |
| Host cannot register/connect | `integration_error` or `system_error` | Preserve redacted host/server diagnostics. |
| Authentication failure to Omnigent API | `integration_error` | Non-retryable until config fixed. |
| Invalid session create payload | `user_error` | Example: managed `hostId` or external repo URL workspace. |
| First-message digest mismatch | `user_error` | Conflicting replay under same idempotency key. |
| Ambiguous `posting` reconciliation | `integration_error` | Fail closed instead of duplicate post. |
| Stream disconnect, terminal snapshot says active | `integration_error` | Retry/reconcile depending on policy. |
| Runtime/harness failure | `execution_error` | The host ran the task and failed. |
| Session/host timeout | `system_error` | Terminal `timed_out` status, kept distinct from `failed`, matching `moonmind/omnigent/execute.py` normalization. |
| Optional resource harvest failed | completed with diagnostics | Unless policy requires full evidence. |
| Required artifact persistence failed | `system_error` | MoonMind artifact authority failed. |

---

## 18. Target module boundaries

This section states the durable desired-state module ownership for the bridge.
It is intentionally declarative: it defines where each responsibility lives and
which existing patterns it supersedes, not an ordered delivery checklist. The
disposable phase-by-phase rollout sequence lives in
[`docs/tmp/OmnigentBridgeRollout.md`](../tmp/OmnigentBridgeRollout.md).

### 18.1 Canonical package placement

All bridge code lives in the existing `moonmind/omnigent/` package alongside the
canonical `moonmind/omnigent/bridge_store.py` and `moonmind/omnigent/execute.py`.
`bridge_store.py` supersedes the removed `moonmind/omnigent/store.py`. There is
no separate `moonmind/omnigent_bridge/` package; a parallel package would
duplicate the active Omnigent code and split ownership.

### 18.2 Component-to-module ownership

| Bridge component (§5) | Owning module | Notes |
|---|---|---|
| Bridge configuration | `moonmind/omnigent/bridge_config.py` | Parses and validates the §6 declarative config. |
| Bridge Session Store | `moonmind/omnigent/bridge_store.py` | Canonical `omnigent_bridge_sessions` store (§18.3). |
| Bridge schemas | `moonmind/schemas/omnigent_bridge_models.py` | Session/event/config models. |
| Durable ORM/migration | `api_service/db/models.py` (`OmnigentBridgeSession` / `OmnigentBridgeSessionEvent`) + `api_service/migrations/versions/*` | Tables in §7. |
| Session API Facade / Host Protocol Facade | `api_service/api/routers/omnigent_bridge.py`, `moonmind/omnigent/bridge_proxy.py` | Proxy and embedded surfaces. |
| Event Normalizer | `moonmind/omnigent/bridge_events.py` | Normalizes host/provider events (§10). |
| Artifact Publisher / Resource Harvester | `moonmind/omnigent/bridge_artifacts.py` | Publishes evidence as MoonMind artifact refs (§13). |
| Workflow Chat projection | API + UI bridge-session event surfaces | Prefers bridge events over legacy logs (§15). |

### 18.3 Superseded patterns

`bridge_store.py` and `omnigent_bridge_sessions` supersede the existing
`omnigent_external_runs` mapping owned by `OmnigentRunStore` in
`moonmind/omnigent/store.py`. Per the repository Compatibility Policy, that
mapping is migrated into `omnigent_bridge_sessions` and the superseded store is
removed in the same cohesive change — no alias, wrapper, or parallel table.

The direct Codex managed-session producer is a temporary compatibility path
(§3.3): it emits bridge-shaped events during migration and is retired once Codex
execution runs behind the bridge. Embedded compatibility mode (§3.2) is a valid
target module surface, but it is only enabled after proxy mode has conformance
and live-smoke evidence, as stated in its own design principles (§2.4).

---

## 19. Acceptance criteria

A successful bridge implementation must satisfy:

1. A stock Omnigent host can participate without a custom build.
2. MoonMind can create or attach an Omnigent-shaped session.
3. MoonMind can post a message event with first-message idempotency.
4. MoonMind can stream host/session events.
5. Workflow Chat renders bridge session events.
6. MoonMind copies snapshots/resources into artifacts.
7. First-message retries do not duplicate the prompt.
8. Failed launch paths still create visible diagnostics and a Chat timeline.
9. Provider-native resource refs do not replace MoonMind artifact refs.
10. Proxy mode and embedded mode expose the same MoonMind-facing session/event model.

---

## 20. Testing strategy

### 20.1 Unit tests

- bridge config validation;
- proxy-mode route mapping;
- managed/external host session validation;
- first-message digest and marker generation;
- `not_prepared -> prepared -> posting -> posted -> terminal` transitions;
- digest mismatch fail-fast behavior;
- ambiguous `posting` reconciliation fail-closed behavior;
- event normalization;
- redaction rules;
- artifact ref validation;
- Workflow Chat resolution priority.

### 20.2 Fake Omnigent server tests

The fake server should support:

- `GET /api/agents`;
- `POST /v1/sessions`;
- `GET /v1/sessions/{id}`;
- `POST /v1/sessions/{id}/events`;
- `GET /v1/sessions/{id}/stream`;
- changed-file/session-file resource endpoints.

Scenarios:

1. successful session with streamed assistant output;
2. failed session with diagnostics;
3. stream disconnect and snapshot reconciliation;
4. retry after session create before first message;
5. retry after `posting` state;
6. digest mismatch under same idempotency key;
7. optional diff unavailable;
8. child-session event capture;
9. cancellation via `interrupt` and `stop_session`.

### 20.3 Stock host smoke tests

Run against a real, unchanged Omnigent host:

- host registration;
- heartbeat/capability visibility;
- managed session creation;
- Codex or Claude harness launch;
- message posting;
- stream capture;
- final snapshot capture;
- changed-file/session-resource harvest;
- no duplicate first message across retry.

---

## 21. Open questions

1. Should proxy mode mount under `/api/omnigent/*`, a dedicated bridge hostname, or both?
2. Which upstream Omnigent host auth modes should MoonMind support in embedded mode?
3. Should direct Codex compatibility emit only normalized bridge events, or both normalized and raw Codex rollout events?
4. What is the minimum host/runner conformance suite required before embedded mode can be enabled?
5. Should clear/reset be represented as a bridge-local policy or an Omnigent extension event?

---

## 22. Non-goals

This design does not attempt to:

- fork or custom-build the Omnigent host;
- make Omnigent session ids become MoonMind workflow ids;
- expose raw Omnigent session states as MoonMind workflow states;
- make Omnigent resources authoritative over MoonMind artifacts;
- require host-side stdout/stderr unless the stock host/server exposes them;
- blindly repost first messages on retry;
- require embedded host-protocol mode before proxy mode proves the contract;
- replace all direct Codex managed-session code in the first bridge slice.
