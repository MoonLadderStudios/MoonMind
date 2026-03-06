# Contract: Temporal Artifact Presentation

## 1. Canonical detail routing

| Concern | Contract |
| --- | --- |
| Canonical route | `/tasks/:taskId` |
| Temporal route identity | For `source=temporal`, `taskId == workflowId` |
| Stable rerun behavior | `workflowId` remains the route handle even when `temporalRunId` changes |
| Optional aliases | Secondary aliases may exist for debug/compatibility, but must redirect/canonicalize back to `/tasks/:taskId` |

The route shell must accept safe Temporal workflow IDs such as `mm:...` and must not require run IDs in the URL.

## 2. Detail fetch sequence

### Request order

1. `GET /api/executions/{workflowId}`
2. `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`

### Required behavior

- The artifact-list request must use the latest `temporalRunId` from the execution detail response.
- The dashboard must not use a stale run ID cached from an earlier list row.
- If execution detail does not provide `namespace`, `workflowId`, or `temporalRunId`, the artifact section falls back to an empty latest-run view rather than guessing a mixed-run scope.

## 3. Detail presentation model

The default Temporal detail page must remain task-oriented and include:

- summary/title
- normalized status
- waiting context when applicable
- synthesized execution timeline
- latest-run artifact table

Secondary/debug metadata may include:

- `workflowId`
- `temporalRunId`
- `namespace`
- `workflowType`
- `temporalStatus`
- `closeStatus`

The default detail experience must not render raw Temporal event-history JSON as the primary view.

## 4. Artifact presentation contract

### Input metadata consumed by the dashboard

| Field | Required | Usage |
| --- | --- | --- |
| `artifact_id` | Yes | Stable display/access identifier |
| `status` | Yes | Render completion state |
| `content_type` | No | Fallback display metadata |
| `size_bytes` | No | Fallback display metadata |
| `links[]` | No | Preferred label and link semantics |
| `preview_artifact_ref` | No | Preferred preview target |
| `default_read_ref` | No | Preferred display/read metadata |
| `raw_access_allowed` | Yes | Governs raw download eligibility |

### Derived presentation rules

| Scenario | Required UI result |
| --- | --- |
| `preview_artifact_ref` exists | Show `Open preview` action first |
| `raw_access_allowed=true` and no preview | Show `Download` |
| `preview_artifact_ref` exists and `raw_access_allowed=true` | Show `Open preview` and `Download raw` |
| `raw_access_allowed=false` and preview exists | Show preview only and note raw restriction |
| `raw_access_allowed=false` and no preview exists | Show no access action and note `No safe preview` |

Other rules:

- Prefer artifact labels from execution linkage metadata when available.
- Do not assume a renderable MIME type is safe for inline raw display.
- Artifact edits from task flows must create new immutable references rather than modifying existing bytes in place.

## 5. Artifact access actions

### Access URL resolution

Preferred access flow:

1. `POST /api/artifacts/{artifactId}/presign-download`
2. Fallback: authorized runtime download endpoint if the response does not include a direct URL

### Browser behavior

- `preview` actions may open a new window/tab with the authorized URL.
- `download` actions may navigate the browser to the authorized URL.
- Failures must surface task-dashboard feedback rather than silent no-op behavior.

## 6. Run-scope policy

- The default artifact table is scoped to the latest run only.
- Prior-run browsing is out of scope for the default view and must not be silently mixed into the main artifact list.
- If prior-run support is added later, it must be an explicit user-selected mode with distinct UI state.

## 7. Task-oriented controls and submit posture

| Concern | Contract |
| --- | --- |
| Action labels | Remain task-oriented |
| Backend action mapping | Uses execution update/signal/cancel endpoints |
| Submit posture | Remains task-oriented and must not expose a visible Temporal runtime selector |
| Feature rollout | Actions/submit/debug fields may remain config-gated until supported |

Temporal is a dashboard source and execution substrate. It must not appear as a worker runtime option.
