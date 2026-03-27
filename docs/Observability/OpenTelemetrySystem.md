# OpenTelemetry System Design for MoonMind (Temporal-Idiomatic)

Status: Draft
Owners: MoonMind Platform
Last updated: 2026-03-26

## 1. Purpose

Define how MoonMind should implement OpenTelemetry (OTel) for logs, metrics, and traces in a way that is idiomatic to Temporal and aligned with MoonMind's architecture.

This design treats OTel as the **operational telemetry plane** for:
- API requests
- Temporal workers
- Activity execution
- provider calls
- artifact operations
- sandbox/tool execution
- integration calls

It does **not** replace:
- Temporal Visibility for list/query/count
- Mission Control for product-facing execution views
- artifact storage for large logs, outputs, traces, and run evidence

## 2. Related architecture assumptions

This design assumes the following MoonMind architecture choices are already locked:

- Temporal is the durable orchestration substrate.
- Workflow code must remain deterministic.
- Activities perform all side effects.
- Task queues are routing only, not product semantics.
- Artifacts hold large inputs/outputs/log blobs outside workflow history.
- Temporal Visibility is the source of truth for Temporal-backed list/query/count.

## 3. Goals

1. Provide end-to-end request-to-run observability using standard OpenTelemetry.
2. Preserve Temporal determinism and avoid non-deterministic telemetry calls from workflow code.
3. Correlate API requests, workflow executions, activities, provider calls, and artifacts.
4. Support self-hosted local/dev operation by default.
5. Keep telemetry vendor-neutral via OTLP.
6. Separate product execution visibility from operational telemetry.
7. Capture AI-specific metrics and spans without leaking secrets or large payloads.

## 4. Non-goals

- Replacing Temporal Web UI or Temporal Visibility.
- Storing large prompts, diffs, stdout/stderr, or artifacts in span attributes.
- Using OpenTelemetry spans as an execution source of truth.
- Treating logs/traces as user-facing state transitions.
- Emitting telemetry directly from deterministic workflow code to external systems.

## 5. Core principles

### 5.1 Temporal-idiomatic boundary

OpenTelemetry must respect Temporal's determinism model.

Rules:
- **Workflow code must not perform exporter I/O, make OTLP network calls, or depend on current wall-clock time for telemetry behavior.**
- Workflow instrumentation should happen through **Temporal client/worker interceptors**, not ad hoc span/export logic inside workflow bodies.
- Activities are the primary place where rich telemetry is created for side-effecting work.

### 5.2 Query plane vs telemetry plane

MoonMind must keep these separate:

- **Temporal Visibility**: list, query, count, filters, recency.
- **Mission Control**: task/workflow product UX, status, artifacts, approvals, execution summary.
- **OpenTelemetry**: traces, metrics, logs for operators and developers.
- **Artifacts**: large logs, sandbox output, command transcripts, prompt transcripts when explicitly enabled.

### 5.3 Stable execution identity

Because workflows may Continue-As-New, telemetry must distinguish:
- **stable execution identity**
- **current Temporal run instance**
- **current trace/span instance**

Use:
- `moonmind.correlation_id` = stable identity across Continue-As-New and reruns where policy says the execution is logically the same
- `temporal.workflow_id` = stable Temporal workflow handle
- `temporal.run_id` = current run instance
- `trace_id` / `span_id` = telemetry identifiers

### 5.4 Small attributes, large evidence elsewhere

Spans and metrics should contain:
- identifiers
- durations
- counts
- status
- bounded summaries

They must not contain:
- large prompts
- large completions
- raw repo diffs
- long sandbox logs
- presigned URLs
- secrets

Large evidence must be written to artifacts and linked by reference.

### 5.5 Vendor-neutral export

MoonMind should emit standard OTLP and allow operators to choose the backend:
- local/dev default: OpenTelemetry Collector + Grafana LGTM
- enterprise optional: Datadog, Honeycomb, Grafana Cloud, New Relic, Langfuse, Phoenix, etc.

## 6. High-level architecture

### 6.1 Components

1. **MoonMind API**
   - FastAPI request instrumentation
   - creates root traces for user/API actions
   - injects correlation data into workflow start/update/signal/cancel requests

2. **Temporal Client**
   - propagates trace context and execution identifiers when starting workflows or invoking updates/signals

3. **Workflow Worker**
   - uses Temporal interceptors for workflow/task execution visibility
   - creates minimal workflow-level spans/events through interceptor boundaries only

4. **Activity Workers**
   - create rich spans for actual side effects
   - emit metrics and structured logs
   - write large diagnostics to artifacts

5. **OpenTelemetry Collector**
   - receives OTLP traces/metrics/logs
   - enriches, batches, and exports
   - routes to local/default backends

6. **Observability Backends**
   - Tempo for traces
   - Prometheus for metrics
   - Loki for logs
   - Grafana for dashboards

7. **Mission Control**
   - remains a product surface
   - links to traces/logs/artifacts by identifiers
   - does not become the OTLP backend itself

### 6.2 Default deployment shape

Add to Docker Compose:
- `otel-collector`
- `prometheus`
- `loki`
- `tempo`
- `grafana`

All MoonMind services export OTLP to the collector on the internal network.

## 7. Signal model by layer

### 7.1 Traces

Best for:
- request path correlation
- workflow/activity timing
- provider call latency
- integration call latency
- sandbox command lifecycle
- retries and failures

### 7.2 Metrics

Best for:
- fleet throughput
- queue/fleet saturation
- retries/timeouts
- token/cost totals
- stuck/waiting counts
- approval latency
- sandbox execution latency
- provider health

### 7.3 Logs

Best for:
- operator debugging
- structured contextual events
- failure details
- bounded summaries
- worker lifecycle messages

Large logs become artifacts, not log lines.

## 8. Temporal-idiomatic instrumentation strategy

### 8.1 API layer

Instrument FastAPI using standard OTel HTTP middleware.

Each incoming request creates a root or entry span with attributes such as:
- `moonmind.request_kind`
- `moonmind.owner_type`
- `moonmind.owner_id` when available
- `moonmind.route`
- `moonmind.task_surface`
- `moonmind.correlation_id` if already known

Examples:
- `POST /api/executions`
- `POST /api/executions/{workflowId}/update`
- `POST /api/executions/{workflowId}/signal`
- `POST /api/executions/{workflowId}/cancel`

When the API starts a workflow or sends an update/signal/cancel request, it must pass execution correlation metadata so downstream traces and logs can be tied back to the request.

### 8.2 Temporal client instrumentation

Use Temporal client interceptors to:
- create spans around workflow start, signal, update, query, and cancel calls
- attach `workflow_id`, `workflow_type`, `task_queue`, and `namespace`
- propagate the active W3C trace context into Temporal headers
- inject MoonMind correlation metadata into headers

This is the boundary where request traces connect to Temporal execution traces.

### 8.3 Workflow instrumentation

Workflows must stay deterministic.

Therefore:
- do **not** call OTLP exporters from workflow code
- do **not** create ad hoc spans in workflow bodies using non-deterministic APIs
- do **not** make telemetry decisions based on current time or runtime state outside workflow history

Instead:
- use Temporal workflow inbound/outbound interceptors to produce minimal workflow/task spans
- emit workflow-level trace events only at deterministic lifecycle points already represented in history
- keep workflow spans thin and primarily about orchestration transitions, not payload content

Recommended workflow span boundaries:
- workflow execution started
- workflow task processed
- state transition occurred
- child workflow scheduled/completed
- activity scheduled/completed/failed/canceled
- continue-as-new requested
- workflow completed/failed/canceled

Workflow spans should be shallow. The rich details belong in activity spans and artifacts.

### 8.4 Activity instrumentation

Activities are the main telemetry producers.

Each activity invocation should create a span with:
- `temporal.workflow_id`
- `temporal.run_id`
- `temporal.activity_id`
- `temporal.activity_type`
- `temporal.task_queue`
- `temporal.attempt`
- `moonmind.correlation_id`
- `moonmind.worker_fleet`
- `moonmind.runtime`
- `moonmind.provider`
- `moonmind.repo`
- `moonmind.integration`
- `moonmind.artifact_ref` where relevant

Activities should also:
- emit bounded events for major steps
- record retries and timeout/cancel checkpoints
- record artifact references for large evidence
- emit domain metrics on completion

### 8.5 Child workflows and Continue-As-New

For child workflows:
- create child workflow spans linked to parent workflow spans
- carry forward `moonmind.correlation_id`
- include `moonmind.parent_workflow_id`
- include `moonmind.root_workflow_id` where useful

For Continue-As-New:
- preserve `moonmind.correlation_id`
- create a new run-scoped span tree for the new `run_id`
- add a span link or attribute indicating prior run
- do not pretend all runs share one physical run instance

This lets operators see stable logical execution plus actual Temporal run boundaries.

## 9. Span taxonomy

### 9.1 API spans

Examples:
- `moonmind.api.start_execution`
- `moonmind.api.update_execution`
- `moonmind.api.signal_execution`
- `moonmind.api.cancel_execution`
- `moonmind.api.upload_artifact`
- `moonmind.api.download_artifact`

### 9.2 Temporal client spans

Examples:
- `temporal.client.start_workflow`
- `temporal.client.signal_workflow`
- `temporal.client.update_workflow`
- `temporal.client.query_workflow`
- `temporal.client.cancel_workflow`

### 9.3 Workflow spans

Examples:
- `temporal.workflow.execute`
- `temporal.workflow.task`
- `temporal.workflow.child_start`
- `temporal.workflow.continue_as_new`

### 9.4 Activity spans

Examples:
- `temporal.activity.plan.generate`
- `temporal.activity.artifact.read`
- `temporal.activity.artifact.write_complete`
- `temporal.activity.mm.skill.execute`
- `temporal.activity.sandbox.run_command`
- `temporal.activity.integration.jules.start`
- `temporal.activity.integration.jules.status`
- `temporal.activity.integration.jules.fetch_result`

### 9.5 Sub-spans inside activities

Activities may create sub-spans for internal steps, such as:
- `moonmind.llm.request`
- `moonmind.llm.stream`
- `moonmind.mcp.call`
- `moonmind.http.request`
- `moonmind.git.checkout`
- `moonmind.sandbox.exec`
- `moonmind.artifact.upload`
- `moonmind.artifact.download`

This is the preferred place to capture deep operational details.

## 10. Attribute model

### 10.1 Required common attributes

All spans and logs that relate to execution should carry, when available:
- `service.name`
- `deployment.environment`
- `moonmind.component`
- `moonmind.worker_fleet`
- `moonmind.correlation_id`
- `moonmind.owner_type`
- `moonmind.owner_id`
- `temporal.namespace`
- `temporal.workflow_id`
- `temporal.run_id`

### 10.2 Required activity attributes

Additionally for activities:
- `temporal.activity_id`
- `temporal.activity_type`
- `temporal.attempt`
- `temporal.task_queue`
- `moonmind.idempotency_key_hash`
- `moonmind.workflow_type`
- `moonmind.mm_state`
- `moonmind.entry`
- `moonmind.waiting_reason` when relevant

### 10.3 AI and runtime attributes

Where relevant and safe:
- `moonmind.provider`
- `moonmind.model`
- `moonmind.reasoning_effort`
- `moonmind.token_input`
- `moonmind.token_output`
- `moonmind.cost_estimate_usd`
- `moonmind.tool_name`
- `moonmind.integration`
- `moonmind.repo`
- `moonmind.artifact_id`
- `moonmind.artifact_link_type`

### 10.4 Attributes that must not be recorded by default

Do not record by default:
- raw prompt text
- raw completion text
- raw command stdout/stderr above a tiny preview
- API keys or tokens
- presigned URLs
- full repo diffs
- unbounded user content

## 11. Metrics design

### 11.1 Use two metric sources

1. **Temporal-native metrics**
   - worker/task metrics
   - activity execution latency
   - poller health
   - schedule-to-start and start-to-close distributions
   - retries/timeouts/cancellations

2. **MoonMind application metrics**
   - FastAPI request metrics
   - provider call metrics
   - artifact metrics
   - sandbox metrics
   - business and AI metrics

### 11.2 Recommended MoonMind metric families

Workflow/domain:
- `moonmind_workflow_started_total`
- `moonmind_workflow_completed_total`
- `moonmind_workflow_failed_total`
- `moonmind_workflow_canceled_total`
- `moonmind_workflow_duration_seconds`
- `moonmind_workflow_waiting_total`
- `moonmind_workflow_waiting_seconds`

Activities:
- `moonmind_activity_started_total`
- `moonmind_activity_completed_total`
- `moonmind_activity_failed_total`
- `moonmind_activity_duration_seconds`
- `moonmind_activity_retry_total`

LLM/tool:
- `moonmind_llm_requests_total`
- `moonmind_llm_latency_seconds`
- `moonmind_llm_tokens_input_total`
- `moonmind_llm_tokens_output_total`
- `moonmind_llm_cost_estimate_usd_total`
- `moonmind_tool_calls_total`
- `moonmind_tool_duration_seconds`
- `moonmind_tool_failures_total`

Artifacts:
- `moonmind_artifact_create_total`
- `moonmind_artifact_read_total`
- `moonmind_artifact_write_total`
- `moonmind_artifact_bytes_total`
- `moonmind_artifact_preview_total`

Sandbox/integrations:
- `moonmind_sandbox_exec_total`
- `moonmind_sandbox_exec_duration_seconds`
- `moonmind_sandbox_exec_failures_total`
- `moonmind_integration_requests_total`
- `moonmind_integration_latency_seconds`
- `moonmind_integration_failures_total`

Approval/operator:
- `moonmind_approval_wait_seconds`
- `moonmind_attention_required_total`
- `moonmind_rerun_requested_total`

### 11.3 Label discipline

Metrics labels must stay bounded.

Allowed labels:
- workflow_type
- activity_type
- provider
- model
- worker_fleet
- integration
- state
- waiting_reason
- outcome

Do not use:
- workflow_id
- run_id
- artifact_id
- repo branch names if unbounded
- raw tool arguments
- user-provided free text

Identifiers belong in traces/logs, not metric label cardinality.

## 12. Logging design

### 12.1 Structured JSON only

MoonMind should standardize on `structlog` JSON output across:
- API service
- workflow worker
- artifacts worker
- llm worker
- sandbox worker
- integrations worker
- agent runtime worker

### 12.2 Required log context

Every execution-related log line should include:
- `timestamp`
- `level`
- `service`
- `component`
- `worker_fleet`
- `trace_id`
- `span_id`
- `moonmind.correlation_id`
- `temporal.workflow_id`
- `temporal.run_id`
- `temporal.activity_id` when relevant
- `temporal.activity_type` when relevant
- `temporal.attempt`
- `workflow_type`
- `idempotency_key_hash`

### 12.3 Large or noisy logs

Do not emit unbounded stdout/stderr or prompt dumps as normal logs.

Instead:
- keep a tiny preview in logs/spans when safe
- write full output to artifacts using `output.logs` or `debug.trace`
- log only the artifact reference and bounded summary

### 12.4 Mission Control integration

Mission Control should consume:
- bounded summaries from APIs
- artifact references for full logs
- trace links when configured
- not raw collector streams directly

## 13. AI-specific tracing

### 13.1 LLM spans

Inside LLM-related activities, create child spans for the actual provider call with attributes:
- provider
- model
- reasoning_effort
- input/output token counts
- cost estimate
- request/response sizes (bounded)
- retry count
- cache hit/miss if applicable

### 13.2 MCP and tool spans

Wrap each MCP/tool execution in its own child span with:
- tool name
- transport type
- latency
- success/failure
- bounded error classification

### 13.3 Sandbox spans

Wrap command execution and repo actions with:
- command class (bounded)
- workspace identifier or hash
- exit code
- duration
- output artifact refs

### 13.4 Privacy controls

Add config flags to separate:
- operational metadata
- payload content capture

Recommended config:
- `MOONMIND_OTEL_ENABLED=true`
- `MOONMIND_OTEL_CAPTURE_LLM_CONTENT=false`
- `MOONMIND_OTEL_CAPTURE_TOOL_IO=false`
- `MOONMIND_OTEL_CAPTURE_SANDBOX_OUTPUT=false`

When disabled, spans should still carry counts, timings, and outcome classes.

## 14. Collector and backend design

### 14.1 Collector role

The OTel Collector should be the only default export target for MoonMind services.

Responsibilities:
- receive OTLP over gRPC/HTTP
- batch and retry exports
- add environment-level resource attributes
- route traces to Tempo
- route metrics to Prometheus remote write or expose scrape pipeline
- route logs to Loki when enabled

### 14.2 Local/default backend

Recommended default stack:
- OTel Collector
- Tempo
- Prometheus
- Loki
- Grafana

This aligns with MoonMind's self-hosted posture and avoids vendor lock-in.

### 14.3 External backend support

Allow OTLP export overrides to:
- Datadog
- Honeycomb
- Grafana Cloud
- New Relic
- Langfuse/Phoenix via compatible collectors or bridges

MoonMind code should not care which backend is used once it speaks OTLP.

## 15. Service-by-service implementation

### 15.1 API service

Add:
- FastAPI instrumentation middleware
- Temporal client interceptor wiring
- structured log processor that injects trace/span/workflow context
- metrics endpoint for Prometheus
- trace link generation in execution/action responses when feature-flagged

### 15.2 Workflow worker

Add:
- worker-level resource attributes (`service.name=moonmind-temporal-worker-workflow`)
- workflow and activity interceptors
- minimal workflow task spans
- correlation-aware logging
- metrics for worker health and queue polling

### 15.3 Artifacts worker

Add:
- spans for create/read/write_complete/link/list/preview
- artifact ID/reference attributes
- bytes/latency metrics
- artifact log routing for large debug output

### 15.4 LLM worker

Add:
- spans for provider API calls
- token/cost metrics
- provider/model labels
- bounded error classification
- optional content capture behind config gates

### 15.5 Sandbox worker

Add:
- spans around repo checkout, patch apply, test runs, command execution
- heartbeat progress logs
- exit-code metrics
- output artifact refs for logs/results

### 15.6 Integrations worker

Add:
- per-provider spans
- rate-limit/error-class metrics
- webhook/callback correlation fields
- circuit-breaker and retry telemetry

## 16. Mission Control and Temporal UI integration

### 16.1 What Mission Control should show

Mission Control should remain the human-friendly operator surface and link to telemetry, not replace it.

Suggested detail-page fields:
- trace ID / “View trace” link
- latest run ID
- worker fleet
- activity summaries
- output log artifact refs
- retry counts and failure class
- AI cost/token summary
- waiting reason / attention required

### 16.2 What should remain elsewhere

- full execution history and advanced Temporal debugging → Temporal UI
- trace exploration → Grafana Tempo or chosen trace backend
- raw logs → Loki or chosen log backend
- dashboards and SLOs → Grafana

## 17. Security and privacy

### 17.1 Default safe posture

Defaults:
- telemetry enabled
- payload capture disabled
- secrets redacted
- large outputs stored as artifacts only
- presigned URLs never appear in spans/logs

### 17.2 Redaction

Use existing MoonMind sanitization paths for:
- tokens
- secrets
- credentials
- cookie values
- OAuth material
- command env dumps

### 17.3 Multi-tenant considerations

Where MoonMind supports multiple owners/operators:
- include owner metadata in spans/logs only when authorization-safe
- avoid cross-tenant trace links in user-facing UI
- keep raw telemetry backend access operator-scoped

## 18. Rollout plan

Phased delivery and actionable checklists are maintained in **`docs/tmp/090-OpenTelemetryPlans.md`** so this design doc stays declarative. That tracker expands the four rollout themes used here—**Foundations → Temporal worker instrumentation → Domain telemetry → Hardening**—and maps them to §15 (service-by-service), §19 (testing expectations), and privacy/hardening notes in §17.

## 19. Testing strategy

### 19.1 Contract tests
- verify required attributes are present on spans/logs
- verify metrics names and labels remain bounded
- verify content capture flags work

### 19.2 Integration tests
- API request starts trace
- workflow start carries correlation metadata
- activity spans inherit execution context
- Continue-As-New produces correct linked run traces
- large sandbox output becomes artifact ref, not span payload

### 19.3 Failure injection
- collector unavailable
- Tempo/Loki/Prometheus unavailable
- worker restart mid-activity
- activity retry/cancel/timeout
- provider timeout/rate limit

Telemetry failure must never break workflow correctness.

## 20. Key decisions

1. **OTel is the telemetry plane, not the execution truth plane.**
2. **Temporal Visibility remains the source of truth for Temporal-backed list/query/count.**
3. **Workflow code stays deterministic; rich telemetry belongs at interceptor and activity boundaries.**
4. **Continue-As-New uses stable correlation IDs plus new run-scoped traces.**
5. **Large evidence goes to artifacts, not spans.**
6. **Default export target is a self-hosted OTel Collector in Docker Compose.**
7. **Mission Control links to telemetry systems; it does not replace them.**

## 21. Implementation tracker

The former numbered implementation checklist is folded into the phase checklists in **`docs/tmp/090-OpenTelemetryPlans.md`** (see §18).