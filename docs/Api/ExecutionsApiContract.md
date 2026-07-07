# Executions API Contract

**Project:** MoonMind 
**Doc type:** API contract 
**Status:** Draft 
**Owner:** MoonMind Platform 
**Last updated:** 2026-06-25 (UTC)
**Audience:** backend, dashboard, integrations

**Implementation tracking:** Rollout and backlog notes live under `docs/tmp/` or in gitignored local-only handoffs (for example `artifacts/`), not as migration checklists in canonical `docs/`.

---

## 1. Purpose

This document defines the contract for MoonMind's direct **Temporal-backed execution lifecycle API** under `/api/executions`.

It exists to make three things explicit:

1. the HTTP surface exposed by the API service,
2. the execution lifecycle semantics that callers can rely on,
3. how this execution-oriented surface relates to the workflow console UI and product routes.

This is an **adapter-first** contract: it describes lifecycle operations for Temporal-managed work. Product prioritization of `/api/executions` vs `/workflows/*` is a separate concern; open work is tracked in the file linked above.

---

## 2. Scope and posture

### 2.1 In scope

This document covers:

- authenticated lifecycle operations under `/api/executions`,
- request and response shapes,
- ownership and authorization rules,
- filtering, pagination, and count semantics,
- update/signal/cancel behavior for Temporal-managed executions,
- how callers should interpret identifiers when workflow product and execution-shaped surfaces coexist.

### 2.2 Out of scope

This document does **not** define:

- the `/workflows/*` product API,
- legacy compatibility routes,
- artifact upload/download APIs,
- direct Temporal server APIs,
- worker-internal lifecycle helpers used only inside workflow/activity execution.

### 2.3 Relationship to workflow product surfaces

- MoonMind presents the **workflow console** UI and workflow product APIs alongside this API.
- `/api/executions` is the **execution-oriented** surface for Temporal-managed work.
- Callers should treat `workflowId` as the canonical execution handle for this API.
- This contract should remain stable even if backing reads move closer to Temporal Visibility.

### 2.4 Current implementation note

As of this draft, the API is implemented via `TemporalExecutionService` and a `temporal_executions` projection row (`TemporalExecutionRecord`) used for lifecycle APIs and filtering.

That implementation detail is important for current behavior, but it is **not** the desired permanent public abstraction. The public contract in this doc should survive future implementation changes.

---

## 3. Related docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/SourceOfTruthAndProjectionModel.md`
- `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`
- `docs/Temporal/StepLedgerAndProgressModel.md`
- `docs/Temporal/WorkflowExecutionProductModel.md`
- `docs/UI/WorkflowConsoleArchitecture.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/UI/DashboardDesignSystem.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`

---

## 4. Vocabulary and identifiers

### 4.1 Terms

| Term | Meaning in this document |
| --- | --- |
| execution | A Temporal-managed MoonMind workflow execution exposed through `/api/executions` |
| workflow execution | The Temporal-native term for the same durable execution |
| task | Reserved for Temporal internals and qualified external systems; no longer a MoonMind product entity |
| workflow type | The root Temporal workflow category, e.g. `MoonMind.UserWorkflow` |
| run | A single Temporal run instance under a durable `workflowId` |

### 4.2 Identifiers

| Identifier | Meaning | Contract posture |
| --- | --- | --- |
| `workflowId` | Canonical durable execution identifier for this API | Primary |
| `runId` | Current run instance identifier | Detail/debug use |
| `taskId` | Legacy product identifier still present on some payloads (renames in the hard switch) | Out of scope for this API |
| `namespace` | Temporal namespace associated with the execution | Returned for detail/debugging |

### 4.3 ID rules

- `workflowId` is the primary path key for `/api/executions`.
- `runId` may change across Continue-As-New or rerun semantics.
- Clients must **not** treat `runId` as the durable identity of an execution.
- In surfaces that still expose the legacy `taskId` field, `taskId == workflowId` for Temporal-backed work, but this API does not expose `taskId` directly.

---

## 5. API surface summary

| Method | Path | Purpose | Success status |
| --- | --- | --- | --- |
| `POST` | `/api/executions` | Create/start a Temporal-backed execution | `201 Created` |
| `GET` | `/api/executions` | List executions visible to the caller | `200 OK` |
| `GET` | `/api/executions/{workflowId}` | Fetch one execution | `200 OK` |
| `POST` | `/api/executions/{workflowId}/update` | Apply a workflow update | `200 OK` |
| `POST` | `/api/executions/{workflowId}/signal` | Send an asynchronous signal | `202 Accepted` |
| `POST` | `/api/executions/{workflowId}/cancel` | Cancel or force-terminate an execution | `202 Accepted` |

### 5.1 Content type

All request and response bodies are JSON.

### 5.2 Field naming

External JSON fields use **camelCase**.

---

## 6. Authentication and authorization

### 6.1 Authentication

All `/api/executions` endpoints require an authenticated MoonMind user.

### 6.2 Ownership model

Execution ownership is derived from the authenticated user at create time.

Rules:

- non-admin callers may only list and access their own executions,
- admin callers may access executions across owners,
- ownership is enforced server-side and is not client-settable during creation.

### 6.3 Access control behavior

| Scenario | Result |
| --- | --- |
| Non-admin caller lists executions with no `ownerId` filter | Server implicitly scopes to caller |
| Non-admin caller provides `ownerId` matching caller | Allowed |
| Non-admin caller provides another user's `ownerId` | `403 Forbidden` |
| Non-admin caller requests another user's execution by `workflowId` | `404 Not Found` |
| Admin caller lists with or without `ownerId` | Allowed |
| Admin caller fetches any `workflowId` | Allowed |

### 6.4 Information disclosure posture

For direct fetch/update/signal/cancel operations, non-admin callers receive `404 Not Found` for executions they do not own. This intentionally avoids confirming whether another user's execution exists.

---

## 7. Workflow catalog and lifecycle model

### 7.1 Supported workflow types

The current allowed values for `workflowType` are:

- `MoonMind.UserWorkflow`
- `MoonMind.ManifestIngest`

### 7.2 Domain state model

The current allowed values for `state` are:

- `scheduled`
- `initializing`
- `waiting_on_dependencies`
- `planning`
- `awaiting_slot`
- `executing`
- `proposals`
- `awaiting_external`
- `finalizing`
- `no_commit`
- `completed`
- `failed`
- `canceled`

### 7.3 Returned Temporal status model

`temporalStatus` is a simplified lifecycle value returned by the API.

| `closeStatus` | Returned `temporalStatus` |
| --- | --- |
| `null` | `running` |
| `completed` | `completed` |
| `canceled` | `canceled` |
| `failed` | `failed` |
| `terminated` | `failed` |
| `timed_out` | `failed` |

`continued_as_new` is a real Temporal close concept, but the current API shape does not expose it directly as a distinct `temporalStatus` value.

---

## 8. Shared response model

### 8.1 `ExecutionModel`

`ExecutionModel` is the canonical materialized execution shape returned by create, describe, signal, and cancel, and nested within list responses.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `namespace` | string | yes | Temporal namespace for the execution |
| `workflowId` | string | yes | Durable execution identifier |
| `runId` | string | yes | Current run instance identifier |
| `workflowType` | string | yes | Current workflow type |
| `state` | string | yes | MoonMind domain lifecycle state |
| `temporalStatus` | `running \| completed \| failed \| canceled` | yes | Simplified lifecycle state |
| `closeStatus` | string or `null` | no | Terminal close status when closed |
| `agentRunId` | string or `null` | no | Managed-run observability binding when one top-level run is directly associated with the execution detail |
| `progress` | object or `null` | no | Lightweight execution progress summary; full step ledger is a separate read |
| `searchAttributes` | object | yes | Indexed execution metadata |
| `memo` | object | yes | Small display-oriented metadata |
| `artifactRefs` | string[] | yes | Artifact references linked to this execution |
| `startedAt` | datetime | yes | Initial execution timestamp |
| `queuedAt` | datetime or `null` | no | Queue-order timestamp used as the stable fallback within small updated-time buckets |
| `updatedAt` | datetime | yes | Last meaningful lifecycle/progress update |
| `closedAt` | datetime or `null` | no | Terminal close timestamp |

### 8.2 `ExecutionProgress`

`progress` is an execution-level summary object used for cheap detail polling.

Representative fields:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `total` | integer | yes | Total planned steps for the current/latest run |
| `pending` | integer | yes | Steps not yet ready |
| `ready` | integer | no | Ready-to-run steps when the executor distinguishes this state |
| `executing` | integer | yes | Active steps |
| `awaitingExternal` | integer | no | Steps waiting on external progress |
| `reviewing` | integer | no | Steps currently under structured review/check processing |
| `completed` | integer | yes | Successfully completed steps |
| `failed` | integer | yes | Failed steps |
| `skipped` | integer | no | Intentionally skipped steps |
| `canceled` | integer | no | Canceled steps |
| `currentStepTitle` | string or `null` | no | Operator-facing title for the most relevant active step |
| `updatedAt` | datetime | no | Last meaningful progress mutation |

Rules:

- `progress` must remain bounded and display-safe
- this object is not a substitute for step detail
- the authoritative detailed step surface is `GET /api/executions/{workflowId}/steps`

### 8.3 Search attributes and memo expectations

Current execution responses are expected to carry the following baseline metadata:

#### Search attributes

Required baseline keys:

- `mm_owner_type`
- `mm_owner_id`
- `mm_state`
- `mm_updated_at`
- `mm_entry`

Optional bounded keys may include values such as:

- `mm_repo`
- `mm_integration`
- `mm_target_runtime`
- `mm_target_skill`

`mm_target_runtime` and `mm_target_skill` are authoritative filter/facet fields
only after the Temporal namespace reports them registered as `KeywordList` Search
Attributes. Before registration, requests that depend on those fields degrade
without issuing invalid Temporal Visibility queries. Unknown values are omitted
rather than serialized as blank strings.

#### Memo

Expected baseline keys:

- `title`
- `summary`

Optional keys may include:

- `input_ref`
- `manifest_ref`
- other small display-safe values

This API returns these fields as opaque JSON objects. Clients may read documented keys, but must tolerate additional keys being added over time.

Current staging note:

- the current projection-backed implementation still returns projection-authored lifecycle state, but owner metadata now uses explicit `mm_owner_type` + `mm_owner_id` values instead of `"unknown"` placeholders

### 8.4 `ExecutionListResponse`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `items` | `ExecutionModel[]` | yes | Page of results |
| `nextPageToken` | string or `null` | no | Opaque pagination token |
| `count` | integer or `null` | no | Count for the filtered query |
| `countMode` | `exact \| estimated_or_unknown` | yes | Count confidence |
| `degradedCount` | boolean | yes | `true` when an exact count was unavailable |

Temporal-backed list reads return `countMode = "exact"` only when the page read
and the bounded count query both succeed. If the page read succeeds but the
count query fails or times out, the response keeps the rows and returns
`count = null`, `countMode = "estimated_or_unknown"`, and `degradedCount = true`.

---

## 9. Create execution

### 9.1 Endpoint

`POST /api/executions`

### 9.2 Request body

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `workflowType` | `MoonMind.UserWorkflow \| MoonMind.ManifestIngest` | yes | Root workflow type |
| `title` | string or `null` | no | Display title; defaulted if omitted |
| `inputArtifactRef` | string or `null` | no | Input artifact reference |
| `planArtifactRef` | string or `null` | no | Plan artifact reference |
| `manifestArtifactRef` | string or `null` | no | Required when `workflowType = MoonMind.ManifestIngest` |
| `failurePolicy` | `fail_fast \| continue_and_report \| best_effort` or `null` | no | Initial failure policy hint |
| `initialParameters` | object | no | Small JSON parameter payload |
| `idempotencyKey` | string or `null` | no | Create deduplication key |

### 9.3 Validation rules

- `workflowType` must be one of the supported values.
- `manifestArtifactRef` is required for `MoonMind.ManifestIngest`.
- `MoonMind.UserWorkflow` create requests must include at least one planning
  source before an execution is persisted or started: non-empty instructions,
  a selected skill, `inputArtifactRef`, or `planArtifactRef`.
- `initialParameters` should remain small and JSON-serializable.
- Artifact refs are references, not embedded blobs.

### 9.4 Idempotency

If `idempotencyKey` is present, create requests are deduplicated against the tuple:

- `ownerId`
- `workflowType`
- `idempotencyKey`

On duplicate create:

- the existing execution is returned,
- no new execution is created.

### 9.5 Create semantics

On successful create:

- a new `workflowId` is allocated,
- a current `runId` is allocated,
- `state` begins as `initializing`,
- baseline `searchAttributes` and `memo` are materialized,
- the response body is an `ExecutionModel`.

### 9.6 Success response

Status: `201 Created`

Example:

```json
{
 "namespace": "moonmind",
 "workflowId": "mm:3cf79b7f-0fc2-4ab4-a0f8-f2d8a65d8c4a",
 "runId": "84ee7f53-06c5-49e5-9f56-bb42f5d79f33",
 "workflowType": "MoonMind.UserWorkflow",
 "state": "initializing",
 "temporalStatus": "running",
 "closeStatus": null,
 "agentRunId": null,
 "progress": {
 "total": 0,
 "pending": 0,
 "executing": 0,
 "completed": 0,
 "failed": 0,
 "currentStepTitle": null
 },
 "searchAttributes": {
 "mm_owner_id": "<user-id>",
 "mm_state": "initializing",
 "mm_updated_at": "2026-03-06T12:00:00Z",
 "mm_entry": "run"
 },
 "memo": {
 "title": "Run",
 "summary": "Execution initialized."
 },
 "artifactRefs": [],
 "startedAt": "2026-03-06T12:00:00Z",
 "updatedAt": "2026-03-06T12:00:00Z",
 "closedAt": null
}
```

### 9.7 Error responses

| Status | Code | Meaning |
| --- | --- | --- |
| `422` | `invalid_execution_request` | Domain validation failure handled by the router |
| `422` | framework validation error | Malformed JSON/body schema failure before route logic |
| `401` / `403` | auth-layer specific | Authentication/authorization failure |

---

## 10. List executions

### 10.1 Endpoint

`GET /api/executions`

### 10.2 Query parameters

| Query parameter | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `workflowType` | string | no | none | Filter by workflow type |
| `state` | string | no | none | Filter by MoonMind domain state |
| `ownerType` | string | no | none | Admin-capable owner-type filter |
| `ownerId` | UUID | no | none | Admin-capable owner filter |
| `entry` | string | no | none | Filter by `mm_entry` |
| `repo` | string | no | none | Optional repo-scoped filter |
| `integration` | string | no | none | Optional integration filter |
| `targetRuntime` / `targetRuntimeIn` | string | no | none | Filter by canonical `mm_target_runtime` when registered |
| `targetSkillIn` | string | no | none | Filter by primary `mm_target_skill` when registered |
| `pageSize` | integer | no | `50` | Must be between `1` and `200` |
| `nextPageToken` | string | no | none | Opaque pagination token |

### 10.3 Filtering semantics

- `workflowType` filters on the root workflow type.
- `state` filters on MoonMind domain state.
- `ownerType`, `entry`, `repo`, and `integration` are supported exact-match filters.
- Runtime and skill filters use `mm_target_runtime` and the singular primary
  `mm_target_skill` Search Attribute only when the registry capability check
  succeeds. They are one-item `KeywordList` values and are filterable through
  membership queries, not sortable through Temporal Visibility.
- `ownerId` is optional for admins.
- Non-admin callers are implicitly scoped to themselves when `ownerId` is omitted.

### 10.4 Ordering semantics

The current default ordering contract is:

1. `updatedAt` descending by one-minute stability bucket
2. queued order descending (`queuedAt`, newest queued first) within the same updated-time bucket
3. `workflowId` descending as a deterministic tiebreaker

Meaningfully newer `updatedAt` values still sort first. Small updated-time differences inside the same bucket do not reorder rows ahead of newer queued executions.

### 10.5 Pagination semantics

- `nextPageToken` is opaque.
- Clients must treat the token as an uninterpreted cursor.
- A `null` token means there are no more pages.
- Current implementation uses offset-based pagination under the hood, but that structure is **not** part of the public contract.

### 10.6 Count semantics

The list response includes:

- `count`: current filtered total when known,
- `countMode`: `exact` or `estimated_or_unknown`,
- `degradedCount`: whether exact count enrichment failed.

Client rule:

- if `countMode != exact`, clients must not present a precise total or page count as authoritative

### 10.7 Success response

Status: `200 OK`

Example:

```json
{
 "items": [
 {
 "namespace": "moonmind",
 "workflowId": "mm:3cf79b7f-0fc2-4ab4-a0f8-f2d8a65d8c4a",
 "runId": "84ee7f53-06c5-49e5-9f56-bb42f5d79f33",
 "workflowType": "MoonMind.UserWorkflow",
 "state": "executing",
 "temporalStatus": "running",
 "closeStatus": null,
 "searchAttributes": {
 "mm_owner_id": "<user-id>",
 "mm_state": "executing",
 "mm_updated_at": "2026-03-06T12:05:00Z",
 "mm_entry": "run"
 },
 "memo": {
 "title": "Refactor proposal",
 "summary": "Execution resumed."
 },
 "artifactRefs": ["artifact://input/123"],
 "startedAt": "2026-03-06T12:00:00Z",
 "updatedAt": "2026-03-06T12:05:00Z",
 "closedAt": null
 }
 ],
 "nextPageToken": null,
 "count": 1,
 "countMode": "exact"
}
```

### 10.8 Error responses

| Status | Code | Meaning |
| --- | --- | --- |
| `403` | `execution_forbidden` | Non-admin attempted to list another user's executions |
| `422` | `invalid_pagination_token` | Malformed page token; current implementation may also surface some filter validation failures through this code path |
| `401` / `403` | auth-layer specific | Authentication/authorization failure |

---

## 11. Describe execution

### 11.1 Endpoint

`GET /api/executions/{workflowId}`

### 11.2 Path parameter

| Path parameter | Type | Required | Notes |
| --- | --- | --- | --- |
| `workflowId` | string | yes | Durable execution identifier |

### 11.3 Success response

Status: `200 OK`

Body: `ExecutionModel`

### 11.4 Error responses

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `execution_not_found` | Execution does not exist or is not visible to the caller |
| `401` / `403` | auth-layer specific | Authentication/authorization failure |

### 11.5 Describe execution steps

`GET /api/executions/{workflowId}/steps`

Purpose:

- return the latest/current run's step ledger
- keep `GET /api/executions/{workflowId}` lightweight
- expose step identity, status, attempts, checks, refs, and step-scoped artifact refs without forcing clients to parse generic logs

Representative response:

```json
{
 "workflowId": "mm:3cf79b7f-0fc2-4ab4-a0f8-f2d8a65d8c4a",
 "runId": "84ee7f53-06c5-49e5-9f56-bb42f5d79f33",
 "runScope": "latest",
 "steps": [
 {
 "logicalStepId": "run-tests",
 "order": 4,
 "title": "Run test suite",
 "tool": { "type": "skill", "name": "repo.run_tests", "version": "1" },
 "dependsOn": ["apply-patch"],
 "status": "executing",
 "waitingReason": null,
 "attentionRequired": false,
 "attempt": 1,
 "startedAt": "2026-04-04T18:10:00Z",
 "updatedAt": "2026-04-04T18:11:15Z",
 "timing": {
   "startedAt": "2026-04-04T18:10:00Z",
   "endedAt": null,
   "durationMs": null,
   "elapsedMs": 75000,
   "serverNow": "2026-04-04T18:11:15Z",
   "precision": "live",
   "preserved": false
 },
 "summary": "Executing tests in sandbox",
 "checks": [],
 "refs": {
 "childWorkflowId": null,
 "childRunId": null,
 "agentRunId": null
 },
 "artifacts": {
 "outputSummary": null,
 "outputPrimary": null,
 "runtimeStdout": null,
 "runtimeStderr": null,
 "runtimeMergedLogs": null,
 "runtimeDiagnostics": null,
 "providerSnapshot": null
 },
 "lastError": null
 }
 ]
}
```

Per-step required fields:

- `logicalStepId`
- `order`
- `title`
- `tool`
- `dependsOn`
- `status`
- `waitingReason`
- `attentionRequired`
- `attempt`
- `startedAt`
- `updatedAt`
- `timing.startedAt`
- `timing.endedAt`
- `timing.durationMs`
- `timing.elapsedMs`
- `timing.serverNow`
- `timing.precision`
- `timing.preserved`
- `summary`
- `checks[]`
- `refs.childWorkflowId`
- `refs.childRunId`
- `refs.agentRunId`
- `artifacts.outputSummary`
- `artifacts.outputPrimary`
- `artifacts.runtimeStdout`
- `artifacts.runtimeStderr`
- `artifacts.runtimeMergedLogs`
- `artifacts.runtimeDiagnostics`
- `artifacts.providerSnapshot`
- `lastError`

Rules:

- the default response is for the latest/current run only
- `logicalStepId` comes from the plan node and is stable within that plan
- `attempt` is scoped to `(workflowId, runId, logicalStepId)`
- `checks[]` is the structured place for review/check verdicts and retry summaries
- `agentRunId` may appear on a step row even when the top-level execution detail also exposes a managed-run binding
- `timing` is the row-level logical step timing object consumed by the Workflow Details step ledger; it is separate from runner workload timing
- `timing.precision` is one of `exact`, `live`, `fallback`, or `unavailable`; `live` values are elapsed as of `serverNow`, and `fallback` values are displayable but not exact terminal evidence
- preserved rows use `timing.preserved: true` and the dashboard labels the value as original timing rather than newly executed work
- clients must not infer logical step duration from `workload.durationSeconds`; workload duration is runner-level metadata

The Step Execution history endpoint, `GET /api/executions/{workflowId}/steps/{logicalStepId}/step-executions`, returns the same `timing` object on each attempt projection. The expanded dashboard history shows per-attempt timing and may derive a total across attempts from those attempt-local values.

### 11.6 Step-route error responses

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `execution_not_found` | Execution does not exist or is not visible to the caller |
| `401` / `403` | auth-layer specific | Authentication/authorization failure |

---

## 12. Update execution

### 12.1 Endpoint

`POST /api/executions/{workflowId}/update`

### 12.2 Request body

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `updateName` | `UpdateInputs \| SetTitle \| RequestRerun` | no | Defaults to `UpdateInputs` |
| `inputArtifactRef` | string or `null` | no | Candidate updated input ref |
| `planArtifactRef` | string or `null` | no | Candidate updated plan ref |
| `parametersPatch` | object or `null` | no | Small JSON patch |
| `title` | string or `null` | no | Required when `updateName = SetTitle` |
| `idempotencyKey` | string or `null` | no | Update idempotency key |

### 12.3 Supported updates

#### `UpdateInputs`

Purpose:

- replace or add input refs,
- replace or add plan refs,
- patch small execution parameters.

Behavior:

- no-op updates succeed with `accepted = true`,
- if the execution is currently `executing` or `awaiting_external`, the update may be accepted for the **next safe point**,
- large semantic changes may be applied through Continue-As-New.

#### `SetTitle`

Purpose:

- update the display title in `memo.title`.

Behavior:

- applied immediately,
- requires `title`.

#### `RequestRerun`

Purpose:

- request a clean re-execution using current or replacement refs/parameters.

Behavior:

- currently modeled as Continue-As-New,
- preserves `workflowId`,
- allocates a new `runId`.
- rerun-from-terminal is not currently implemented as a special exception; terminal executions still follow the general non-accepted update response described below.

### 12.4 Response body

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `accepted` | boolean | yes | Whether the update was accepted |
| `applied` | `immediate \| next_safe_point \| continue_as_new` | yes | Application timing |
| `message` | string | yes | Human-readable outcome |

### 12.5 Idempotency

The current update idempotency behavior is intentionally narrow:

- if `idempotencyKey` matches the execution's **most recent** update idempotency key,
- the most recent cached update response is returned.

This is **not** a general historical deduplication ledger. Callers should not assume arbitrary old update keys will be replayable forever.

### 12.6 Terminal execution behavior

If the execution is already terminal, the current contract is:

- return `200 OK`,
- body contains `accepted = false`,
- `applied = "immediate"`,
- `message` explains that the workflow no longer accepts updates.

### 12.7 Success response

Status: `200 OK`

Example:

```json
{
 "accepted": true,
 "applied": "next_safe_point",
 "message": "Update accepted and will be applied at the next safe point."
}
```

### 12.8 Error responses

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `execution_not_found` | Execution does not exist or is not visible to the caller |
| `422` | `invalid_update_request` | Domain validation failure |
| `422` | framework validation error | Malformed JSON/body schema failure before route logic |
| `401` / `403` | auth-layer specific | Authentication/authorization failure |

---

## 13. Signal execution

### 13.1 Endpoint

`POST /api/executions/{workflowId}/signal`

### 13.2 Request body

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `signalName` | `ExternalEvent \| Approve \| Pause \| Resume` | yes | Supported signal name |
| `payload` | object | no | Signal-specific payload; defaults to `{}` |
| `payloadArtifactRef` | string or `null` | no | Optional artifact ref associated with the signal |

### 13.3 Supported signals

#### `ExternalEvent`

Purpose:

- deliver webhook-like or integration-originated async events.

Required payload fields:

- `source`
- `event_type`

Behavior:

- may attach `payloadArtifactRef` to `artifactRefs`,
- records the integration event,
- clears the external wait only when the execution is not paused,
- updates the execution summary.

#### `Approve`

Purpose:

- deliver a human or policy approval signal.

Required payload fields:

- `approval_type`

Behavior:

- clears pause/external wait flags,
- moves execution back to `executing`.

#### `Pause`

Purpose:

- pause automatic progress for a non-terminal execution.

Behavior:

- sets `paused = true`,
- preserves the underlying lifecycle `state`,
- records operator pause waiting metadata so product surfaces can display the paused overlay.

#### `Resume`

Purpose:

- resume a paused execution.

Behavior:

- clears `paused` and operator pause waiting metadata,
- preserves the underlying lifecycle `state` so scheduled, dependency-waiting, slot-waiting, and active executions resume from the correct gate.

### 13.4 Success response

Status: `202 Accepted`

Body: `ExecutionModel`

The returned execution body reflects the post-signal materialized state.

### 13.5 Terminal execution behavior

Signals are rejected for terminal executions.

### 13.6 Error responses

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `execution_not_found` | Execution does not exist or is not visible to the caller |
| `409` | `signal_rejected` | Invalid signal name, missing required signal payload fields, or terminal-state rejection |
| `422` | framework validation error | Malformed JSON/body schema failure before route logic |
| `401` / `403` | auth-layer specific | Authentication/authorization failure |

---

## 14. Cancel execution

### 14.1 Endpoint

`POST /api/executions/{workflowId}/cancel`

### 14.2 Request body

The request body is optional.

| Field | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `reason` | string or `null` | no | `null` | Human-readable cancellation reason |
| `graceful` | boolean | no | `true` | Chooses cancel vs forced termination behavior |

### 14.3 Cancel semantics

#### Graceful cancel (`graceful = true`)

Behavior:

- clears pause/external wait flags,
- moves state to `canceled`,
- sets `closeStatus = canceled`,
- records the provided reason or a default message.

#### Forced termination (`graceful = false`)

Behavior:

- clears pause/external wait flags,
- moves state to `failed`,
- sets `closeStatus = terminated`,
- prefixes the summary with `forced_termination:`.

### 14.4 Terminal execution behavior

If the execution is already terminal, the current execution model is returned unchanged.

### 14.5 Success response

Status: `202 Accepted`

Body: `ExecutionModel`

### 14.6 Error responses

| Status | Code | Meaning |
| --- | --- | --- |
| `404` | `execution_not_found` | Execution does not exist or is not visible to the caller |
| `401` / `403` | auth-layer specific | Authentication/authorization failure |

---

## 15. Error model

### 15.1 Structured domain error shape

When the router raises a structured domain error, the response body uses this shape:

```json
{
 "detail": {
 "code": "some_error_code",
 "message": "Human-readable explanation"
 }
}
```

### 15.2 Known domain error codes

| Code | Typical status | Meaning |
| --- | --- | --- |
| `execution_not_found` | `404` | Execution is missing or caller cannot see it |
| `execution_forbidden` | `403` | Caller attempted an unauthorized list scope |
| `invalid_execution_request` | `422` | Create request failed domain validation |
| `invalid_update_request` | `422` | Update request failed domain validation |
| `invalid_pagination_token` | `422` | List request token invalid; currently may also surface some filter validation failures |
| `signal_rejected` | `409` | Signal request rejected by current lifecycle rules |

### 15.3 Framework validation errors

Malformed request bodies, wrong JSON types, and invalid query/path coercions may also return FastAPI/Pydantic validation errors before route logic executes. Those responses are part of the HTTP behavior, but are not the stable domain-specific error contract.

---

## 16. Compatibility with `task`-typed payloads

`/api/executions` is execution-oriented. Clients submitting the legacy `task`-typed payload envelope may still exist; when they map to Temporal-backed work:

- `workflowId` is the canonical Temporal execution identity,
- adapters may transform execution responses into `task`-typed payloads,
- for `task`-typed create and promotion payloads, `task.tool` / `step.tool` are canonical while `task.skill` / `step.skill` remain compatibility aliases where supported,
- when a tool selector is present on Temporal-backed `task`-typed submit payloads, `task.tool.type` must be `skill`,
- surfaces that still expose `taskId` preserve `taskId == workflowId` for Temporal-backed work.

The JSON shapes in this document should remain stable even if the backing implementation shifts among projection, Visibility, or mixed adapters.

---

## 17. Current implementation notes (non-contract)

These notes reflect the current repository behavior and help explain why the contract looks the way it does.

### 17.1 Current backing store

The current implementation materializes execution rows in `temporal_executions` with fields including:

- identifiers (`workflow_id`, `run_id`, `namespace`),
- workflow metadata (`workflow_type`, `entry`),
- lifecycle state (`state`, `close_status`),
- search/display metadata (`search_attributes`, `memo`),
- linked refs (`artifact_refs`, `input_ref`, `plan_ref`, `manifest_ref`),
- update bookkeeping (`pending_parameters_patch`, idempotency keys),
- operational counters (`step_count`, `wait_cycle_count`, `rerun_count`),
- timestamps (`started_at`, `updated_at`, `closed_at`).

### 17.2 Continue-As-New behavior

Current service behavior uses Continue-As-New-style semantics for:

- `RequestRerun`,
- some major `UpdateInputs` changes,
- lifecycle threshold rollover.

In those cases, callers should expect:

- stable `workflowId`,
- new `runId`,
- refreshed non-terminal lifecycle state.

### 17.3 Current list/count staging behavior

The current router returns:

- projection-backed pagination tokens,
- projection-backed exact counts,
- Temporal-backed rows when `source=temporal`, with exact counts treated as
  bounded best-effort enrichment.

Temporal-backed list reads preserve rows when count enrichment fails; only the
page fetch itself is a page-load prerequisite.

### 17.4 Known cleanup candidates

The current list endpoint uses the domain error code `invalid_pagination_token` for malformed page tokens and may also surface some filter validation failures through that same route-level error wrapper. That naming should be cleaned up later, but this draft documents the current behavior honestly.

---

## 18. Change rules

Any future change to this contract must explicitly call out whether it is:

1. a **backward-compatible additive change**,
2. a **behavioral cleanup** that keeps payload shape stable,
3. a **breaking contract change** requiring coordinated API and client updates.

Examples of changes that require a contract update:

- adding or removing supported `workflowType` values,
- changing accepted `updateName` or `signalName` values,
- changing error status/code behavior,
- changing list ordering semantics,
- changing whether `count` is exact or estimated,
- changing the meaning of `workflowId` vs `runId`.

---

## 19. Summary

`/api/executions` is MoonMind's current direct execution lifecycle surface for Temporal-managed work.

Its contract is:

- execution-oriented, not queue-oriented,
- authenticated and ownership-scoped,
- grounded in `workflowId` as the durable handle,
- explicit about update/signal/cancel semantics,
- usable alongside the workflow console product surfaces; it is not inherently a replacement for every `/workflows/*` flow.
