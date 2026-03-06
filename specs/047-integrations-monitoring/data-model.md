# Data Model: Integrations Monitoring Design

## Entity: TemporalExecutionRecord Monitoring Projection

- **Description**: Existing execution lifecycle row in `temporal_executions` extended with integration-monitoring state and lifecycle metadata.
- **Fields**:
  - `workflow_id` (string, stable Temporal workflow identity)
  - `run_id` (string, changes on Continue-As-New)
  - `workflow_type` (enum, initial scope uses `MoonMind.Run`)
  - `state` (enum: `initializing`, `planning`, `executing`, `awaiting_external`, `finalizing`, `succeeded`, `failed`, `canceled`)
  - `search_attributes` (json object including canonical `mm_*` fields and bounded integration fields)
  - `memo` (json object with compact title/summary/safe display details)
  - `artifact_refs` (list of artifact references linked to execution lifecycle)
  - `integration_state` (nullable `ExternalOperationState`)
- **Rules**:
  - Initial delivery supports at most one active monitored external operation per execution record.
  - `workflow_id` survives Continue-As-New; `run_id` does not.
  - `integration_state` must remain compact and JSON-safe.

## Entity: ExternalOperationState

- **Description**: Compact workflow-resident state for one provider operation under active monitoring.
- **Fields**:
  - `integration_name` (string, normalized provider name such as `jules`)
  - `correlation_id` (string, stable MoonMind-generated identifier)
  - `external_operation_id` (string, provider handle)
  - `normalized_status` (enum: `queued`, `running`, `succeeded`, `failed`, `canceled`, `unknown`)
  - `provider_status` (string nullable, raw provider token)
  - `started_at` (datetime)
  - `last_observed_at` (datetime)
  - `monitor_attempt_count` (integer, non-negative)
  - `callback_supported` (boolean)
  - `result_refs` (list of artifact refs)
  - `callback_correlation_key` (string nullable)
  - `provider_event_ids_seen` (bounded list of strings, optional dedupe state)
  - `next_poll_at` (datetime nullable)
  - `poll_interval_seconds` (integer nullable)
  - `external_url` (string nullable)
  - `provider_summary` (small json object)
- **Rules**:
  - Required fields from the source design are mandatory.
  - `provider_event_ids_seen` must stay bounded.
  - Large or volatile provider payloads do not belong here; only artifact references do.
  - Terminal statuses are sticky for late non-terminal events.

## Entity: ProviderActivityContract

- **Description**: Provider-neutral contract executed by integrations workers for lifecycle side effects.
- **Operations**:
  - `integration.<provider>.start`
  - `integration.<provider>.status`
  - `integration.<provider>.fetch_result`
  - `integration.<provider>.cancel`
- **Common fields**:
  - `correlation_id` (string)
  - `idempotency_key` (string for create/start flows)
  - `external_operation_id` (string for status/fetch/cancel flows)
  - `normalized_status` / `provider_status`
  - `recommended_poll_seconds` (integer nullable)
  - `external_url` (string nullable)
  - `provider_summary` (small json object)
  - `artifact_refs` / `output_refs` / `diagnostics_ref` (artifact references)
- **Rules**:
  - `start` must be retry-safe and derive idempotency from stable identity, not `run_id`.
  - `status` is read-only and aggressively retryable.
  - `fetch_result` must be idempotent and artifact-backed.
  - `cancel` must report unsupported or ambiguous outcomes explicitly.

## Entity: CorrelationRecord

- **Description**: Durable callback-routing record stored outside workflow history.
- **Fields**:
  - `id` (UUID primary key)
  - `integration_name` (string)
  - `correlation_id` (string)
  - `callback_correlation_key` (string nullable)
  - `external_operation_id` (string nullable)
  - `workflow_id` (string)
  - `run_id` (string)
  - `lifecycle_status` (enum-like string: `active`, `succeeded`, `failed`, `canceled`)
  - `expires_at` (datetime nullable)
  - `created_at` / `updated_at` (datetime)
- **Rules**:
  - Must survive Continue-As-New by updating `run_id` while keeping stable correlation identity.
  - Primary lookup path for callbacks is `(integration_name, callback_correlation_key)`.
  - Correlation lookup must not depend on visibility scans by external operation ID.

## Entity: ExternalEventPayload

- **Description**: Small signal payload delivered to workflow logic after API validation succeeds.
- **Fields**:
  - `source` (string)
  - `event_type` (string)
  - `external_operation_id` (string nullable)
  - `provider_event_id` (string nullable)
  - `normalized_status` (enum nullable)
  - `provider_status` (string nullable)
  - `observed_at` (datetime nullable)
  - `external_url` (string nullable)
  - `provider_summary` (small json object)
  - `payload_artifact_ref` (string nullable)
- **Rules**:
  - Payload must stay small enough for workflow history.
  - Raw body bytes and detailed provider payloads must be referenced by artifact, not embedded inline.
  - Duplicate and stale events must be harmless under replay.

## Entity: PollingPolicy

- **Description**: Runtime policy that controls fallback polling cadence and long-wait handling.
- **Fields**:
  - `initial_interval_seconds` (integer, default near 5)
  - `recommended_interval_seconds` (integer nullable, provider hint)
  - `current_interval_seconds` (integer)
  - `max_interval_seconds` (integer)
  - `jitter_ratio` (float/configured policy)
  - `wait_cycle_threshold` (integer for Continue-As-New)
  - `last_reset_reason` (string nullable)
- **Rules**:
  - Provider guidance may adjust cadence when reasonable.
  - Poll interval resets on meaningful status changes.
  - Policy values are configuration-driven and bounded.

## Entity: IntegrationVisibilitySnapshot

- **Description**: Search-attribute and memo fields exposed to dashboard/API consumers while waiting on external work.
- **Fields**:
  - Search attributes:
    - `mm_owner_id`
    - `mm_state`
    - `mm_updated_at`
    - `mm_entry`
    - `mm_integration` (bounded optional)
    - `mm_stage` (bounded optional)
  - Memo:
    - `title`
    - `summary`
    - `external_url` (safe display field only)
    - `error_category` (nullable)
- **Rules**:
  - High-cardinality provider identifiers are excluded from default search attributes.
  - Memo remains compact and operator-readable.
  - Visibility is derived from canonical lifecycle fields, not a parallel integration dashboard model.

## Entity: ProviderFailureSummary

- **Description**: Compact operator-facing terminal error summary for failed, canceled, or ambiguous provider outcomes.
- **Fields**:
  - `integration_name` (string)
  - `correlation_id` (string)
  - `external_operation_id` (string)
  - `normalized_status` (enum)
  - `provider_status` (string nullable)
  - `summary` (string)
  - `diagnostics_ref` (artifact ref nullable)
  - `provider_cancel_accepted` (boolean nullable)
  - `error_category` (string, expected `integration_error` for provider-side failures)
- **Rules**:
  - Must be compact enough for operator display.
  - Detailed logs and provider responses stay in artifacts.

## Entity: ProviderProfile

- **Description**: Provider-specific normalization and capability mapping behind the shared monitoring contract.
- **Fields**:
  - `integration_name` (string)
  - `status_mapping` (map of provider status -> normalized status)
  - `callback_mode` (enum: `required`, `preferred`, `unsupported`)
  - `supports_cancel` (boolean)
  - `result_shape` (description of artifact/result contract)
  - `rate_limit_policy` (config metadata)
- **Rules**:
  - Jules is the first implemented profile.
  - Profiles must not change workflow semantics or create provider-specific root workflow types.

## Relationships

- One `TemporalExecutionRecord` may own zero or one active `ExternalOperationState` in the initial scope.
- One `ExternalOperationState` maps to one active `CorrelationRecord` and many artifact references.
- One `ProviderProfile` defines normalization/capability behavior for many `ExternalOperationState` instances.
- One terminal `ExternalOperationState` may emit zero or one `ProviderFailureSummary` plus one or many result artifacts.

## State Transitions

### Execution lifecycle

- `initializing -> planning|executing`
- `planning|executing -> awaiting_external` when integration monitoring starts
- `awaiting_external -> executing` when a non-terminal callback/poll progresses work or terminal success is ready for follow-up processing
- `awaiting_external -> finalizing -> succeeded|failed|canceled` after result fetch/failure summary/cancel closure
- `awaiting_external -> awaiting_external` across Continue-As-New with preserved monitoring identity and refreshed `run_id`

### External operation lifecycle

- `queued -> running`
- `queued|running|unknown -> succeeded|failed|canceled`
- `unknown -> queued|running` when provider status becomes clearer
- `succeeded|failed|canceled -> terminal` (late non-terminal events are ignored)

### Callback dedupe lifecycle

- unseen `provider_event_id` -> append to bounded `provider_event_ids_seen`
- repeated `provider_event_id` -> ignore without state corruption
- late non-terminal event after terminal status -> ignore and preserve terminal state

## Determinism and Security Rules

- Workflow-side state stores compact JSON and artifact refs only.
- Provider I/O, callback verification, filesystem access, and artifact writes happen outside deterministic workflow code.
- Secrets and raw provider payloads never enter memo or search attributes.
