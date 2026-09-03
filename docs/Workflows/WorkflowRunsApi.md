# Workflow Runs API

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-08-07

## 1. Purpose

Define the REST API surfaces used to create, monitor, control, and observe MoonMind Workflow Executions in the Temporal-first architecture.

MoonMind splits this responsibility across:

- **`/api/executions`** for Temporal-backed execution lifecycle operations,
- **`/api/agent-runs`** for artifact-backed managed-run observability,
- **the Omnigent Bridge** for authorized native-session, event, stream, resource, and control traffic.

The Workflow Detail **Chat** product surface is defined by `docs/UI/WorkflowChatPanel.md`. Ordinary chat uses the native Omnigent composer through a workflow-bound MoonMind bridge. It does not use the reserved chat-instruction endpoint or a Temporal Update.

The dashboard presents executions as **Workflows** in product UI, but the lifecycle API remains execution-oriented.

The public `/api/agent-runs` path comes from the `agent_runs` router's `prefix="/agent-runs"`, which FastAPI mounts under the app-level `/api` prefix.

## 2. API surface

### 2.1 Execution lifecycle (`/api/executions`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/executions` | Create/start a Temporal-backed execution. |
| `GET` | `/api/executions` | List executions visible to the caller. |
| `GET` | `/api/executions/{workflowId}` | Get execution detail. |
| `POST` | `/api/executions/{workflowId}/update` | Apply an enabled in-place workflow update such as `UpdateInputs`, `SetTitle`, or `RequestRerun`. The deferred `SubmitChatInstruction` update is not an ordinary chat compatibility form. |
| `POST` | `/api/executions/{workflowId}/signal` | Send an asynchronous workflow signal such as pause, resume, or approve. |
| `POST` | `/api/executions/{workflowId}/cancel` | Cancel or terminate an execution. |

### 2.2 Auxiliary execution routes (`/api/executions`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/executions/{workflowId}/manifest-status` | Fetch manifest-run status summary. |
| `GET` | `/api/executions/{workflowId}/manifest-nodes` | Page manifest node state. |
| `GET` | `/api/executions/{workflowId}/steps` | Fetch the latest/current run Step ledger. |
| `GET` | `/api/executions/{workflowId}/chat-binding` | Resolve the browser-safe, caller-authorized native Workflow Chat binding. |
| `POST` | `/api/executions/{workflowId}/integration` | Register/update integration monitoring state. |
| `POST` | `/api/executions/{workflowId}/integration/poll` | Record integration poll results. |
| `POST` | `/api/executions/{workflowId}/reschedule` | Change the scheduled time of a scheduled execution. |
| `POST` | `/api/executions/{workflowId}/rerun` | Create a fresh execution with the original parameters and a new `workflowId`. |
| `POST` | `/api/executions/{workflowId}/recover-from-failed-step` | Create a linked recovery execution that resumes from the last failed Step using durable checkpoint evidence. |
| `POST` | `/api/executions/{workflowId}/recover-from-selected-step` | Create a linked recovery execution from an operator-selected eligible Step using pinned source identity and checkpoint evidence. |
| `POST` | `/api/executions/{workflowId}/continue` | Create an authorized linked continuation from terminal source evidence when the product exposes **Continue in a new workflow**. |

The terminal continuation route leaves the source execution unchanged. It is not an ordinary chat message, not failed-Step recovery, and not proof that the deferred chat-instruction feature is enabled.

### 2.3 Workflow-bound native Chat facade

The browser-facing native application uses an opaque `chatBindingId`, not a caller-selected provider session id or endpoint.

Canonical surfaces:

| Method | Path | Description |
|---|---|---|
| `GET` | `/omnigent-ui/workflow-chat/{chatBindingId}` | Serve the authorized native Omnigent Chat application in embedded or full-page form. |
| `*` | `/api/workflow-chat-bindings/{chatBindingId}/omnigent/{path}` | Binding-scoped Omnigent-compatible HTTP, SSE, WebSocket, resource, and control facade. |

Every request to the scoped facade independently authenticates the caller, resolves the durable binding, authorizes the requested operation against the Workflow Execution, validates expected session state, recomputes effective capabilities, applies required outbound security scans, records mutation audit evidence, strips MoonMind credentials, and forwards only to the server-resolved upstream target.

A browser cannot gain authority by replacing `chatBindingId`, inserting another provider session id in `{path}`, changing an upstream URL, or invoking a native control that the UI hid. The server rejects any request that does not map exactly to the authorized binding and capability set.

### 2.4 Managed-run observability (`/api/agent-runs`)

These endpoints expose artifact-backed observability for managed runs. The legacy `/live-session*` family is not part of the active API.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/agent-runs/{agentRunId}/observability-summary` | Get observability metadata and artifact refs. |
| `GET` | `/api/agent-runs/{agentRunId}/logs/stream` | Stream active live logs over SSE when supported. |
| `GET` | `/api/agent-runs/{agentRunId}/logs/stdout` | Read the stdout log artifact. |
| `GET` | `/api/agent-runs/{agentRunId}/logs/stderr` | Read the stderr log artifact. |
| `GET` | `/api/agent-runs/{agentRunId}/logs/merged` | Read the merged log view or synthesized fallback. |
| `GET` | `/api/agent-runs/{agentRunId}/diagnostics` | Read the diagnostics artifact. |

## 3. Identity model

MoonMind uses related but distinct identifiers around Workflow Executions:

- **`workflowId`** — canonical durable execution identity and `/api/executions` route key,
- **`runId`** — current/latest Temporal run identity,
- **`agentRunId`** — managed/external run observability identity,
- **`chatBindingId`** — opaque browser-safe authorization handle for one Workflow-to-native-session binding.

Provider session, bridge session, host, runner, endpoint, credential, and immutable profile-snapshot identities remain server-side unless an authorized diagnostic contract exposes bounded safe refs.

The normal control-plane flow is:

1. create or list work through `/api/executions`,
2. use `workflowId` for lifecycle actions and detail fetches,
3. read the Step ledger from `/api/executions/{workflowId}/steps`,
4. resolve managed-run observability through the relevant `agentRunId`,
5. resolve native Chat only through `/api/executions/{workflowId}/chat-binding`,
6. use the returned scoped `chatUrl` and API base without authoring provider identity.

## 4. Observability behavior

The observability routes are artifact-first:

- `observability-summary` reports whether live follow is appropriate,
- `logs/stream` is the active-run SSE live-follow endpoint,
- `logs/stdout`, `logs/stderr`, and `logs/merged` remain available historically,
- `diagnostics` exposes persisted supervision evidence.

Full log bodies and diagnostics come from managed-run artifact storage and spool files, not workflow history or raw Temporal event history.

The Omnigent Bridge separately retains raw and normalized event journals, snapshots, resources, manifests, diagnostics, mutation audit refs, and terminal evidence. The native Chat application presents live state; MoonMind artifacts remain durable workflow evidence.

## 4.1 Recovery behavior

Failed `MoonMind.UserWorkflow` executions may expose failed-Step recovery when the source run has an original input snapshot, recovery checkpoint ref, plan identity, workspace checkpoint evidence, and completed prior-Step output refs.

The default `recover-from-failed-step` route preserves the original input and starts new execution at the recorded failed Step. The `recover-from-selected-step` route is for intentional earlier recovery: the request includes the source `workflowId`, source `runId`, and `selectedStartStepId`. The service validates those values against canonical source execution and checkpoint evidence before creating linked work.

Recovery routes do not accept edited instructions, attachments, runtime/model settings, dependency changes, or publish changes. Operators use edit/rerun flows for those behaviors.

## 4.2 Workflow Chat behavior

The ordinary active-session path is:

```text
native Omnigent composer
  -> binding-scoped MoonMind facade
  -> authorized provider session event
```

The facade enforces:

- per-request Workflow and binding authorization,
- session-id and upstream-target non-substitution,
- immutable Agent Profile, Provider Profile, launch-policy, workflow-state, and caller-permission capabilities,
- actor, idempotency, expected-state, outcome, and durable audit requirements for mutations,
- `MOONMIND_HIGH_SECURITY_MODE` outbound scans before forwarding text-bearing events,
- redaction and credential separation.

Ordinary native chat does not call `SubmitChatInstruction`, revise plans, supersede Steps, create Step Execution attempts, or create follow-up Workflow Executions.

The optional `/api/executions/{workflowId}/chat-instructions` contract and `SubmitChatInstruction` Temporal Update are reserved for a future separately labeled **Steer Workflow** action. They are deferred and are not compatibility aliases for the native message path.

For terminal work, **Continue in a new workflow** uses the explicit linked-continuation route and authorized source evidence. A terminal transcript remains read-only.

## 5. Request model posture

`POST /api/executions` is the active create surface. It accepts the execution-oriented request model and may normalize legacy task-shaped payloads only where the repository's explicit migration contract still requires it.

Execution requests dispatch into `MoonMind.UserWorkflow` or another allowed workflow type, then fan out across Temporal worker fleets grouped by capability and security boundary:

| Fleet | Task Queue | Capabilities | Purpose |
|---|---|---|---|
| `workflow` | `mm.workflow` | Workflow orchestration | Workflow code only; no side effects. |
| `artifacts` | `mm.activity.artifacts` | Artifact I/O, provider profiles | I/O-bound artifact storage and metadata. |
| `llm` | `mm.activity.llm` | LLM calls, plan generation, and review gates | Rate-limited by provider quotas. |
| `sandbox` | `mm.activity.sandbox` | Repo checkout, patch, tests, commands | CPU/memory heavy; strict concurrency limits. |
| `integrations` | `mm.activity.integrations` | External provider calls | Protected with rate limiting and circuit breakers. |
| `agent_runtime` | `mm.activity.agent_runtime` | Supervised agent execution, artifact publish | Long-lived runtime executions. |

`activity_catalog.py` defines the full routing contract including per-activity timeout and retry policies.

## 6. Legacy queue posture

The legacy `/api/queue/jobs` lifecycle routes and `/api/queue` worker callback routes are historical migration references only. They are not the active Workflow Execution lifecycle API, and the execution router rejects fallback to the old queue substrate when Temporal submission is disabled.

## 7. Related documentation

- [../UI/WorkflowChatPanel.md](../UI/WorkflowChatPanel.md) — native-primary Workflow Detail Chat and its binding/security contract
- [../Omnigent/OmnigentBridge.md](../Omnigent/OmnigentBridge.md) — session/event/resource facade, evidence, and request authority
- [ChatInstructionIntervention.md](ChatInstructionIntervention.md) — deferred explicit workflow-steering extension
- [../Api/ExecutionsApiContract.md](../Api/ExecutionsApiContract.md) — direct execution lifecycle contract
- [WorkflowArchitecture.md](WorkflowArchitecture.md) — overall Workflow system design
- [../UI/WorkflowConsoleArchitecture.md](../UI/WorkflowConsoleArchitecture.md) — Workflow-oriented UI over execution APIs
- [FollowUpWorkSystem.md](FollowUpWorkSystem.md) — explicit follow-up work boundaries
- [WorkflowCancellation.md](WorkflowCancellation.md) — cancellation flow
- [../Observability/LiveLogs.md](../Observability/LiveLogs.md) — managed-run log and observability design
- [../Temporal/TemporalArchitecture.md](../Temporal/TemporalArchitecture.md) — Temporal infrastructure
