# Runtime Contract: Temporal Activity Catalog and Worker Topology

## 1. Canonical v1 task queues

| Queue | Fleet | Purpose |
|---|---|---|
| `mm.workflow` | `workflow` | Workflow-task execution only |
| `mm.activity.artifacts` | `artifacts` | Artifact lifecycle operations |
| `mm.activity.llm` | `llm` | Planning and default LLM-backed skill execution |
| `mm.activity.sandbox` | `sandbox` | Isolated repo, command, patch, and test execution |
| `mm.activity.integrations` | `integrations` | Provider-backed external operations |

## 2. Worker fleet contract

| Fleet | Queues | Required capabilities | Privileges | Forbidden capabilities |
|---|---|---|---|---|
| `workflow` | `mm.workflow` | `workflow` | Temporal connection only | Artifact blob IO, LLM calls, shell execution, provider secrets |
| `artifacts` | `mm.activity.artifacts` | `artifacts` | Artifact store credentials, metadata DB access | Arbitrary shell execution, provider secrets |
| `llm` | `mm.activity.llm` | `llm` | LLM provider credentials | Sandbox shell execution, provider-webhook secrets |
| `sandbox` | `mm.activity.sandbox` | `sandbox` | Isolated process/container execution only | LLM keys, integration provider tokens |
| `integrations` | `mm.activity.integrations` | `integration:<provider>` | Provider tokens, webhook verification secrets | Arbitrary shell execution |

## 3. Canonical activity families

### 3.1 Artifact family

- `artifact.create`
- `artifact.write_complete`
- `artifact.read`
- `artifact.list_for_execution`
- `artifact.compute_preview`
- `artifact.link`
- `artifact.pin`
- `artifact.unpin`
- `artifact.lifecycle_sweep`

Contract rules:

- Request and result payloads use artifact references and compact metadata.
- `write_complete` is retry-safe through integrity verification.
- Restricted artifacts may expose preview artifacts instead of raw bytes.

### 3.2 Planning family

- `plan.generate`
- `plan.validate`

Contract rules:

- Plans are stored as artifacts, not inlined in workflow history.
- `plan.validate` is the authoritative deep-validation gate.
- v1 planning routes through `mm.activity.llm` unless a non-LLM planner is explicitly introduced later.

### 3.3 Skill family

- `mm.skill.execute`
- curated explicit activity types declared by the registry only when an operational reason exists

Contract rules:

- Default path is `mm.skill.execute`.
- Explicit binding requires one declared reason:
  - `stronger_isolation`
  - `specialized_credentials`
  - `clearer_routing`
- Missing or unsupported binding reasons are validation failures.

### 3.4 Sandbox family

- `sandbox.checkout_repo`
- `sandbox.run_command`
- `sandbox.apply_patch`
- `sandbox.run_tests`

Contract rules:

- Sandbox work is isolated, cancellation-aware, and resource-limited.
- Long-running operations emit heartbeats with progress metadata.
- Workspace refs are idempotent and retry-safe.

### 3.5 Integration family

- `integration.jules.start`
- `integration.jules.status`
- `integration.jules.fetch_result`

Contract rules:

- Provider APIs stay behind adapter interfaces.
- Start operations reuse external identity for the same idempotency key.
- Callback-first workflow coordination is preferred; bounded polling fallback is allowed.

## 4. Shared envelope contract

### Request fields

- `correlation_id`
- `idempotency_key` for side-effecting operations
- `input_refs[]`
- `parameters`

### Response fields

- `output_refs[]`
- `summary`
- `metrics` (optional)
- `diagnostics_ref` (optional)

### Context-derived fields

- `workflow_id`
- `run_id`
- `activity_id`
- `attempt`

Contract rules:

- Large content must move through artifacts.
- Activities must not require duplicated workflow/runtime identifiers as business payload fields by default.
- Activities do not update Search Attributes or Memo directly.

## 5. Routing contract

- Routing is capability-based per activity invocation.
- Workflow type must not decide which activity queue is used.
- v1 provider selection for LLM-backed work happens inside the LLM worker, not via provider-specific task queues.
- Priority lanes are deferred for v1.

## 6. Timeout, retry, and heartbeat contract

| Family | Timeout profile | Retry profile | Heartbeat |
|---|---|---|---|
| `artifact.*` | short | bounded retry-safe retries | optional |
| `plan.generate` | moderate | bounded backoff | optional |
| `plan.validate` | short-to-moderate | bounded, invalid-input non-retryable | optional |
| `mm.skill.execute` | policy-driven | policy-driven | capability-dependent |
| `sandbox.*` | long | carefully bounded | required for long-running operations |
| `integration.*` | short per call | bounded backoff | optional; prefer workflow-timer polling over monolithic long activities |

## 7. Security contract

- Sandbox workers never receive provider credentials.
- Integration workers never execute arbitrary shell commands.
- LLM workers hold model/provider secrets only.
- Artifact storage remains private on the internal network in local/dev mode.
- Logs and previews are redacted before persistence or display.

## 8. Observability contract

Every activity summary/log context must include:

- `workflow_id`
- `run_id`
- `activity_type`
- `activity_id`
- `attempt`
- `correlation_id`
- hashed or redacted idempotency identity

Additional requirements:

- Large logs are stored as `output.logs` or `debug.trace` artifacts.
- Fleet metrics cover backlog, latency, retries, and family-specific behavior.
- Tracing propagates correlation and execution identifiers when enabled.
