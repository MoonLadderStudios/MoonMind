# Data Model: Agent Runtime Phase 1 Contracts

## 1. AgentExecutionRequest

- **Purpose**: Canonical input envelope for launching true agent runtime execution.
- **Core Fields**:
  - `agent_kind` (`external` | `managed`)
  - `agent_id` (provider/runtime identifier)
  - `execution_profile_ref` (indirect profile reference)
  - `correlation_id`
  - `idempotency_key`
  - `instruction_ref`
  - `input_refs[]`
  - `expected_output_schema`
  - `workspace_spec`
  - `parameters` (`model`, `effort`, `allowed_tools`, `publish_mode`, extra knobs)
  - `timeout_policy`, `retry_policy`, `approval_policy`, `callback_policy`
- **Validation Notes**:
  - `idempotency_key` required for start-like side effects.
  - Artifact/log-heavy inputs represented by refs, not inline large blobs.

## 2. AgentRunHandle

- **Purpose**: Start acknowledgement used for durable tracking.
- **Core Fields**:
  - `run_id`
  - `agent_kind`
  - `agent_id`
  - `status`
  - `started_at`
  - `poll_hint_seconds`
  - optional callback correlation metadata
- **Validation Notes**:
  - `run_id` and status are mandatory.
  - Status value must be canonical set member.

## 3. AgentRunStatus

- **Purpose**: Canonical lifecycle state snapshot.
- **Allowed States**:
  - `queued`
  - `launching`
  - `running`
  - `awaiting_callback`
  - `awaiting_approval`
  - `intervention_requested`
  - `collecting_results`
  - `completed`
  - `failed`
  - `cancelled`
  - `timed_out`
- **Derived Semantics**:
  - Terminal states: `completed`, `failed`, `cancelled`, `timed_out`.

## 4. AgentRunResult

- **Purpose**: Final result envelope for agent run completion.
- **Core Fields**:
  - `output_refs[]`
  - `summary`
  - `metrics`
  - `diagnostics_ref`
  - `failure_class`
  - `provider_error_code`
  - `retry_recommendation`
- **Validation Notes**:
  - `output_refs` and `diagnostics_ref` are references, not large inline data.

## 5. ManagedAgentAuthProfile

- **Purpose**: Named managed-runtime auth and execution policy.
- **Core Fields**:
  - `profile_id`
  - `runtime_id`
  - `auth_mode`
  - `volume_ref`
  - `account_label`
  - `max_parallel_runs`
  - `cooldown_after_429`
  - `rate_limit_policy`
  - `enabled`
- **Validation Notes**:
  - No raw credentials in payload.
  - Concurrency/cooldown are per profile and must be bounded positive values when enabled.

## 6. AgentAdapter Interface

- **Purpose**: Provider/runtime-neutral behavior contract.
- **Operations**:
  - `start(request) -> AgentRunHandle`
  - `status(run_id) -> AgentRunStatus`
  - `fetch_result(run_id) -> AgentRunResult`
  - `cancel(run_id) -> AgentRunStatus`
- **Validation Notes**:
  - `start` must be idempotent for repeated side-effecting calls with stable identity.
