# Agent Runtime Contracts (Phase 1)

## Canonical Status Vocabulary

`AgentRunStatus.status` must be one of:

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

Terminal states:

- `completed`
- `failed`
- `cancelled`
- `timed_out`

## Canonical Request/Result Shapes

### AgentExecutionRequest

- Required:
  - `agent_kind`, `agent_id`, `execution_profile_ref`
  - `correlation_id`, `idempotency_key`
- Optional:
  - `instruction_ref`, `input_refs[]`, `workspace_spec`
  - `parameters`, `timeout_policy`, `retry_policy`, `approval_policy`, `callback_policy`
- Contract rule:
  - Side-effecting start calls must reject empty idempotency identity.

### AgentRunHandle

- Required:
  - `run_id`, `agent_kind`, `agent_id`, `status`, `started_at`
- Optional:
  - `poll_hint_seconds`, callback correlation metadata

### AgentRunResult

- Required:
  - `output_refs[]`
- Optional:
  - `summary`, `metrics`, `diagnostics_ref`, `failure_class`, `provider_error_code`, `retry_recommendation`
- Contract rule:
  - Large payloads/logs/transcripts are represented by refs, not inline blobs.

### ManagedAgentAuthProfile

- Required:
  - `profile_id`, `runtime_id`, `auth_mode`, `max_parallel_runs`, `enabled`
- Optional:
  - `volume_ref`, `account_label`, `cooldown_after_429`, `rate_limit_policy`
- Contract rules:
  - No raw credentials fields.
  - Concurrency/cooldown policy is per profile and validation-enforced.

## AgentAdapter Interface

```text
start(request: AgentExecutionRequest) -> AgentRunHandle
status(run_id: str) -> AgentRunStatus
fetch_result(run_id: str) -> AgentRunResult
cancel(run_id: str) -> AgentRunStatus
```

Interface guarantees:

- Provider/runtime-specific payloads are normalized into canonical contracts.
- Start operation is idempotent under stable idempotency identity.
- Cancel returns normalized status semantics (including unsupported/ambiguous cases via canonical metadata where needed).
