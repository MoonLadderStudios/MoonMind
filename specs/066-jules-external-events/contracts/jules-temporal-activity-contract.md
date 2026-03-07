# Jules Temporal Activity Contract

## 1. Purpose

This contract defines the planned runtime-facing semantics for Jules-backed Temporal monitoring in MoonMind. It narrows the provider-neutral integration design into concrete Jules behavior without changing the existing shared Temporal vocabulary.

## 2. Shared Rules

- `integration_name` is always `jules`.
- `external_operation_id` is always the Jules `taskId`.
- Raw provider `status` is preserved as `provider_status`.
- Normalized status is limited to `queued`, `running`, `succeeded`, `failed`, `canceled`, or `unknown`.
- Default routing stays on `mm.activity.integrations`.
- Large provider payloads are artifact-backed, not returned inline.
- `callback_supported` defaults to `false` until a verified callback ingress exists.
- No provider-side cancellation is reported unless Jules exposes a real cancel API.

## 3. Activity Names

- `integration.jules.start`
- `integration.jules.status`
- `integration.jules.fetch_result`
- `integration.jules.cancel` (reserved, unsupported today)

## 4. `integration.jules.start`

### Purpose

Start a Jules task and return the compact provider handle plus monitoring hints.

### Semantic Input

- `correlation_id`
- `idempotency_key`
- `parameters.title`
- `parameters.description`
- `parameters.metadata` (optional, non-secret)
- `input_refs[]` or current `inputs_ref` support for artifact-backed description sourcing
- optional callback metadata only when callback support exists

### Current Runtime Mapping

- Source implementation: `moonmind/workflows/temporal/activity_runtime.py`
- Transport call: `JulesClient.create_task()`
- Schema source: `moonmind/schemas/jules_models.py`

### Required Behavior

- Reject blank correlation IDs when explicitly provided.
- Accept description from parameters or artifact-backed input, but fail closed when neither is available.
- Embed stable non-secret correlation/idempotency hints in provider metadata when useful.
- Derive provider-side idempotency from stable request identity, never from Temporal `run_id`.
- Preserve returned `status` and `url`.
- Optionally persist the provider response as a tracking artifact when artifact storage is available.

### Semantic Output

- `external_operation_id`
- `provider_status`
- `normalized_status`
- `callback_supported`
- `external_url` (optional)
- `tracking_ref` (implementation detail, optional)

## 5. `integration.jules.status`

### Purpose

Read current provider state during polling or reconciliation.

### Semantic Input

- `external_operation_id`

### Required Behavior

- Call the current Jules get-task adapter with the provider `taskId`.
- Preserve raw provider status and optional external URL.
- Reuse the shared Jules status normalizer.
- Treat the activity as read-only and aggressively retryable.
- Persist tracking artifacts only when artifact storage is available.
- Keep workflow-visible return data compact.

### Semantic Output

- `external_operation_id`
- `provider_status`
- `normalized_status`
- `terminal`
- `external_url` (optional)
- `tracking_ref` (implementation detail, optional)

## 6. `integration.jules.fetch_result`

### Purpose

Materialize terminal provider state into MoonMind artifacts.

### Required Behavior

- Reuse current status fetch behavior to obtain the latest task snapshot.
- Persist the terminal Jules task snapshot as an artifact.
- Persist a failure/unsupported-cancel summary artifact when relevant.
- Return artifact refs plus small summary data only.
- Do not assume the provider exposes logs, diff downloads, or rich output artifacts unless the adapter grows those contracts later.

### Semantic Output

- `output_refs[]`
- `summary` (optional)
- `diagnostics_ref` (optional)

## 7. `integration.jules.cancel`

### Current Posture

- Reserved activity name only.
- Provider-side cancellation is unsupported today.

### Required Behavior Once Implemented

- Must reflect real provider capability.
- Must not report provider cancellation success when no Jules cancel API exists.
- Must keep workflow-side cancellation truthful even when provider cancellation is unsupported.

## 8. Future `ExternalEvent` Callback Profile

Any future Jules callback ingress must map into the generic `ExternalEvent` signal contract with bounded Jules-specific metadata:

- `source = jules`
- `event_type`
- `external_operation_id`
- `provider_event_id` (optional)
- `provider_status` (optional)
- `normalized_status` (optional)
- `observed_at`
- `payloadArtifactRef` (optional)

Rules:

- Authenticate callbacks before signaling Temporal workflows.
- Dedupe on bounded provider event identity when available.
- Retain raw callback bodies only as restricted artifacts.
- Treat callback delivery as advisory until authenticity and correlation both succeed.

## 9. Artifact Classes

Minimum Temporal-backed Jules artifact classes:

- start request/response snapshot
- status snapshot
- terminal result snapshot
- failure summary
- completion/resolution summary when available
- raw callback payload (future)

## 10. Compatibility Rules

- MoonMind workflow/task identity remains the durable primary handle.
- Jules `taskId` is exposed separately as the provider handle.
- Compatibility rows may stay task-oriented, but they must not substitute the provider handle for MoonMind execution identity.
