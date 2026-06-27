# Omnigent Adapter Design

Status: Proposed design
Owners: MoonMind Platform
Last updated: 2026-06-27

**Implementation tracking:** rollout notes, spikes, and temporary handoffs should live under `docs/tmp/` or gitignored local-only artifacts, not as mutable checklists in this canonical design document.

## Related docs

- `docs/ExternalAgents/AddingExternalProvider.md`
- `docs/ExternalAgents/OpenClawAgentAdapter.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/ErrorTaxonomy.md`
- `docs/MoonMindArchitecture.md`
- Omnigent upstream API reference: `omnigent/server/API.md` in `omnigent-ai/omnigent`

---

## 1. Purpose

This document defines a declarative design for integrating **Omnigent** as an execution substrate inside MoonMind.

In the target topology, MoonMind does not launch Claude Code, Codex CLI, or other Omnigent-supported harnesses directly. Instead:

```text
MoonMind Temporal Workflow / AgentRun
  -> MoonMind Omnigent adapter
      -> Omnigent server API
          -> Omnigent managed session
              -> omnigent-host container / sandbox / runner
                  -> Claude Code, Codex, or another Omnigent harness
```

The adapter must preserve MoonMind's core architecture:

- Temporal owns durable orchestration.
- MoonMind artifacts own durable evidence.
- Omnigent owns live session/runtime execution.
- Provider-specific details stay at the adapter/activity boundary.
- Workflow code receives only canonical MoonMind runtime contracts.

---

## 2. Decision summary

### 2.1 Treat Omnigent as an external provider in v1

The v1 adapter should use the **external-agent adapter pattern**, not the managed-runtime adapter pattern.

Reasoning:

- MoonMind is not directly launching the agent process.
- MoonMind is not directly materializing Claude/Codex credentials into a runtime container.
- MoonMind is delegating a run to an Omnigent server, which then provisions or uses an Omnigent host/runner.
- Omnigent's primary unit is a long-running session/conversation, not a MoonMind-managed runtime process.

Therefore the v1 provider must register as a single canonical provider identity:

```text
agentKind = external
agentId   = omnigent
```

Runtime selection, session mode, harness choice, Claude-vs-Codex choice, and built-in Omnigent agent selection must be declared in `parameters.omnigent`, not by changing the top-level MoonMind `agentId`.

### 2.2 Use streaming-gateway execution first

The recommended v1 activity shape is:

```text
integration.omnigent.execute
```

This mirrors the existing streaming-gateway external-provider pattern. The activity performs the entire Omnigent session execution and returns a canonical `AgentRunResult`.

The polling/session lifecycle may be added later as v2:

```text
integration.omnigent.start
integration.omnigent.status
integration.omnigent.fetch_result
integration.omnigent.cancel
integration.omnigent.send_message       # optional extension
integration.omnigent.harvest_session    # optional extension
```

### 2.3 MoonMind remains the artifact authority

Omnigent's files panel and session resources are observable surfaces, not the authoritative MoonMind artifact system.

The adapter must copy observable Omnigent inputs, outputs, streams, snapshots, diffs, and resource files into MoonMind artifacts and return compact refs in `AgentRunResult`.

### 2.4 Durability boundary in v1

The streaming-gateway model runs the entire Omnigent session inside a single `integration.omnigent.execute` activity. Temporal's durability is at the activity boundary, so v1 does **not** provide Temporal-checkpointed progress *within* a run: if the MoonMind worker dies mid-session, Temporal retries the activity from the top.

The honest v1 guarantee is therefore:

- **omnigent-host owns live-execution durability.** The Omnigent session and its workspace survive MoonMind worker death because they live on the Omnigent side, not in the activity attempt.
- **Temporal durably records delegation and result.** Temporal durably remembers that the run was delegated to Omnigent and what the final canonical result was, and re-attaches on retry (§9.5, §10, §12.5).
- **Retry re-attaches, it does not recreate.** A retry reconnects to the existing Omnigent session rather than provisioning a second host or reposting the first message.

Out of scope for v1: durable checkpointing of intra-run progress, and Omnigent session continuity across MoonMind steps (a v2 concern — §21). "MoonMind with or without Temporal" is a separate architectural track, not part of this adapter contract.

---

## 3. Conceptual mapping

| MoonMind concept | Omnigent analogue | Adapter rule |
|---|---|---|
| Workflow Execution | No direct equivalent | MoonMind remains orchestration envelope. |
| `MoonMind.AgentRun` | Omnigent session execution | One AgentRun may own one Omnigent session. |
| Workflow step | Session message/turn or whole session | v1 maps one step to one execute activity. |
| Managed runtime profile | Omnigent server/agent/session target | Use endpoint/agent selectors, not raw credentials. |
| ArtifactRef | Omnigent snapshot/resource/file/diff copied into MoonMind | Never return Omnigent raw resources as top-level result. |
| Cancel workflow/step | `interrupt`, then `stop_session` | Do not delete before harvesting artifacts. |
| Result | Final snapshot + transcript + workspace harvest | Return `AgentRunResult` with artifact refs. |

---

## 4. Non-goals

The v1 adapter does not attempt to:

- replace MoonMind's direct Claude Code or Codex managed runtimes;
- expose Omnigent as a second workflow engine;
- store raw Omnigent server credentials in workflow payloads;
- make Omnigent's internal `ArtifactStore` the MoonMind artifact system;
- automatically capture native runtime private state not exposed by Omnigent APIs;
- guarantee perfect replay of transient Omnigent SSE events after the stream is disconnected;
- implement multi-step, long-lived Omnigent session reuse across MoonMind steps in v1;
- introduce provider-specific top-level MoonMind agent-id aliases such as `omnigent_session`, `omnigent_claude`, or `omnigent_codex`.

---

## 5. Provider identity and registration

### 5.1 Runtime gate

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

### 5.2 External registry

Register the adapter only when the runtime gate is enabled.

Canonical registration:

```text
omnigent
```

Do not register a second top-level `agentId` for the same provider. In particular, do not register:

```text
omnigent_session
omnigent_claude
omnigent_codex
omnigent_polly
```

Those values would split routing, policy, metrics, tests, and provider identity. Session mode, runtime selection, built-in agent choice, and harness overrides belong under `parameters.omnigent`.

The top-level `AgentExecutionRequest` remains:

```json
{
  "agentKind": "external",
  "agentId": "omnigent"
}
```

---

## 6. Adapter classification

### 6.1 Base class

The provider adapter should extend `BaseExternalAgentAdapter`.

The class exists primarily for registry/capability declaration in v1. The actual execution should happen in `integration.omnigent.execute`, following the streaming-gateway model.

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
    execution_style="streaming_gateway",
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

These capability values match the only existing streaming-gateway provider, OpenClaw:

- `supportsCancel=False` — in streaming mode, cancellation is delivered by Temporal activity cancellation, not a `do_cancel` hook (the hook raises `RuntimeError`). Advertising `supportsCancel=True` while the hook raises would be internally contradictory.
- `defaultPollHintSeconds=15` — unused in streaming mode, but kept equal to the other providers rather than introducing a bespoke value.

### 6.2 Why not `ManagedAgentAdapter`

`ManagedAgentAdapter` is reserved for MoonMind-owned managed runtimes where MoonMind resolves provider profiles, obtains profile leases, shapes credentials/env/files, and launches runtime activities.

The Omnigent topology delegates those responsibilities to Omnigent server and `omnigent-host`. Treating Omnigent as a managed runtime would create a misleading ownership boundary unless MoonMind directly provisions and controls the Omnigent host container itself.

A future v2 may add an `OmnigentManagedBridgeAdapter` only if MoonMind directly provisions Omnigent hosts and controls their lifecycle as MoonMind managed workloads.

Host packaging — whether `omnigent-host` runs containers via docker-in-docker, and whether MoonMind co-locates the Omnigent server/host in its own compose — is a Phase-1 compose/host-image concern tracked in `OmnigentIntegrationArchitecture.md`, not part of this adapter contract. If MoonMind ends up provisioning and controlling the host lifecycle, the ownership boundary shifts toward "managed," which is exactly the trigger for the reserved `OmnigentManagedBridgeAdapter`.

---

## 7. Declarative request contract

### 7.1 Top-level request

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

### 7.2 Omnigent target block

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
        "workspace": "https://github.com/org/repo#main",
        "title": "Implement auth fix",
        "labels": {},
        "modelOverride": null,
        "reasoningEffort": "high",
        "terminalLaunchArgs": [],
        "collaborationMode": null
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
        "fileDiffs": true,
        "workspaceFiles": true,
        "sessionFiles": true,
        "childSessions": true,
        "githubPr": true,
        "deleteOmnigentSessionAfterHarvest": false
      }
    }
  }
}
```

### 7.3 Field meanings

| Field | Meaning | Notes |
|---|---|---|
| `endpointRef` | Named Omnigent server endpoint | Resolves from activity-side config. No raw token. |
| `agent.agentId` | Existing Omnigent `ag_*` id | Preferred when known. |
| `agent.agentName` | Omnigent agent name to resolve through `/api/agents` | Useful for built-ins. |
| `agent.bundleRef` | MoonMind artifact ref for an Omnigent agent bundle | Uploaded via `/api/agents` if no `agentId`. |
| `agent.harnessOverride` | Omnigent session harness override | Optional; only for compatible Omnigent agents. |
| `session.hostType` | `managed` or `external` | `managed` means Omnigent provisions host/sandbox. |
| `session.workspace` | Repo URL for managed sessions or path for external sessions | For managed sessions, prefer repo URL with optional `#branch`. |
| `session.terminalLaunchArgs` | Native Claude/Codex launch flags | Must be bounded and non-secret. |
| `prompt.text` | Inline prompt | Prefer artifact-backed prompt for large inputs. |
| `prompt.instructionRef` | MoonMind artifact ref with prompt/instructions | Activity reads artifact and posts text. |
| `prompt.includeIdempotencyMarker` | Whether to include a compact non-secret retry marker in the first message | Defaults to true for retry reconciliation. |
| `capture.*` | Artifact capture policy | Controls MoonMind harvesting, not Omnigent storage. |

### 7.4 Target resolution order

The activity resolves the Omnigent agent target in this order:

1. Use `agent.agentId` if present.
2. Resolve `agent.agentName` with `GET /api/agents`.
3. If `agent.bundleRef` is present, read the MoonMind artifact and upload to `POST /api/agents`.
4. Fall back to `OMNIGENT_DEFAULT_AGENT_NAME`.
5. Fail with `integration_error` if no target can be resolved.

---

## 8. Omnigent client

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
| `get_workspace_file` | `GET /v1/sessions/{id}/resources/environments/default/filesystem/{path}` |
| `get_workspace_diff` | `GET /v1/sessions/{id}/resources/environments/default/diff/{path}` |
| `list_session_files` | `GET /v1/sessions/{id}/resources/files` |
| `get_session_file_content` | `GET /v1/sessions/{id}/resources/files/{file_id}/content` |
| `interrupt` | `POST /v1/sessions/{id}/events` with `type=interrupt` |
| `stop_session` | `POST /v1/sessions/{id}/events` with `type=stop_session` |
| `delete_session` | `DELETE /v1/sessions/{id}` |

Transport rules:

- Use activity-side authentication only.
- Redact request/response logs before artifact persistence.
- Treat non-2xx responses as structured integration errors.
- Preserve Omnigent status codes and error bodies in diagnostics artifacts after redaction.
- Avoid tight read timeouts for SSE streams.
- Surface malformed SSE frames as contract errors unless clearly transient before execution started.

---

## 9. Execute activity lifecycle

### 9.1 Activity name

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

### 9.2 Lifecycle flow

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
15. Return canonical AgentRunResult.
```

### 9.3 Session creation

The activity creates an Omnigent session using JSON session creation.

Representative payload:

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

For `hostType=managed`, Omnigent server chooses/provisions the host and workspace. For `hostType=external`, the caller must provide an Omnigent host/workspace model that is already valid for that server.

### 9.4 First message construction

The message text is assembled from:

1. `parameters.omnigent.prompt.text`, if present.
2. `parameters.omnigent.prompt.instructionRef`, if present.
3. `AgentExecutionRequest.instructionRef`, if present.
4. `parameters.description`, if present.
5. a generated prompt from title, workspace spec, and input refs.

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

### 9.5 First message idempotency

The first message must be durable-idempotent across Temporal activity retries.

The adapter must not unconditionally post the first message when reusing an existing Omnigent session for the same `idempotencyKey`.

Required durable states:

```text
not_prepared -> prepared -> posting -> posted -> terminal
```

Rules:

1. `prepared` records the canonical message digest before any HTTP POST.
2. `posting` is persisted immediately before the `POST /events` call.
3. `posted` is persisted immediately after Omnigent returns a successful response.
4. If Omnigent returns a native-terminal `pending_id`, store it.
5. If a retry finds `posted`, it must skip the first POST.
6. If a retry finds `posting`, it must reconcile before deciding whether to repost.
7. Reconciliation checks Omnigent snapshot `items`, native `pending_inputs`, and any captured `session.input.consumed` events for the stored digest or idempotency marker.
8. If reconciliation finds the first message, mark it `posted` and skip posting.
9. If reconciliation cannot prove absence, do not blindly repost. Wait within the configured reconciliation grace period or surface `intervention_requested` / `integration_error` rather than duplicating work.
10. If the same `idempotencyKey` is reused with a different first-message digest, fail fast and require an explicit operator reset.

Representative event:

```json
{
  "type": "message",
  "data": {
    "role": "user",
    "content": [
      {"type": "input_text", "text": "..."}
    ]
  }
}
```

---

## 10. Idempotency and run mapping

The adapter must persist an idempotency mapping outside the Activity attempt.

### 10.1 Why a durable table (Simplicity Gate)

Existing external adapters keep idempotency in `BaseExternalAgentAdapter._starts_by_idempotency`, an in-memory dict scoped to a single Activity attempt. That is sufficient for stateless streaming providers like OpenClaw, where a retry simply re-runs a chat-style completion. It is **not** sufficient for Omnigent: a retry that cannot see the prior attempt's state would create a second Omnigent session — meaning duplicate host provisioning and possibly a duplicate PR.

Per the CLAUDE.md Simplicity Gate, the decision and what it replaces:

- **v1 uses a dedicated `omnigent_external_runs` durable store**, because the in-memory per-attempt dict cannot survive worker death or span Temporal retries — the exact failure mode that produces duplicate sessions.
- MoonMind does **not** introduce a shared `externalSession` table for Jules / Codex Cloud / OpenClaw / Omnigent in v1. No such table exists today; adding one now would be speculative cross-provider scaffolding outside this task's scope. Promotion to a shared table is deferred until a second provider actually needs durable session mapping (resolves open question #6).

### 10.2 Run-mapping store

The durable store holds:

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

Rules:

- If a record exists for `idempotencyKey`, reuse its Omnigent session.
- If session creation succeeds, persist the `session_id` before preparing or posting the first message.
- If first-message state is `posted`, skip the first message and continue stream/snapshot reconciliation.
- If first-message state is `posting`, reconcile against Omnigent snapshot/pending inputs/transcript before deciding whether any retry may post.
- Never create a second Omnigent session for the same idempotency key unless an operator explicitly resets the mapping.
- Never post a second first message for the same idempotency key and digest unless reconciliation has positively proved the prior POST did not reach Omnigent.
- Store only non-secret endpoint refs, not raw tokens or headers.

---

## 11. State normalization

Map Omnigent observations into canonical MoonMind states.

| Omnigent observation | MoonMind status |
|---|---|
| Session created, host still launching | `launching` |
| Runner unavailable during managed provisioning | `launching` |
| `session.status = running` | `running` |
| `session.status = waiting` and active elicitation exists | `awaiting_approval` |
| `session.status = waiting` without known elicitation | `intervention_requested` |
| `response.elicitation_request` | `awaiting_approval` |
| Terminal response received; adapter harvesting snapshot/workspace/session artifacts | `collecting_results` |
| `response.completed`, session idle, and harvest complete | `completed` |
| `response.failed` | `failed` |
| `session.status = failed` | `failed` |
| Activity timeout | `timed_out` |
| Activity cancellation after interrupt/stop | `canceled` |

Unsupported or unknown provider states must be treated as canonical contract failures and surfaced with the `UnsupportedStatus` `ApplicationError.type` from `ErrorTaxonomy.md` (§5.3), not a bespoke error. Do not pass raw Omnigent statuses into workflow code as canonical states.

---

## 12. Stream and snapshot capture

### 12.1 Capture principle

Omnigent's stream is a live tail. It is not a replay log. Therefore the execute activity must open the stream before posting the first message whenever possible.

On retry, opening the stream before reconciliation is still preferred, but the first-message idempotency rules take precedence. A retry must never post the first message merely because it has successfully reattached to the stream.

### 12.2 Required artifacts

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

### 12.3 Normalized SSE event shape

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

### 12.4 Snapshot rule

Fetch snapshots at least twice:

1. immediately after stream attachment;
2. after terminal state or cancellation.

Optional periodic snapshots may be captured for long runs.

### 12.5 Heartbeating, worker death, and re-attach

Because the whole session runs inside one activity, MoonMind worker death is only detected through Temporal's activity heartbeat — the same mechanism the OpenClaw streaming activity relies on ("heartbeats carry stream progress").

- **Heartbeat on progress.** The activity heartbeats on each captured SSE frame (or at least each snapshot / periodic interval), carrying a compact progress token (last event id / snapshot marker), never raw payloads.
- **`heartbeat_timeout` ≪ `start_to_close_timeout`.** A long session needs a large start-to-close timeout; without a much smaller heartbeat timeout, worker death surfaces only at start-to-close, delaying recovery. Size the heartbeat timeout from `OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS` (§5.1) and mark the activity heartbeat-required in the activity catalog (`ActivityCatalogAndWorkerTopology.md`).
- **Retry re-attaches, it never recreates.** On retry the activity: (1) looks up `omnigent_session_id` by `idempotencyKey` in `omnigent_external_runs` (§10); (2) reconnects the SSE stream to that session; (3) fetches a fresh snapshot (§12.4) to reconcile the gap missed while disconnected; (4) applies first-message idempotency (§9.5) before any POST; (5) resumes waiting for terminal state. A successful re-attach must never trigger a second session or a duplicate first message.

---

## 13. Artifact harvesting

### 13.1 Principle

MoonMind must copy Omnigent-observable resources into MoonMind artifacts. Omnigent resource URLs, session ids, or file ids may be stored in metadata, but they are not substitutes for MoonMind `ArtifactRef`s.

### 13.2 Workspace files

At terminal state, harvest:

```text
GET /v1/sessions/{id}/resources/environments/default/changes
GET /v1/sessions/{id}/resources/environments/default/diff/{path}
GET /v1/sessions/{id}/resources/environments/default/filesystem/{path}
```

Store:

```text
output.workspace.changed_files.index.json
output.workspace.diff.full.patch
output.workspace.files/<path>.before
output.workspace.files/<path>.after
output.workspace.files/<path>.current
output.workspace.manifest.json
```

### 13.3 Session file resources

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

### 13.4 GitHub and PR artifacts

If the run creates a PR or emits a PR URL:

```text
output.github.pr.metadata.json
output.github.pr.diff.patch
output.github.pr.checks.json
output.github.pr.comments.json
```

PR harvesting may be implemented by a follow-up GitHub post-processor activity. The Omnigent adapter should at minimum detect and persist PR URLs from transcript, final snapshot, and metadata.

### 13.5 Diagnostics artifact

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
    "childSessions": 0
  }
}
```

### 13.6 Canonical `link_type` bindings

Harvested artifacts must be persisted under the stable `artifact_links.link_type` taxonomy from `WorkflowArtifactSystemDesign.md` so existing MoonMind observability surfaces pick them up. The readable filenames in §12.2 and §13 are labels; the `link_type` is the machine-stable binding.

| Harvested artifact | Canonical `link_type` |
|---|---|
| `input.omnigent.session_create.request/response.json` | `input.manifest` |
| `input.omnigent.first_message.request/response.json` | `input.instructions` |
| `runtime.omnigent.sse.raw.jsonl` | `runtime.merged_logs` |
| `runtime.omnigent.sse.normalized.jsonl` | `output.logs` |
| `runtime.omnigent.snapshot.initial.json` | `output.provider_snapshot` |
| `output.omnigent.snapshot.final.json` | `output.provider_snapshot` |
| `output.omnigent.transcript.jsonl` | `runtime.merged_logs` |
| `output.omnigent.final_response.md` | `output.primary` |
| `output.workspace.diff.full.patch` | `output.patch` |
| `output.github.pr.diff.patch` | `output.patch` |
| diagnostics artifact (§13.5) | `runtime.diagnostics` |
| capture manifest / changed-files index | `output.agent_result` |

Multiple artifacts may share a `link_type` (e.g. both snapshots, both patches); disambiguate with `label` and metadata per `WorkflowArtifactSystemDesign.md`.

---

## 14. Child sessions

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

## 15. Cancellation and cleanup

### 15.1 Soft cancel

On Temporal activity cancellation or MoonMind AgentRun cancellation:

```json
{
  "type": "interrupt",
  "data": {}
}
```

### 15.2 Hard stop

If the session remains active after a short grace period:

```json
{
  "type": "stop_session",
  "data": {}
}
```

### 15.3 Delete policy

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

## 16. Security and secret handling

Rules:

1. Omnigent server URL may be stored as an endpoint ref in workflow payloads.
2. Omnigent API tokens must be activity-side secrets only.
3. MoonMind must redact auth headers, cookies, tokens, and secret-looking fields before artifact storage.
4. Omnigent session labels must not include secrets.
5. `parameters.omnigent` must not include raw credentials.
6. Runtime credentials for Claude/Codex remain Omnigent server/host configuration concerns in this topology.
7. Artifact capture must prefer redacted raw event records plus normalized views.
8. Host-side capture helpers, if added later, must authenticate to MoonMind artifact APIs using scoped, short-lived credentials.
9. First-message idempotency markers may include correlation ids and digests, but must not include raw prompts from private artifacts beyond the actual prompt body that is already being sent to Omnigent.

---

## 17. Error classification

| Failure | MoonMind failure class | Notes |
|---|---|---|
| Omnigent server unreachable before session create | `integration_error` | Retryable if transport policy allows. |
| Omnigent server returns `429` / rate limited | `integration_error` (retryable-with-policy) | Classify as `RATE_LIMITED` per `ErrorTaxonomy.md` §8; honor `Retry-After` and use bounded retry, not immediate retry. |
| Authentication failure to Omnigent | `integration_error` | Non-retryable until config fixed. |
| Unknown Omnigent agent name | `user_error` | Bad request/target selection. |
| Session create 4xx | `user_error` or `integration_error` | Depends on validation vs auth/config. |
| Managed host provisioning failure | `system_error` or `integration_error` | Preserve Omnigent error body in diagnostics. |
| Runtime/harness not configured | `integration_error` | Omnigent host/provider config issue. |
| Agent returns failed response | `execution_error` | Runtime attempted work and failed. |
| Activity timeout | `system_error` or `execution_error` | Depends on timeout policy. |
| Artifact harvest partial failure | completed with diagnostics or `system_error` | Policy-controlled. |
| Unknown Omnigent stream event schema | `integration_error` | Contract drift. |
| Idempotency key reused with different first-message digest | `user_error` | Caller attempted conflicting replay. |
| Retry cannot prove whether first message was accepted | `intervention_requested` or `integration_error` | Do not blindly duplicate the first message. |

---

## 18. Canonical result contract

The execute activity must return a compact `AgentRunResult`.

Example:

```json
{
  "outputRefs": [
    "art_transcript",
    "art_final_snapshot",
    "art_workspace_manifest",
    "art_full_patch"
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
    "captureManifestRef": "art_capture_manifest"
  }
}
```

Provider-native payloads must not be returned as top-level fields.

---

## 19. Observability surfaces

The adapter should produce enough data to power existing MoonMind AgentRun observability endpoints.

Suggested mapping:

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

## 20. v1 adapter and registry sketch

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
    execution_style="streaming_gateway",
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

## 21. v2 polling/session mode

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
- per-message idempotency records, not only first-message records.

---

## 22. Host-side capture helper

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

## 23. Testing strategy

### 23.1 Unit tests

- target block validation;
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
- digest mismatch fail-fast behavior.

### 23.2 Adapter contract tests

- provider registers only when enabled;
- `agentKind=external` is required;
- `agentId=omnigent` is accepted;
- unknown `agentId` is rejected;
- `agentId=omnigent_session` is rejected;
- streaming capability is declared;
- polling hooks fail loudly in v1.

### 23.3 Integration tests with fake Omnigent server

The fake server should support:

- `/api/agents` list/get;
- `/v1/sessions` create;
- `/v1/sessions/{id}` snapshot;
- `/v1/sessions/{id}/events`;
- `/v1/sessions/{id}/stream` SSE;
- resource endpoints for changes/files/diffs.

Scenarios:

1. successful run with text output;
2. failed run;
3. managed host launch delay;
4. stream disconnect and snapshot reconciliation;
5. elicitation request and approval;
6. changed files and diff harvest;
7. child session event capture;
8. cancellation before completion;
9. idempotent retry after session create but before first message;
10. idempotent retry after first message response but before terminal result;
11. crash-window retry with `posting` state and snapshot reconciliation;
12. conflicting first-message digest under the same idempotency key.

### 23.4 Live smoke tests

Run against a real Omnigent server with a disposable repository and a sandbox host. Validate:

- session creation;
- host provisioning;
- Claude or Codex launch selected through `parameters.omnigent`;
- stream capture;
- final snapshot capture;
- changed-file harvest;
- optional PR detection;
- activity retry does not duplicate the first prompt.

---

## 24. Rollout phases

### Phase 0: design and fake-client tests

- Add this design doc.
- Add settings gate.
- Add typed target/capture models.
- Add fake Omnigent client tests.

### Phase 1: streaming execute provider

- Add `OmnigentExternalAdapter` registration.
- Add `integration.omnigent.execute` activity.
- Register `integration.omnigent.execute` in the activity catalog under the integration family/fleet, marked heartbeat-required (`ActivityCatalogAndWorkerTopology.md`).
- Expose the activity through the integration worker handler / task-queue binding so it is discoverable via catalog-driven routing.
- Capture requests, stream, snapshots, and final result.
- Return canonical `AgentRunResult` with artifact refs.
- Enforce one canonical top-level `agentId=omnigent`.
- Enforce first-message durable idempotency.

### Phase 2: resource harvesting

- Harvest changed files, diffs, file contents, and session files.
- Add capture manifest.
- Add partial-harvest diagnostics policy.

### Phase 3: PR post-processing

- Detect PR URL/branch.
- Fetch GitHub PR metadata/diff/checks/comments.
- Link PR artifacts to AgentRun result.

### Phase 4: interactive session reuse

- Add polling/session activity family.
- Support continuation across workflow steps.
- Add durable stream mirror or session event polling.
- Add idempotency records for each follow-up message.

### Phase 5: host-side capture helper

- Add optional helper to Omnigent host image.
- Capture native logs, full terminal scrollback, runtime diagnostics, and workspace tarballs.

---

## 25. Open questions

1. Should Omnigent endpoint selection be global config only, or should MoonMind support named endpoint refs per tenant/project?
2. Should v1 require `hostType=managed`, or should external-host sessions be allowed immediately?
3. Should the adapter delete Omnigent sessions after successful harvest in CI-style workflows?
4. Should MoonMind patch Omnigent to emit webhooks or artifact callbacks instead of relying on SSE capture?
5. How should clear/context-reset semantics map onto MoonMind step epochs in v2?
6. *(Resolved — see §10.1.)* A shared `externalSession` table is not introduced in v1; `omnigent_external_runs` stays dedicated, and promotion to a shared table is deferred until a second provider needs durable session mapping.
7. *(Resolved — fail closed.)* Ambiguous `posting` retries surface `intervention_requested` rather than reposting (§9.5 rule 9); a CI-only opt-in for repost-after-positive-absence is possible later but out of scope for v1.

---

## 26. Design invariant

The core invariant is:

> Omnigent may own live execution, but MoonMind owns durable orchestration and durable evidence.

Therefore every Omnigent-backed AgentRun must end with a canonical MoonMind result whose important inputs, outputs, diagnostics, and observable runtime traces are represented as MoonMind artifact refs, not as transient Omnigent session state.
