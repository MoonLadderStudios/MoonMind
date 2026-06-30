# Omnigent Adapter Design

Status: Proposed design
Owners: MoonMind Platform
Last updated: 2026-06-30

**Implementation tracking:** rollout notes, spikes, and temporary handoffs should live under `docs/tmp/` or gitignored local-only artifacts, not as mutable checklists in this canonical design document.

## Related docs

- `docs/ExternalAgents/AddingExternalProvider.md`
- `docs/ExternalAgents/OpenClawAgentAdapter.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/ErrorTaxonomy.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/MoonMindArchitecture.md`
- Omnigent upstream API reference: `omnigent/server/API.md` in `omnigent-ai/omnigent`
- Omnigent upstream session schema: `omnigent/server/schemas.py` in `omnigent-ai/omnigent`

---

## 1. Purpose

This document defines the architecture for integrating **Omnigent** as an execution substrate inside MoonMind.

Omnigent is a meta-harness and session server over coding and agent harnesses such as Claude Code, Codex, Cursor, OpenCode, Hermes, Pi, and custom YAML agents. Its primary runtime unit is a **session/conversation** with live streams, resources, files, host/runner binding, and optional managed sandboxes.

In the target topology, MoonMind does not launch Claude Code, Codex CLI, or other Omnigent-supported harnesses directly. Instead:

```text
MoonMind Temporal Workflow / AgentRun
  -> MoonMind Omnigent adapter
      -> Omnigent server API
          -> Omnigent session
              -> Omnigent host / sandbox / runner
                  -> Claude Code, Codex, Polly, or another Omnigent harness/agent
```

The adapter must preserve MoonMind's core architecture:

- Temporal owns durable orchestration.
- MoonMind artifacts own durable evidence.
- Omnigent owns live session/runtime execution.
- Provider-specific details stay at the adapter/activity boundary.
- Workflow code receives only canonical MoonMind runtime contracts.

This document is intentionally written with two goals in mind:

1. **Minimal goal:** support a robust v1 `OmnigentAdapter` without destabilizing MoonMind's Temporal execution model.
2. **Stretch goal:** keep future decisions compatible with a less painful merger or deeper integration between Omnigent Server and the MoonMind API.

---

## 2. Architectural decision summary

### 2.1 Treat Omnigent as an external provider in v1

The v1 adapter uses the **external-agent adapter pattern**, not the managed-runtime adapter pattern.

Reasoning:

- MoonMind is not directly launching the agent process.
- MoonMind is not directly materializing Claude/Codex credentials into a runtime container.
- MoonMind is delegating a run to an Omnigent server, which then provisions or uses an Omnigent host/runner.
- Omnigent's primary unit is a long-running session/conversation, not a MoonMind-managed runtime process.

Therefore the v1 provider registers as a single canonical provider identity:

```text
agentKind = external
agentId   = omnigent
```

Runtime selection, session mode, harness choice, Claude-vs-Codex choice, built-in Omnigent agent selection, model overrides, reasoning effort, and capture policy live under `parameters.omnigent`, not by changing the top-level MoonMind `agentId`.

Do **not** register provider-specific top-level aliases such as:

```text
omnigent_session
omnigent_claude
omnigent_codex
omnigent_polly
```

Those values would split routing, policy, metrics, tests, and provider identity.

### 2.2 Use streaming-gateway execution first

The v1 activity shape is:

```text
integration.omnigent.execute
```

This mirrors the existing streaming-gateway external-provider pattern. The activity performs the entire Omnigent session execution and returns a terminal canonical `AgentRunResult`, or raises an activity error. It does **not** return non-terminal provider states in v1.

The polling/session lifecycle may be added later as v2:

```text
integration.omnigent.start
integration.omnigent.status
integration.omnigent.fetch_result
integration.omnigent.cancel
integration.omnigent.send_message
integration.omnigent.harvest_session
```

### 2.3 MoonMind remains the artifact authority

Omnigent's files panel, session resources, snapshots, and stream surfaces are observable provider resources, not the authoritative MoonMind artifact system.

The adapter must copy observable Omnigent inputs, outputs, streams, snapshots, changed-file manifests, current file contents, optional diff content, session file resources, and diagnostics into MoonMind artifacts and return compact refs in `AgentRunResult`.

Omnigent resource URLs, session ids, file ids, and runner ids may appear in diagnostics metadata and provider-specific mapping tables, but they must not replace MoonMind `ArtifactRef`s in workflow evidence.

### 2.4 Durability boundary in v1

The streaming-gateway model runs the entire Omnigent session inside one `integration.omnigent.execute` activity. Temporal's durability is at the activity boundary, so v1 does **not** provide Temporal-checkpointed progress within a single Omnigent turn.

The honest v1 guarantee is:

- **Omnigent owns live-execution durability.** The Omnigent session and its runner-side workspace survive MoonMind worker death when the Omnigent server/host keeps them alive.
- **Temporal durably records delegation and result.** Temporal remembers that the run was delegated to Omnigent and records the final canonical result.
- **Retry re-attaches, it does not recreate.** A retry reconnects to the existing Omnigent session rather than provisioning a second host or reposting the first message.

Out of scope for v1:

- durable Temporal checkpointing of intra-session Omnigent progress;
- status-bearing streaming results consumed by `MoonMind.AgentRun`;
- long-lived Omnigent session reuse across multiple MoonMind steps;
- making Omnigent a second MoonMind workflow engine;
- merging MoonMind API and Omnigent Server persistence.

### 2.5 Future merger posture

A future MoonMind API / Omnigent Server merger should be prepared through **interface alignment**, not premature state-model fusion.

Convergence-friendly boundaries:

```text
MoonMind Workflow / AgentRun / ArtifactRef
  <-> Omnigent Session / Agent / Stream / Resources
```

Avoid designs that assume either system must subsume the other's state model. In particular:

- Do not make Omnigent session ids be MoonMind workflow ids.
- Do not force Omnigent sessions into MoonMind's Temporal lifecycle states.
- Do not expose raw Omnigent session statuses as MoonMind workflow states.
- Do not store Omnigent runtime credentials in MoonMind workflow payloads.
- Do not make Omnigent's artifact store authoritative for MoonMind evidence.
- Do not add Omnigent-specific Temporal Search Attributes unless a generic cross-provider dimension truly needs indexing.

Prefer stable cross-references:

```text
MoonMind workflow id
MoonMind agent run id
MoonMind step execution id
Omnigent endpoint ref
Omnigent session id
Omnigent agent id/name
MoonMind artifact refs for copied evidence
```

---

## 3. Conceptual mapping

| MoonMind concept | Omnigent analogue | Adapter rule |
|---|---|---|
| Workflow Execution | No direct equivalent | MoonMind remains the orchestration envelope. |
| `MoonMind.AgentRun` | Omnigent session execution | One AgentRun may own one Omnigent session in v1. |
| Workflow step | Session message/turn or whole session | v1 maps one step to one execute activity. |
| Managed runtime profile | Omnigent endpoint/agent/session target | Use endpoint/agent selectors, not raw credentials. |
| ArtifactRef | Omnigent snapshot/resource/file data copied into MoonMind | Never return Omnigent raw resources as top-level result. |
| Cancel workflow/step | `interrupt`, then `stop_session` | Do not delete before harvesting artifacts. |
| Result | Final snapshot + transcript + resource harvest | Return `AgentRunResult` with artifact refs. |
| Intervention/approval | Omnigent elicitation resolution | Normalize to MoonMind intervention semantics; do not leak raw provider state. |
| Session stream | Live-tail SSE | Mirror into MoonMind artifacts; do not rely on stream replay. |

---

## 4. Provider identity and registration

### 4.1 Runtime gate

Add a small runtime gate:

```text
moonmind/omnigent/settings.py
```

Required configuration:

```text
OMNIGENT_ENABLED=1
OMNIGENT_SERVER_URL=https://omnigent.example.com
```

Optional configuration:

```text
OMNIGENT_API_TOKEN=...
OMNIGENT_DEFAULT_AGENT_NAME=codex-native-ui
OMNIGENT_DEFAULT_HOST_TYPE=managed
OMNIGENT_DEFAULT_CAPTURE_POLICY=full
OMNIGENT_REQUEST_TIMEOUT_SECONDS=30
OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS=120
```

Security rule: `OMNIGENT_API_TOKEN` is resolved only at the activity boundary. It must not appear in Temporal workflow payloads, request metadata, artifacts, logs, or diagnostics.

### 4.2 External registry

Register the adapter only when the runtime gate is enabled.

Canonical registration:

```text
omnigent
```

The top-level `AgentExecutionRequest` remains:

```json
{
  "agentKind": "external",
  "agentId": "omnigent"
}
```

### 4.3 Base class

The provider adapter should extend `BaseExternalAgentAdapter`.

The class exists primarily for registry/capability declaration in v1. The actual execution happens in `integration.omnigent.execute`, following the streaming-gateway model.

```text
moonmind/workflows/adapters/omnigent_agent_adapter.py
```

Declarative capability:

```python
ProviderCapabilityDescriptor(
    providerName="omnigent",
    supportsCallbacks=False,
    supportsCancel=False,
    supportsResultFetch=False,
    defaultPollHintSeconds=15,
    executionStyle="streaming_gateway",
)
```

The polling hooks should fail loudly in v1:

```text
do_start        -> unused, raise RuntimeError
do_status       -> unused, raise RuntimeError
do_fetch_result -> unused, raise RuntimeError
do_cancel       -> unused, raise RuntimeError
```

Cancellation for v1 is handled by cancellation of the execute activity, which then sends Omnigent `interrupt` / `stop_session` best-effort signals.

### 4.4 Why not `ManagedAgentAdapter`

`ManagedAgentAdapter` is reserved for MoonMind-owned managed runtimes where MoonMind resolves provider profiles, obtains profile leases, shapes credentials/env/files, and launches runtime activities.

The Omnigent topology delegates those responsibilities to Omnigent server and `omnigent-host`. Treating Omnigent as a managed runtime would create a misleading ownership boundary unless MoonMind directly provisions and controls the Omnigent host container itself.

A future v2 may add an `OmnigentManagedBridgeAdapter` only if MoonMind directly provisions Omnigent hosts and controls their lifecycle as MoonMind managed workloads.

Host packaging — whether `omnigent-host` runs containers via docker-in-docker, and whether MoonMind co-locates the Omnigent server/host in its own compose — is a compose/host-image concern tracked outside this adapter contract.

---

## 5. Declarative request contract

### 5.1 Top-level request

The adapter consumes the existing canonical `AgentExecutionRequest`.

Required fields:

```json
{
  "agentKind": "external",
  "agentId": "omnigent",
  "correlationId": "workflow:run:step",
  "idempotencyKey": "workflow:step:attempt",
  "parameters": {
    "title": "Implement auth fix",
    "description": "Fix the login redirect bug and open a PR.",
    "omnigent": {}
  }
}
```

### 5.2 Omnigent target block

All Omnigent-specific execution selection lives under `parameters.omnigent`.

```json
{
  "parameters": {
    "omnigent": {
      "endpointRef": "default",
      "agent": {
        "agentId": null,
        "agentName": "codex-native-ui",
        "bundleRef": null,
        "harnessOverride": null
      },
      "session": {
        "hostType": "managed",
        "hostId": null,
        "workspace": "https://github.com/org/repo#main",
        "title": "Implement auth fix",
        "labels": {},
        "modelOverride": null,
        "reasoningEffort": "high",
        "terminalLaunchArgs": [],
        "collaborationMode": null
      },
      "workspaceContext": {
        "includeInPrompt": true
      },
      "prompt": {
        "mode": "message",
        "text": null,
        "instructionRef": null,
        "includeInputRefs": true,
        "includeIdempotencyMarker": true
      },
      "capture": {
        "stream": true,
        "snapshots": true,
        "changedFiles": true,
        "workspaceFiles": true,
        "workspaceDiffs": "capability_probe",
        "sessionFiles": true,
        "githubPr": true,
        "patchSource": "omnigent_diff_or_github_pr_or_host_helper",
        "deleteOmnigentSessionAfterHarvest": false
      }
    }
  }
}
```

### 5.3 Field meanings

| Field | Meaning | Notes |
|---|---|---|
| `endpointRef` | Named Omnigent server endpoint | Resolves from activity-side config. No raw token. |
| `agent.agentId` | Existing Omnigent `ag_*` id | Preferred when known. |
| `agent.agentName` | Omnigent agent name to resolve through `/api/agents` | Useful for built-ins. |
| `agent.bundleRef` | MoonMind artifact ref for an Omnigent agent bundle | Uploaded via `/api/agents` if no `agentId`. |
| `agent.harnessOverride` | Omnigent session harness override | Optional; only for compatible Omnigent agents. |
| `session.hostType` | `managed` or `external` | `managed` means Omnigent provisions host/sandbox. |
| `session.hostId` | Omnigent external host id | Must be absent/null for `managed`; required for external-host launch flows that need a host-bound runner. |
| `session.workspace` | Omnigent workspace selector | For managed sessions, optional git repository URL with optional `#branch`; for external hosts, absolute path on that host. |
| `workspaceContext` | Additional prompt/artifact metadata | May mirror repository context, but it is not a checkout mechanism. |
| `session.terminalLaunchArgs` | Native Claude/Codex launch flags | Must be bounded and non-secret. |
| `prompt.text` | Inline prompt | Prefer artifact-backed prompt for large inputs. |
| `prompt.instructionRef` | MoonMind artifact ref with prompt/instructions | Activity reads artifact and posts text. |
| `prompt.includeIdempotencyMarker` | Whether to include a compact non-secret retry marker in the first message | Defaults to true for retry reconciliation. |
| `capture.patchSource` | Where patch artifacts may come from | Capability-probed Omnigent diff, GitHub PR harvesting, or host helper. |
| `capture.*` | Artifact capture policy | Controls MoonMind harvesting, not Omnigent storage. |

### 5.4 Managed vs external session validation

The adapter must validate the session target before calling Omnigent.

Rules:

- For `hostType="managed"`, omit `hostId`.
- For `hostType="managed"`, `session.workspace` may be omitted or set to a git repository URL, optionally with `#<branch>`.
- For `hostType="managed"`, local/absolute host paths are invalid because the sandbox does not exist before Omnigent provisions it.
- For managed repository tasks, `workspaceSpec.repository`, `workspaceContext.repository`, or equivalent workflow repository input must be normalized into `session.workspace` before session creation. Keeping it only as prompt/artifact context creates an empty sandbox and is not sufficient for code-editing tasks.
- If `hostType="managed"` and no repository URL is available, Omnigent creates an empty server workspace. The adapter should allow that only for non-repository tasks or when the request explicitly opts into an empty workspace.
- For `hostType="external"`, `hostId` and `workspace` are allowed, and `workspace` must be an absolute path on that external Omnigent host.
- For `hostType="external"`, repository URLs are invalid; repository URLs belong to managed session creation.

### 5.5 Upstream compatibility note: managed `workspace`

As of the 2026-06-30 upstream review, current Omnigent schema behavior allows `host_type="managed"` with a repository-URL `workspace`, and rejects `host_id` for managed sessions. Some upstream prose may still describe an older managed-session rule where `workspace` must not be set. MoonMind's adapter should follow the current schema/implementation contract:

```text
managed + workspace=https://github.com/org/repo#branch  -> valid
managed + workspace=/local/path                         -> invalid
managed + host_id=host_...                              -> invalid
external + workspace=/absolute/path                     -> valid
external + workspace=https://github.com/org/repo        -> invalid
```

If upstream changes again, update this document before changing adapter behavior.

### 5.6 Target resolution order

The activity resolves the Omnigent agent target in this order:

1. Use `agent.agentId` if present.
2. Resolve `agent.agentName` with `GET /api/agents`.
3. If `agent.bundleRef` is present, read the MoonMind artifact and upload to `POST /api/agents`.
4. Fall back to `OMNIGENT_DEFAULT_AGENT_NAME`.
5. Fail with `integration_error` if no target can be resolved.

---

## 6. Omnigent client

Create a thin transport client:

```text
moonmind/workflows/adapters/omnigent_client.py
```

The client must not know about Temporal workflows. It only knows Omnigent HTTP/SSE transport.

Required operations:

| Method | Omnigent API |
|---|---|
| `list_agents` | `GET /api/agents` |
| `get_agent` | `GET /api/agents/{id}` |
| `create_agent_bundle` | `POST /api/agents` multipart |
| `create_session` | `POST /v1/sessions` |
| `get_session` | `GET /v1/sessions/{session_id}` |
| `post_event` | `POST /v1/sessions/{session_id}/events` |
| `stream_events` | `GET /v1/sessions/{session_id}/stream` |
| `resolve_elicitation` | `POST /v1/sessions/{id}/elicitations/{eid}/resolve` |
| `list_changed_files` | `GET /v1/sessions/{id}/resources/environments/default/changes` |
| `list_workspace_files` | `GET /v1/sessions/{id}/resources/environments/default/filesystem` |
| `get_workspace_file` | `GET /v1/sessions/{id}/resources/environments/default/filesystem/{path}` |
| `get_workspace_diff` | Optional/capability-probed `GET /v1/sessions/{id}/resources/environments/default/diff/{path}` |
| `list_session_files` | `GET /v1/sessions/{id}/resources/files` |
| `get_session_file_content` | `GET /v1/sessions/{id}/resources/files/{file_id}/content` |
| `interrupt` | `POST /v1/sessions/{id}/events` with `type=interrupt` |
| `stop_session` | `POST /v1/sessions/{id}/events` with `type=stop_session` |
| `delete_session` | `DELETE /v1/sessions/{id}` |

`get_workspace_diff` is **optional** in v1. The adapter should capability-probe it and degrade if it is unavailable. It must not be the only path to produce a patch artifact.

Transport rules:

- Use activity-side authentication only.
- Redact request/response logs before artifact persistence.
- Treat non-2xx responses as structured integration errors.
- Preserve Omnigent status codes and error bodies in diagnostics artifacts after redaction.
- Avoid tight read timeouts for SSE streams.
- Surface malformed SSE frames as contract errors unless clearly transient before execution started.

---

## 7. Execute activity lifecycle

### 7.1 Activity name

```text
integration.omnigent.execute
```

Input:

```text
AgentExecutionRequest
```

Output:

```text
AgentRunResult
```

### 7.2 Lifecycle flow

```text
1. Validate AgentExecutionRequest.
2. Resolve Omnigent endpoint and agent target.
3. Create or reuse idempotent Omnigent session mapping.
4. Build canonical first-message payload and digest.
5. Determine whether the first message was already posted or can be reconciled.
6. Open SSE stream.
7. Fetch initial snapshot.
8. Post first message event only if durable state proves it has not already been posted.
9. Persist first-message sent marker immediately after successful POST.
10. Mirror SSE stream into MoonMind artifacts.
11. Wait for terminal response/session status.
12. Fetch final snapshot.
13. Harvest workspace/session resources.
14. Optionally discover GitHub PR metadata.
15. Return canonical terminal AgentRunResult, or raise activity error on unrecoverable adapter ambiguity.
```

### 7.3 Session creation

The activity creates an Omnigent session using JSON session creation.

Representative managed repository-session payload:

```json
{
  "agent_id": "ag_abc123",
  "title": "Implement auth fix",
  "labels": {
    "moonmind.workflow_id": "wf_...",
    "moonmind.agent_run_id": "ar_...",
    "moonmind.correlation_id": "...",
    "moonmind.idempotency_key": "..."
  },
  "host_type": "managed",
  "workspace": "https://github.com/org/repo#main",
  "model_override": null,
  "reasoning_effort": "high",
  "terminal_launch_args": []
}
```

Representative external-host payload:

```json
{
  "agent_id": "ag_abc123",
  "title": "Implement auth fix",
  "labels": {
    "moonmind.workflow_id": "wf_...",
    "moonmind.agent_run_id": "ar_...",
    "moonmind.correlation_id": "...",
    "moonmind.idempotency_key": "..."
  },
  "host_type": "external",
  "host_id": "host_abc123",
  "workspace": "/workspace/repo",
  "model_override": null,
  "reasoning_effort": "high",
  "terminal_launch_args": []
}
```

### 7.4 First message construction

The message text is assembled from:

1. `parameters.omnigent.prompt.text`, if present.
2. `parameters.omnigent.prompt.instructionRef`, if present.
3. `AgentExecutionRequest.instructionRef`, if present.
4. `parameters.description`, if present.
5. A generated prompt from title, workspace spec, workspace context, and input refs.

Large prompt bodies should be artifact-backed and read at the activity boundary.

Before sending the first message, the activity computes:

```text
first_message_digest = sha256(canonical_json(first_message_event.data))
```

When `prompt.includeIdempotencyMarker` is true, the activity appends or includes a compact non-secret marker in the first message body:

```text
MoonMind-Omnigent-Run:
  correlation_id: <correlationId>
  idempotency_key: <idempotencyKey>
  first_message_digest: <sha256>
```

The marker is intentionally non-secret and exists to support retry reconciliation against Omnigent snapshots, pending inputs, and transcripts if a Temporal activity retry occurs after the first POST reached Omnigent.

---

## 8. Idempotency and run mapping

The adapter must persist an idempotency mapping outside the Activity attempt.

Suggested table or durable store:

```text
omnigent_external_runs
  idempotency_key text primary key
  moonmind_workflow_id text not null
  moonmind_agent_run_id text not null
  correlation_id text not null
  omnigent_endpoint_ref text not null
  omnigent_session_id text null
  omnigent_agent_id text null
  omnigent_agent_name text null
  status text not null
  first_message_state text not null default 'not_prepared'
  first_message_digest text null
  first_message_marker text null
  first_message_post_attempted_at timestamptz null
  first_message_posted_at timestamptz null
  first_message_pending_id text null
  first_message_item_id text null
  first_message_request_ref text null
  first_message_response_ref text null
  created_at timestamptz not null
  updated_at timestamptz not null
  final_snapshot_ref text null
  sse_events_ref text null
  diagnostics_ref text null
  result_ref text null
```

Required first-message states:

```text
not_prepared -> prepared -> posting -> posted -> terminal
```

Rules:

1. If a record exists for `idempotencyKey`, reuse its Omnigent session.
2. If session creation succeeds, persist the `session_id` before preparing or posting the first message.
3. `prepared` records the canonical message digest before any HTTP POST.
4. `posting` is persisted immediately before the `POST /events` call.
5. `posted` is persisted immediately after Omnigent returns a successful response.
6. If Omnigent returns a native-terminal `pending_id`, store it.
7. If a retry finds `posted`, it must skip the first POST.
8. If a retry finds `posting`, it must reconcile before deciding whether to repost.
9. Reconciliation checks Omnigent snapshot `items`, native `pending_inputs`, and any captured `session.input.consumed` events for the stored digest or idempotency marker.
10. If reconciliation finds the first message, mark it `posted` and skip posting.
11. If reconciliation cannot prove absence, do not blindly repost. In v1, fail closed by raising a retryable activity error while the reconciliation grace period remains, then a non-retryable `integration_error` once policy is exhausted.
12. If the same `idempotencyKey` is reused with a different first-message digest, fail fast and require an explicit operator reset.

Do not introduce a generic `externalSession` table in v1. Omnigent has session-specific concerns — first-message state, pending ids, SSE artifact refs, session resource harvest state, and possible child sessions — that do not yet generalize cleanly across Jules, Codex Cloud, and OpenClaw. Promotion to a shared table becomes appropriate only when at least one additional provider needs durable reattach/session mapping with comparable fields.

---

## 9. State normalization

Map Omnigent observations into canonical MoonMind states.

| Omnigent observation | MoonMind status |
|---|---|
| Session created, host still launching | `launching` |
| Runner unavailable during managed provisioning | `launching` |
| `session.status = running` | `running` |
| `session.status = waiting` and active elicitation exists | `awaiting_approval` |
| `session.status = waiting` without known elicitation | `intervention_requested` |
| `response.elicitation_request` | `awaiting_approval` |
| `response.completed` and session returns idle | `completed` |
| `response.failed` | `failed` |
| `session.status = failed` | `failed` |
| Activity timeout | `timed_out` |
| Activity cancellation after interrupt/stop | `canceled` |

These states may be used internally by the execute activity, diagnostics, and future polling/session activities. In the v1 streaming-gateway path, the execute activity still returns only a terminal `AgentRunResult` or raises an activity error.

Unsupported or unknown provider states must be treated as adapter contract errors. Do not pass raw Omnigent statuses into workflow code as canonical states.

---

## 10. Stream and snapshot capture

### 10.1 Capture principle

Omnigent's stream is a live tail. It is not a replay log. Therefore the execute activity must open the stream before posting the first message whenever possible.

On retry, opening the stream before reconciliation is still preferred, but the first-message idempotency rules take precedence. A retry must never post the first message merely because it has successfully reattached to the stream.

### 10.2 Required artifacts

Minimum stream/snapshot artifacts:

```text
input.omnigent.session_create.request.json
input.omnigent.session_create.response.json
input.omnigent.first_message.request.json
input.omnigent.first_message.response.json
runtime.omnigent.snapshot.initial.json
runtime.omnigent.sse.raw.jsonl
runtime.omnigent.sse.normalized.jsonl
output.omnigent.snapshot.final.json
output.omnigent.transcript.jsonl
output.omnigent.final_response.md
```

### 10.3 Normalized SSE event shape

```json
{
  "schemaVersion": "v1",
  "capturedAt": "2026-06-27T00:00:00Z",
  "provider": "omnigent",
  "omnigentSessionId": "conv_abc123",
  "eventType": "response.output_text.delta",
  "itemId": null,
  "responseId": null,
  "payload": {},
  "redaction": {
    "applied": true,
    "rules": []
  }
}
```

### 10.4 Snapshot and reconnect rule

Fetch snapshots at least twice:

1. immediately after stream attachment;
2. after terminal state or cancellation.

Optional periodic snapshots may be captured for long runs.

Omnigent reconnect semantics are:

```text
open stream first
fetch snapshot second
dedupe items between snapshot and stream by stable item id
```

MoonMind should persist its own mirrored stream/snapshot artifacts; it must not depend on Omnigent SSE replay for correctness.

### 10.5 Heartbeating, worker death, and reattach

Because the whole session runs inside one activity, MoonMind worker death is detected through Temporal activity heartbeat.

- Heartbeat on each captured SSE frame, or at least each snapshot / periodic interval.
- Heartbeat payloads must be compact progress tokens, not raw provider payloads.
- `heartbeat_timeout` must be much smaller than `start_to_close_timeout`.
- On retry, the activity looks up `omnigent_session_id`, reconnects the SSE stream to that session, fetches a fresh snapshot, applies first-message idempotency, and resumes waiting for terminal state.

---

## 11. Artifact harvesting

### 11.1 Principle

MoonMind must copy Omnigent-observable resources into MoonMind artifacts. Omnigent resource URLs, session ids, or file ids may be stored in metadata, but they are not substitutes for MoonMind `ArtifactRef`s.

### 11.2 Workspace files and patch sources

At terminal state, harvest the upstream resource surfaces when available:

```text
GET /v1/sessions/{id}/resources/environments/default/changes
GET /v1/sessions/{id}/resources/environments/default/filesystem
GET /v1/sessions/{id}/resources/environments/default/filesystem/{path}
```

Store:

```text
output.workspace.changed_files.index.json
output.workspace.files/<path>.current
output.workspace.manifest.json
```

If upstream exposes the capability, optionally harvest:

```text
GET /v1/sessions/{id}/resources/environments/default/diff/{path}
```

Store:

```text
output.workspace.diffs/<path>.before
output.workspace.diffs/<path>.after
output.workspace.patch_from_omnigent_diff.patch
```

`get_workspace_diff` must be capability-probed. If unavailable, patch artifacts come from one of these sources:

1. GitHub PR diff after PR creation;
2. host-side helper output, such as `git diff` captured inside the Omnigent host;
3. a future Omnigent diff capability discovered explicitly at runtime.

When none of those sources is available, store a diagnostic artifact instead of failing the whole run solely because patch capture could not produce a patch:

```text
output.workspace.patch_unavailable.json
```

### 11.3 Session file resources

If Omnigent session files exist, harvest:

```text
GET /v1/sessions/{id}/resources/files
GET /v1/sessions/{id}/resources/files/{file_id}/content
```

Store:

```text
output.omnigent.session_files.index.json
output.omnigent.session_files/<file_id>/<filename>
output.omnigent.session_files/<file_id>/metadata.json
```

### 11.4 GitHub and PR artifacts

If the run creates a PR or emits a PR URL, harvest:

```text
output.github.pr.metadata.json
output.github.pr.diff.patch
output.github.pr.checks.json
output.github.pr.comments.json
```

PR harvesting may be implemented by a follow-up GitHub post-processor activity. The Omnigent adapter should at minimum detect and persist PR URLs from transcript, final snapshot, and metadata.

### 11.5 Diagnostics artifact

Always produce a diagnostics artifact, even for successful runs.

Suggested contents:

```json
{
  "provider": "omnigent",
  "endpointRef": "default",
  "omnigentSessionId": "conv_abc123",
  "omnigentAgentId": "ag_abc123",
  "hostType": "managed",
  "terminalStatus": "completed",
  "errors": [],
  "transport": {
    "sseConnected": true,
    "sseEndedNormally": true,
    "eventsCaptured": 231
  },
  "idempotency": {
    "firstMessageState": "posted",
    "firstMessageDigest": "sha256:...",
    "firstMessagePendingId": "pending_..."
  },
  "capture": {
    "changedFiles": 4,
    "sessionFiles": 0,
    "childSessions": 0,
    "patchSource": "omnigent_diff_or_github_pr_or_host_helper",
    "patchProduced": false
  }
}
```

---

## 12. Child sessions

If Omnigent emits child-session creation events, the adapter must record them and may recursively capture their snapshots/resources.

Minimum v1 behavior:

- store child session ids in `runtime.omnigent.child_sessions.jsonl`;
- fetch final snapshot for each child session if authorized and available;
- include child refs in diagnostics metadata.

Future behavior:

- model each child session as a nested external run capture;
- create explicit artifact links from parent AgentRun to child Omnigent sessions;
- expose child session progress in the MoonMind observability UI.

---

## 13. Cancellation and cleanup

### 13.1 Soft cancel

On Temporal activity cancellation or MoonMind AgentRun cancellation:

```json
{
  "type": "interrupt",
  "data": {}
}
```

### 13.2 Hard stop

If the session remains active after a short grace period:

```json
{
  "type": "stop_session",
  "data": {}
}
```

### 13.3 Delete policy

Do not delete the Omnigent session before artifact harvest.

After harvest, deletion is governed by:

```json
{
  "deleteOmnigentSessionAfterHarvest": false
}
```

Default: preserve Omnigent session.

Optional cleanup may call:

```text
DELETE /v1/sessions/{session_id}?delete_branch=false
```

`delete_branch=true` should require explicit operator/workflow policy because it removes Omnigent-created worktrees and branches.

---

## 14. Security, auth, and secret handling

Rules:

1. Omnigent server URL may be stored as an endpoint ref in workflow payloads.
2. Omnigent API tokens must be activity-side secrets only.
3. MoonMind must redact auth headers, cookies, tokens, and secret-looking fields before artifact storage.
4. Use existing MoonMind redaction helpers, including `SecretRedactor` and `redact_sensitive_text`, rather than inventing a separate Omnigent-only redactor.
5. Omnigent session labels must not include secrets.
6. `parameters.omnigent` must not include raw credentials.
7. Runtime credentials for Claude/Codex remain Omnigent server/host configuration concerns in this topology.
8. Artifact capture must prefer redacted raw event records plus normalized views.
9. Host-side capture helpers, if added later, must authenticate to MoonMind artifact APIs using scoped, short-lived credentials.
10. First-message idempotency markers may include correlation ids and digests, but must not include raw prompts from private artifacts beyond the actual prompt body that is already being sent to Omnigent.

### 14.1 Auth topology warning

Omnigent has two auth surfaces:

```text
MoonMind adapter -> Omnigent HTTP/SSE API
Omnigent host/runner/sandbox -> Omnigent runner tunnel
```

The adapter can authenticate its own HTTP/SSE calls, but that does not automatically prove that Omnigent managed sandboxes can dial back to the Omnigent server. Upstream Omnigent deploys support auth modes such as accounts, OIDC, header-auth, and single-user/local auth. Managed sandbox runner dial-back may require Omnigent server auth configuration that can supply or accept runner identity, especially in deployed multi-user environments.

The adapter should therefore surface managed-host launch failures as `integration_error`/`system_error` diagnostics rather than treating them as MoonMind credential failures.

---

## 15. Error classification

| Failure | MoonMind failure class | Notes |
|---|---|---|
| Omnigent server unreachable before session create | `integration_error` | Retryable if transport policy allows. |
| Omnigent server returns `429` / rate limited | `integration_error` | Classify as rate-limited; honor `Retry-After` and use bounded retry. |
| Authentication failure to Omnigent API | `integration_error` | Non-retryable until config fixed. |
| Unknown Omnigent agent name | `user_error` | Bad request/target selection. |
| Invalid managed-session create fields | `user_error` | Example: managed `hostId`, managed local path workspace, or external repository URL workspace. |
| Managed repo task without managed repository URL | `user_error` | Repository edit tasks must provide a managed repo URL or explicitly opt into an empty workspace. |
| Session create 4xx | `user_error` or `integration_error` | Depends on validation vs auth/config. |
| Managed host provisioning failure | `system_error` or `integration_error` | Preserve Omnigent error body in diagnostics after redaction. |
| Runtime/harness not configured | `integration_error` | Omnigent host/provider config issue. |
| Agent returns failed response | `execution_error` | Runtime attempted work and failed. |
| Activity timeout | `system_error` or `execution_error` | Depends on timeout policy. |
| Artifact harvest partial failure | completed with diagnostics or `system_error` | Policy-controlled. |
| Unknown Omnigent stream event schema | `integration_error` | Contract drift. |
| Idempotency key reused with different first-message digest | `user_error` | Caller attempted conflicting replay. |
| Retry cannot prove whether first message was accepted | `integration_error` | V1 streaming execute must fail/throw or return a terminal result with this failure class; it must not return non-terminal `intervention_requested`. |

---

## 16. Canonical result contract

The execute activity returns a compact `AgentRunResult`.

Example:

```json
{
  "outputRefs": [
    "art_transcript",
    "art_final_snapshot",
    "art_workspace_manifest"
  ],
  "summary": "Implemented login redirect fix and opened PR ...",
  "diagnosticsRef": "art_diagnostics",
  "failureClass": null,
  "providerErrorCode": null,
  "metadata": {
    "providerName": "omnigent",
    "omnigentSessionId": "conv_abc123",
    "omnigentAgentId": "ag_abc123",
    "omnigentAgentName": "codex-native-ui",
    "hostType": "managed",
    "workspace": "https://github.com/org/repo#main",
    "githubPrUrl": "https://github.com/org/repo/pull/123",
    "captureManifestRef": "art_capture_manifest",
    "patchRef": "art_github_pr_diff"
  }
}
```

Provider-native payloads must not be returned as top-level fields. In the v1 streaming-gateway path, returned results are terminal. Non-terminal state reporting requires a future workflow contract change.

---

## 17. Observability surfaces

The adapter should produce enough data to power existing MoonMind AgentRun observability endpoints.

| MoonMind observability surface | Canonical `link_type` | Omnigent-backed source |
|---|---|---|
| Observability summary | `output.summary` | diagnostics + status timeline + final snapshot |
| Logs stream | `output.logs` | normalized SSE JSONL projection |
| stdout/stderr | `runtime.stdout` / `runtime.stderr` | not available API-only; use host helper in v2 |
| merged logs | `runtime.merged_logs` | raw SSE stream + transcript projection |
| diagnostics | `runtime.diagnostics` | diagnostics artifact |
| step evidence | `output.agent_result` | capture manifest + output refs |
| final summary | `output.primary` | final response markdown + result summary |

Host-side stdout/stderr capture is explicitly out of scope for v1 unless a MoonMind helper is added to the Omnigent host image.

---

## 18. Temporal Visibility and Search Attribute posture

Do not add Omnigent-specific Temporal Search Attributes in v1.

Do **not** add:

```text
mm_omnigent_session_id
mm_omnigent_agent_id
mm_omnigent_host_type
mm_omnigent_harness
mm_external_session_id
```

Prefer existing generic dimensions:

```text
mm_owner_id
mm_owner_type
mm_state
mm_entry
mm_repo
mm_integration
mm_target_runtime
mm_target_skill
mm_scheduled_for
```

Omnigent-specific values belong in:

```text
AgentRunResult.metadata
diagnostics artifacts
omnigent_external_runs mapping table
artifact links
step/run evidence
```

This keeps the Temporal custom Search Attribute budget provider-neutral and leaves a future merger path open without making Temporal Visibility an Omnigent session browser.

---

## 19. v1 adapter and registry sketch

```python
# moonmind/workflows/adapters/omnigent_agent_adapter.py

from moonmind.schemas.agent_runtime_models import ProviderCapabilityDescriptor
from moonmind.workflows.adapters.base_external_agent_adapter import BaseExternalAgentAdapter

_OMNIGENT_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="omnigent",
    supportsCallbacks=False,
    supportsCancel=False,
    supportsResultFetch=False,
    defaultPollHintSeconds=15,
    executionStyle="streaming_gateway",
)

class OmnigentExternalAdapter(BaseExternalAgentAdapter):
    """Registry/capability adapter for Omnigent-backed external agent runs."""

    def __init__(self) -> None:
        super().__init__(accepted_agent_ids=frozenset({"omnigent"}))

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _OMNIGENT_CAPABILITY

    async def do_start(self, request, title, description, metadata):
        raise RuntimeError("Omnigent v1 uses integration.omnigent.execute")

    async def do_status(self, run_id: str):
        raise RuntimeError("Omnigent v1 uses streaming execution")

    async def do_fetch_result(self, run_id: str):
        raise RuntimeError("Omnigent v1 uses streaming execution")

    async def do_cancel(self, run_id: str):
        raise RuntimeError("Omnigent v1 cancels via execute activity cancellation")
```

```python
# moonmind/workflows/adapters/external_adapter_registry.py

from moonmind.omnigent.settings import build_omnigent_gate

gate = build_omnigent_gate(env=env)
if gate.enabled:
    from moonmind.workflows.adapters.omnigent_agent_adapter import OmnigentExternalAdapter

    def _omnigent_factory() -> AgentAdapter:
        return OmnigentExternalAdapter()

    registry.register("omnigent", _omnigent_factory)
```

---

## 20. v2 polling/session mode

A future polling/session adapter can support multi-step continuation against one Omnigent session.

Activities:

```text
integration.omnigent.start
integration.omnigent.status
integration.omnigent.fetch_result
integration.omnigent.cancel
integration.omnigent.send_message
integration.omnigent.harvest_session
```

Use cases:

- one MoonMind workflow step creates a session;
- later steps send additional Omnigent messages;
- operator can pause/review between turns;
- Omnigent session continuity is intentionally preserved.

Additional requirements:

- durable stream mirror service or recurring stream capture activity;
- explicit `externalProviderContinuation` payload support;
- session epoch tracking for clear/reset semantics;
- stricter ownership and cleanup policies;
- child-session mapping in MoonMind run ledger;
- per-message idempotency records, not only first-message records;
- preserve the same workspace contract: managed sessions may use repository-URL `workspace`, while external sessions use host-path `workspace`;
- if streaming-gateway execution should surface non-terminal states, change `MoonMind.AgentRun` to consume a status-bearing streaming result instead of treating every returned result as completed.

---

## 21. Host-side capture helper

API-only capture is enough for v1. A host-side helper should be added only when MoonMind needs artifacts not exposed by Omnigent APIs.

Potential helper responsibilities:

```text
on session start:
  capture git HEAD, workspace root, runtime environment manifest

during run:
  tail native runtime logs/transcripts
  periodically capture git status/diff
  optionally sync selected files to MoonMind artifact API

on terminal state:
  create diagnostics tarball
  create workspace patch bundle
  upload native transcript bundle
```

This helper must write to MoonMind's artifact API or artifact worker surface, not to Omnigent's internal artifact store.

---

## 22. Testing strategy

### 22.1 Unit tests

- target block validation;
- managed-session rejection of caller-provided `hostId`;
- managed-session rejection of local/absolute path workspaces;
- managed repository URL preservation as Omnigent `workspace`;
- managed repo task failure when repository context is prompt-only and no managed `session.workspace` is supplied;
- external-session translation of `hostId` / host-path `workspace` to Omnigent `host_id` / `workspace`;
- endpoint config resolution;
- single canonical `agentId=omnigent` registration;
- rejection of provider-specific top-level aliases;
- Omnigent status normalization;
- SSE event parsing;
- redaction rules;
- artifact manifest generation;
- cancellation event sequence;
- idempotency mapping reuse;
- first-message digest computation;
- first-message skip-on-reuse behavior;
- `posting` state reconciliation behavior;
- ambiguous `posting` state fails/throws instead of returning non-terminal success;
- digest mismatch fail-fast behavior;
- optional `get_workspace_diff` capability probe and fallback.

### 22.2 Adapter contract tests

- provider registers only when enabled;
- `agentKind=external` is required;
- `agentId=omnigent` is accepted;
- unknown `agentId` is rejected;
- `agentId=omnigent_session` is rejected;
- streaming capability is declared;
- polling hooks fail loudly in v1.

### 22.3 Integration tests with fake Omnigent server

The fake server should support:

- `/api/agents` list/get;
- `/v1/sessions` create;
- `/v1/sessions/{id}` snapshot;
- `/v1/sessions/{id}/events`;
- `/v1/sessions/{id}/stream` SSE;
- resource endpoints for changes, filesystem content, optional diff content, and session files.

Scenarios:

1. successful run with text output;
2. failed run;
3. managed host launch delay;
4. managed session create includes repository URL `workspace` when repository work is requested;
5. managed session create rejects local path `workspace` and caller-provided `host_id`;
6. managed session create may omit `workspace` only for explicit empty-workspace tasks;
7. external session create may include `host_id` and absolute-path `workspace`;
8. stream disconnect and snapshot reconciliation;
9. elicitation request and approval;
10. changed files and current-file harvest;
11. optional diff harvest when supported;
12. patch unavailable diagnostic when no Omnigent diff/GitHub PR/host helper exists;
13. child session event capture;
14. cancellation before completion;
15. idempotent retry after session create but before first message;
16. idempotent retry after first message response but before terminal result;
17. crash-window retry with `posting` state and snapshot reconciliation;
18. ambiguous `posting` reconciliation fails closed rather than returning `intervention_requested` from execute;
19. conflicting first-message digest under the same idempotency key.

### 22.4 Live smoke tests

Run against a real Omnigent server with a disposable repository and a sandbox host. Validate:

- session creation;
- managed repository session creation includes `workspace` as a repository URL;
- managed path workspaces and `host_id` are rejected before or by Omnigent;
- host provisioning;
- Claude or Codex launch selected through `parameters.omnigent`;
- stream capture;
- final snapshot capture;
- changed-file harvest;
- optional diff harvest if available;
- optional PR detection;
- activity retry does not duplicate the first prompt.

---

## 23. Implementation sequencing

Stable implementation milestones:

1. Runtime gate, typed target models, fake Omnigent client, and adapter registration.
2. `omnigent_external_runs` mapping and first-message idempotency.
3. `integration.omnigent.execute` with stream/snapshot capture.
4. Resource harvesting for changed-file manifests, current file contents, optional diffs, and session files.
5. GitHub PR post-processing for durable patch artifacts when PRs are produced.
6. Optional polling/session reuse and host-side capture helper.

---

## 24. Open questions

1. Should Omnigent endpoint selection be global config only, or should MoonMind support named endpoint refs per tenant/project?
2. Should v1 require `hostType=managed`, or should external-host sessions be allowed immediately?
3. Should the adapter delete Omnigent sessions after successful harvest in CI-style workflows?
4. Should MoonMind patch Omnigent to emit webhooks or artifact callbacks instead of relying on SSE capture?
5. How should clear/context-reset semantics map onto MoonMind step epochs in v2?
6. Which Omnigent auth modes should be considered supported for server-managed sandboxes in MoonMind deployments?
7. Should optional Omnigent diff capture become required once upstream documents it as stable?

---

## 25. Non-goals

The v1 adapter does not attempt to:

- replace MoonMind's direct Claude Code or Codex managed runtimes;
- expose Omnigent as a second workflow engine;
- store raw Omnigent server credentials in workflow payloads;
- make Omnigent's internal `ArtifactStore` the MoonMind artifact system;
- automatically capture native runtime private state not exposed by Omnigent APIs;
- guarantee perfect replay of transient Omnigent SSE events after the stream is disconnected;
- implement multi-step, long-lived Omnigent session reuse across MoonMind steps in v1;
- introduce provider-specific top-level MoonMind agent-id aliases such as `omnigent_session`, `omnigent_claude`, or `omnigent_codex`;
- require a diff endpoint when upstream capability probing says it is unavailable;
- return non-terminal states from `integration.omnigent.execute` unless `MoonMind.AgentRun` is changed to consume status-bearing streaming results.
