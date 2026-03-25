# Temporal Message Passing - Phase 3: Idempotency and Deduplication

This document fulfills the deliverables for Phase 3 of the Temporal Workflow Message Passing Improvements plan, focusing on handling duplicate submissions, duplicate client retries, and idempotency for side effects.

## 1. Conventions for Update IDs and Deduplication

### Overview
In MoonMind's architecture, operators use Mission Control and APIs to send commands (e.g., Pause, Resume, Cancel). Network issues, UI refreshes, or user double-clicks can result in duplicate commands being sent. 

### Temporal Update IDs
Temporal's SDK natively handles deduplication of Workflow Updates if the client provides an `update_id`.
- **Client Convention**: When initiating a trackable update (e.g., from `api_service` or `Mission Control`), the client MUST provide an `update_id` (e.g., a UUID generated at the time of user intent).
- **Workflow Convention**: Inside `@workflow.update` handlers, workflows can access `workflow.current_update_info().update_id`. When a workflow executes a `Continue-As-New` operation, we must pass along previously processed `update_id`s if commands need to be deduplicated across workflow runs (though native Temporal handles deduplication within a single run).

### Duplicate Client Retries
If the API Service retries a Temporal request due to a transient Temporal Server error, it must reuse the original `update_id` or `workflow_id` to prevent dual execution.

## 2. Idempotency Keys at Activity Boundaries

Activities encapsulate side effects (e.g., calling an LLM, generating a plan, storing an artifact). Temporal retries failed activities automatically based on the configured Retry Policy. For non-repeatable or expensive operations, idempotency keys are required.

### Implementation Strategy
1. **Activity Payloads**: Activities that interact with external state (e.g., `plan.generate`, `artifact.write`, LLM calls) must accept an `idempotency_key` string in their input payload.
2. **Generation**: The orchestrating workflow (`MoonMind.Run` or child workflows) generates this key deterministically.
   - Example format (current convention): use `workflow.info().workflow_id`, e.g. `{workflow_id}_plan_generate` or `{workflow_id}_{node_id}_execute`. This keeps the key stable across retries and Continue-As-New runs for the same logical job.
3. **Usage in Worker**: The activity worker uses the `idempotency_key` when communicating with external APIs (e.g., setting the `Idempotency-Key` HTTP header) or checking local caches to prevent re-execution of billed operations if the activity fails *after* external side-effects complete but *before* Temporal records the completion.

### Compensating Actions
Where full idempotency is impossible (e.g., an external API doesn't support idempotency keys):
- **Bounded Retries**: Use an explicit, bounded Retry Policy (e.g., `max_attempts=3`).
- **Idempotency via Check**: The activity should begin by querying the external state to check if the action was already performed (e.g., "does this commit already exist?").
- **Compensation**: If an operation fails midway and leaves dangling state, the workflow should catch the exception and execute a compensating activity (e.g., deleting a partial branch) before retrying or failing.

## 3. Signal-With-Start and Logical Jobs

To guarantee "exactly one logical job" per task ID:
- Use Temporal's **Signal-With-Start** or standard `start_workflow` with a deterministic `workflow_id`.
- **Convention**: The `workflow_id` should map to the application's unique job identifier (e.g., `run-{task_uuid}`). 
- If a client calls `start_workflow` with an existing `workflow_id` and the `WorkflowIdReusePolicy` is set to `REJECT_DUPLICATE` or `ALLOW_DUPLICATE_FAILED_ONLY`, Temporal automatically rejects the duplicate, ensuring exactly-once execution per logical job. MoonMind relies on this mechanism to prevent parallel executions of the same task.

## 4. Workflow-Boundary Testing

Per MoonMind's testing policy, workflow-boundary tests must cover compatibility paths for prior payload shapes where in-flight runs may exist. Tests added in Phase 3 explicitly verify that activities tolerate the optional presence or absence of `idempotency_key` in their inputs (payload-schema backwards compatibility), and our Temporal workflows gate any new commands behind `workflow.patched(...)` (or an equivalent pattern) so that in-flight workflows replay with identical command sequences (Temporal replay backwards compatibility).