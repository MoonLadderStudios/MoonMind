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
          -> Omnigent session
              -> omnigent-host container / sandbox / runner
                  -> Claude Code, Codex, Polly, or another Omnigent harness/agent
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

This mirrors the existing streaming-gateway external-provider pattern. The activity performs the entire Omnigent session execution and returns a terminal canonical `AgentRunResult`, or raises an activity error. It does not return non-terminal provider states in v1.

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

The adapter must copy observable Omnigent inputs, outputs, streams, snapshots, changed-file manifests, current file contents, and session file resources into MoonMind artifacts and return compact refs in `AgentRunResult`.

### 2.4 Durability boundary in v1

The streaming-gateway model runs the entire Omnigent session inside a single `integration.omnigent.execute` activity. Temporal's durability is at the activity boundary, so v1 does **not** provide Temporal-checkpointed progress *within* a run: if the MoonMind worker dies mid-session, Temporal retries the activity from the top.

The honest v1 guarantee is therefore:

- **Omnigent owns live-execution durability.** The Omnigent session and its runner-side workspace survive MoonMind worker death when the Omnigent server/host keeps them alive.
- **Temporal durably records delegation and result.** Temporal durably remembers that the run was delegated to Omnigent and what the final canonical result was, and re-attaches on retry (§9.5, §10, §12.5).
- **Retry re-attaches, it does not recreate.** A retry reconnects to the existing Omnigent session rather than provisioning a second host or reposting the first message.

Out of scope for v1: durable checkpointing of intra-run progress, status-bearing streaming results consumed by `MoonMind.AgentRun`, and Omnigent session continuity across MoonMind steps (a v2 concern — §21). "MoonMind with or without Temporal" is a separate architectural track, not part of this adapter contract.

---

## 3. Conceptual mapping

| MoonMind concept | Omnigent analogue | Adapter rule |
|---|---|---|
| Workflow Execution | No direct equivalent | MoonMind remains orchestration envelope. |
| `MoonMind.AgentRun` | Omnigent session execution | One AgentRun may own one Omnigent session. |
| Workflow step | Session message/turn or whole session | v1 maps one step to one execute activity. |
| Managed runtime profile | Omnigent server/agent/session target | Use endpoint/agent selectors, not raw credentials. |
| ArtifactRef | Omnigent snapshot/resource/file data copied into MoonMind | Never return Omnigent raw resources as top-level result. |
| Cancel workflow/step | `interrupt`, then `stop_session` | Do not delete before harvesting artifacts. |
| Result | Final snapshot + transcript + resource harvest | Return `AgentRunResult` with artifact refs. |

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
- introduce provider-specific top-level MoonMind agent-id aliases such as `omnigent_session`, `omnigent_claude`, or `omnigent_codex`;
- assume Omnigent exposes a stable public diff endpoint when the upstream OpenAPI does not advertise one;
- return non-terminal states from `integration.omnigent.execute` unless `MoonMind.AgentRun` is changed to consume status-bearing streaming results.

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

These capability values match the only existing streaming-gateway provider, OpenClaw:

- `supportsCancel=False` — in streaming mode, cancellation is delivered by Temporal activity cancellation, not a `do_cancel` hook.
- `defaultPollHintSeconds=15` — unused in streaming mode, but kept equal to the other providers rather than introducing a bespoke value.
- `executionStyle` uses the canonical serialized alias used by other external-provider snippets.

### 6.2 Why not `ManagedAgentAdapter`

`ManagedAgentAdapter` is reserved for MoonMind-owned managed runtimes where MoonMind resolves provider profiles, obtains profile leases, shapes credentials/env/files, and launches runtime activities.

The Omnigent topology delegates those responsibilities to Omnigent server and `omnigent-host`. Treating Omnigent as a managed runtime would create a misleading ownership boundary unless MoonMind directly provisions and controls the Omnigent host container itself.

A future v2 may add an `OmnigentManagedBridgeAdapter` only if MoonMind directly provisions Omnigent hosts and controls their lifecycle as MoonMind managed workloads.

Host packaging — whether `omnigent-host` runs containers via docker-in-docker, and whether MoonMind co-locates the Omnigent server/host in its own compose — is a compose/host-image concern tracked outside this adapter contract. If MoonMind ends up provisioning and controlling the host lifecycle, the ownership boundary shifts toward "managed," which is exactly the trigger for the reserved `OmnigentManagedBridgeAdapter`.

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
        "hostId": null,
        "workspacePath": null,
        "title": "Implement auth fix",
        "labels": {},
        "modelOverride": null,
        "reasoningEffort": "high",
        "terminalLaunchArgs": [],
        "collaborationMode": null
      },
      "workspaceContext": {
        "repository": "https://github.com/org/repo",
        "branch": "main",
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
        "sessionFiles": true,
        "githubPr": true,
        "patchSource": "github_pr_or_host_helper",
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
| `session.hostId` | Omnigent external host id | Must be absent/null for `managed`; required for external-host launch flows that need a host-bound runner. |
| `session.workspacePath` | Absolute path on an external Omnigent host | Must be absent/null for `managed`; required when `hostId` is set. |
| `workspaceContext` | Repository/branch context for prompts and artifact metadata | Not sent as Omnigent `workspace` when `hostType=managed`. |
| `session.terminalLaunchArgs` | Native Claude/Codex launch flags | Must be bounded and non-secret. |
| `prompt.text` | Inline prompt | Prefer artifact-backed prompt for large inputs. |
| `prompt.instructionRef` | MoonMind artifact ref with prompt/instructions | Activity reads artifact and posts text. |
| `prompt.includeIdempotencyMarker` | Whether to include a compact non-secret retry marker in the first message | Defaults to true for retry reconciliation. |
| `capture.patchSource` | Where patch artifacts may come from | `github_pr_or_host_helper` in v1; do not call an undocumented Omnigent diff route. |
| `capture.*` | Artifact capture policy | Controls MoonMind harvesting, not Omnigent storage. |

### 7.4 Managed vs external session validation

The adapter must validate the session target before calling Omnigent.

Rules:

- For `hostType="managed"`, omit `host_id` and `workspace` in the Omnigent `POST /v1/sessions` payload. Omnigent server chooses both, and upstream rejects caller-provided `host_id` / `workspace` for managed session creates.
- For `hostType="managed"`, `session.hostId` and `session.workspacePath` must be null or absent. Repository hints belong in `workspaceContext` or top-level `workspaceSpec`, and may be included in the first prompt, but they are not sent as Omnigent `workspace`.
- For `hostType="external"`, `hostId` and `workspacePath` are allowed and may be translated to Omnigent `host_id` and `workspace`.
- A repository URL in `workspaceSpec.repository` or `workspaceContext.repository` is prompt/artifact context unless a future Omnigent API explicitly accepts repository URLs for managed session creation.

### 7.5 Target resolution order

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
| `list_session_files` | `GET /v1/sessions/{id}/resources/files` |
| `get_session_file_content` | `GET /v1/sessions/{id}/resources/files/{file_id}/content` |
| `interrupt` | `POST /v1/sessions/{id}/events` with `type=interrupt` |
| `stop_session` | `POST /v1/sessions/{id}/events` with `type=stop_session` |
| `delete_session` | `DELETE /v1/sessions/{id}` |

There is intentionally no required `get_workspace_diff` method in v1. The upstream OpenAPI-confirmed resource surface is changes plus filesystem file content; patch artifacts must come from GitHub PR harvesting, a host-side helper, or a future Omnigent capability probe.

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
15. Return canonical terminal AgentRunResult, or raise activity error on unrecoverable adapter ambiguity.
```

### 9.3 Session creation

The activity creates an Omnigent session using JSON session creation.

Representative managed-session payload:

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
  "model_override": null,
  "reasoning_effort": "high",
  "terminal_launch_args": []
}
```

For `hostType=managed`, Omnigent server chooses/provisions the host and workspace. The adapter must not send `host_id` or `workspace` for managed session creation.

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

### 9.4 First message construction

The message text is assembled from:

1. `parameters.omnigent.prompt.text`, if present.
2. `parameters.omnigent.prompt.instructionRef`, if present.
3. `AgentExecutionRequest.instructionRef`, if present.
4. `parameters.description`, if present.
5. a generated prompt from title, workspace spec, workspace context, and input refs.

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
9. If reconciliation cannot prove absence, do not blindly repost and do not return a non-terminal state from `integration.omnigent.execute`. In v1, fail closed by raising a retryable activity error while the reconciliation grace period remains, then a non-retryable `integration_error` once retry/reconciliation policy is exhausted. If the implementation chooses to return instead of throwing at exhaustion, the returned `AgentRunResult` must be terminal with `failureClass=integration_error`.
10. If the same `idempotencyKey` is reused with a different first-message digest, fail fast and require an explicit operator reset.

V1 deliberately avoids returning `status=intervention_requested` from the streaming execute activity because the current streaming-gateway workflow path treats any returned result as completed. Supporting a status-bearing streaming result requires a `MoonMind.AgentRun` workflow change and belongs to v2.

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

Rules:

- If a record exists for `idempotencyKey`, reuse its Omnigent session.
- If session creation succeeds, persist the `session_id` before preparing or posting the first message.
- If first-message state is `posted`, skip the first message and continue stream/snapshot reconciliation.
- If first-message state is `posting`, reconcile against Omnigent snapshot/pending inputs/transcript before deciding whether any retry may post.
- Never create a second Omnigent session for the same idempotency key unless an operator explicitly resets the mapping.
- Never post a second first message for the same idempotency key and digest unless reconciliation has positively proved the prior POST did not reach Omnigent.
- Store only non-secret endpoint refs, not raw tokens or headers.

### 10.1 Shared external-session table decision

Do not introduce a generic `externalSession` table in v1.

Omnigent has session-specific concerns — first-message state, pending ids, SSE artifact refs, session resource harvest state, and possible child sessions — that do not yet generalize cleanly across Jules, Codex Cloud, and OpenClaw. Keep the v1 durable mapping provider-specific as `omnigent_external_runs`.

Promotion to a shared table becomes appropriate when at least one additional provider needs durable re-attach/session mapping with comparable fields.

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
| `response.completed` and session returns idle | `completed` |
| `response.failed` | `failed` |
| `session.status = failed` | `failed` |
| Activity timeout | `timed_out` |
| Activity cancellation after interrupt/stop | `canceled` |

These states may be used internally by the execute activity, diagnostics, and future polling/session activities. In the v1 streaming-gateway path, the execute activity still returns only a terminal `AgentRunResult` or raises an activity error.

Unsupported or unknown provider states must be treated as adapter contract errors. Do not pass raw Omnigent statuses into workflow code as canonical states.

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

Because the whole session runs inside one activity, MoonMind worker death is only detected through Temporal's activity heartbeat — the same mechanism the OpenClaw streaming activity relies on.

- **Heartbeat on progress.** The activity heartbeats on each captured SSE frame, or at least on each snapshot / periodic interval, carrying a compact progress token rather than raw payloads.
- **`heartbeat_timeout` must be much smaller than `start_to_close_timeout`.** A long session needs a large start-to-close timeout; without a much smaller heartbeat timeout, worker death surfaces only at start-to-close. Size the heartbeat timeout from `OMNIGENT_STREAM_HEARTBEAT_TIMEOUT_SECONDS` and mark the activity heartbeat-required in the activity catalog.
- **Retry re-attaches, it never recreates.** On retry the activity looks up `omnigent_session_id`, reconnects the SSE stream to that session, fetches a fresh snapshot, applies first-message idempotency, and resumes waiting for terminal state.

---

## 13. Artifact harvesting

### 13.1 Principle

MoonMind must copy Omnigent-observable resources into MoonMind artifacts. Omnigent resource URLs, session ids, or file ids may be stored in metadata, but they are not substitutes for MoonMind `ArtifactRef`s.

### 13.2 Workspace files

At terminal state, harvest the upstream-confirmed resource surfaces:

```text
GET /v1/sessions/{id}/resources/environments/default/changes
GET /v1/sessions/{id}/resources/environments/default/filesystem/{path}
```

Store:

```text
output.workspace.changed_files.index.json
output.workspace.files/<path>.current
output.workspace.manifest.json
```

The adapter must not assume a public Omnigent diff endpoint exists. If a patch artifact is required, produce it from one of these sources:

1. GitHub PR diff after PR creation;
2. host-side helper output, such as `git diff` captured inside the Omnigent host;
3. a future Omnigent diff capability discovered explicitly at runtime.

When none of those sources is available, store a diagnostic artifact instead of failing the whole run solely because `capture.patchSource` could not produce a patch:

```text
output.workspace.patch_unavailable.json
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
    "childSessions": 0,
    "patchSource": "github_pr_or_host_helper",
    "patchProduced": false
  }
}
```

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
4. Use the existing MoonMind redaction helpers, including `SecretRedactor` and `redact_sensitive_text`, rather than inventing a separate Omnigent-only redactor.
5. Omnigent session labels must not include secrets.
6. `parameters.omnigent` must not include raw credentials.
7. Runtime credentials for Claude/Codex remain Omnigent server/host configuration concerns in this topology.
8. Artifact capture must prefer redacted raw event records plus normalized views.
9. Host-side capture helpers, if added later, must authenticate to MoonMind artifact APIs using scoped, short-lived credentials.
10. First-message idempotency markers may include correlation ids and digests, but must not include raw prompts from private artifacts beyond the actual prompt body that is already being sent to Omnigent.

---

## 17. Error classification

| Failure | MoonMind failure class | Notes |
|---|---|---|
| Omnigent server unreachable before session create | `integration_error` | Retryable if transport policy allows. |
| Omnigent server returns `429` / rate limited | `integration_error` | Classify as rate-limited; honor `Retry-After` and use bounded retry, not immediate retry. |
| Authentication failure to Omnigent | `integration_error` | Non-retryable until config fixed. |
| Unknown Omnigent agent name | `user_error` | Bad request/target selection. |
| Invalid managed-session create fields | `user_error` | Example: `hostType=managed` with `host_id` / `workspace`. |
| Session create 4xx | `user_error` or `integration_error` | Depends on validation vs auth/config. |
| Managed host provisioning failure | `system_error` or `integration_error` | Preserve Omnigent error body in diagnostics. |
| Runtime/harness not configured | `integration_error` | Omnigent host/provider config issue. |
| Agent returns failed response | `execution_error` | Runtime attempted work and failed. |
| Activity timeout | `system_error` or `execution_error` | Depends on timeout policy. |
| Artifact harvest partial failure | completed with diagnostics or `system_error` | Policy-controlled. |
| Unknown Omnigent stream event schema | `integration_error` | Contract drift. |
| Idempotency key reused with different first-message digest | `user_error` | Caller attempted conflicting replay. |
| Retry cannot prove whether first message was accepted | `integration_error` | V1 streaming execute must fail/throw or return a terminal result with this valid `FailureClass`; it must not return non-terminal `intervention_requested`. |

---

## 18. Canonical result contract

The execute activity must return a compact `AgentRunResult`.

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
    "githubPrUrl": "https://github.com/org/repo/pull/123",
    "captureManifestRef": "art_capture_manifest",
    "patchRef": "art_github_pr_diff"
  }
}
```

Provider-native payloads must not be returned as top-level fields. In the v1 streaming-gateway path, returned results are terminal; non-terminal state reporting requires a future workflow contract change.

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
- per-message idempotency records, not only first-message records;
- re-evaluate whether external sessions may carry `host_id` / `workspace` while managed sessions still omit both;
- if streaming-gateway execution should surface non-terminal states, change `MoonMind.AgentRun` to consume a status-bearing streaming result instead of treating every returned result as completed.

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
- managed-session rejection of caller-provided host/workspace fields;
- external-session translation of `hostId` / `workspacePath` to Omnigent `host_id` / `workspace`;
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
- no required `get_workspace_diff` transport call in v1.

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
- resource endpoints for changes, filesystem content, and session files.

Scenarios:

1. successful run with text output;
2. failed run;
3. managed host launch delay;
4. managed session create omits `host_id` and `workspace`;
5. external session create may include `host_id` and `workspace`;
6. stream disconnect and snapshot reconciliation;
7. elicitation request and approval;
8. changed files and current-file harvest without a diff endpoint;
9. patch unavailable diagnostic when no GitHub PR/host helper/future diff capability exists;
10. child session event capture;
11. cancellation before completion;
12. idempotent retry after session create but before first message;
13. idempotent retry after first message response but before terminal result;
14. crash-window retry with `posting` state and snapshot reconciliation;
15. ambiguous `posting` reconciliation fails closed rather than returning `intervention_requested` from execute;
16. conflicting first-message digest under the same idempotency key.

### 23.4 Live smoke tests

Run against a real Omnigent server with a disposable repository and a sandbox host. Validate:

- session creation;
- managed session creation does not include caller-provided workspace;
- host provisioning;
- Claude or Codex launch selected through `parameters.omnigent`;
- stream capture;
- final snapshot capture;
- changed-file harvest;
- optional PR detection;
- activity retry does not duplicate the first prompt.

---

## 24. Implementation sequencing

This canonical doc describes desired state. Detailed rollout tasks and temporary execution checklists should live under `docs/tmp/` or local-only handoff artifacts.

The stable implementation milestones are:

1. Runtime gate, typed target models, fake Omnigent client, and adapter registration.
2. `integration.omnigent.execute` with stream/snapshot capture and first-message idempotency.
3. Resource harvesting for changed-file manifests, current file contents, and session files.
4. GitHub PR post-processing for durable patch artifacts when PRs are produced.
5. Optional polling/session reuse and host-side capture helper.

---

## 25. Open questions

1. Should Omnigent endpoint selection be global config only, or should MoonMind support named endpoint refs per tenant/project?
2. Should v1 require `hostType=managed`, or should external-host sessions be allowed immediately?
3. Should the adapter delete Omnigent sessions after successful harvest in CI-style workflows?
4. Should MoonMind patch Omnigent to emit webhooks or artifact callbacks instead of relying on SSE capture?
5. How should clear/context-reset semantics map onto MoonMind step epochs in v2?
6. *(Resolved — see §10.1.)* A shared `externalSession` table is not introduced in v1; `omnigent_external_runs` stays dedicated, and promotion to a shared table is deferred until a second provider needs durable session mapping.
7. *(Resolved — fail closed.)* Ambiguous `posting` retries do not return a non-terminal state from the streaming execute activity. They raise/retry while the reconciliation grace period remains, then fail with `failureClass=integration_error` or an equivalent non-retryable integration error once exhausted.
8. Should MoonMind require a host-side helper for first-class patch artifacts, or is GitHub PR post-processing sufficient for v1?

---

## 26. Design invariant

The core invariant is:

> Omnigent may own live execution, but MoonMind owns durable orchestration and durable evidence.

Therefore every Omnigent-backed AgentRun must end with a canonical MoonMind result whose important inputs, outputs, diagnostics, and observable runtime traces are represented as MoonMind artifact refs, not as transient Omnigent session state.
