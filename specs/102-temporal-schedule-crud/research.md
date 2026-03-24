# Research: Temporal Schedule CRUD

## Temporal Python SDK Schedule API

**Decision**: Use `temporalio.client.Client.create_schedule()` and `ScheduleHandle` methods.
**Rationale**: The Temporal Python SDK provides first-class schedule support. The API is stable and matches the Go SDK semantics. The self-hosted Temporal server (auto-setup image) supports schedules without additional configuration.
**Alternatives considered**: (1) Custom DB-backed scheduler — rejected, this is what we're replacing. (2) Temporal cron schedules on workflows (deprecated `cron_schedule` parameter) — rejected, Temporal Schedules are the modern replacement.

## Schedule ID Convention

**Decision**: Use `mm-schedule:{definition_uuid}` for schedule IDs, `mm:{definition_uuid}:{schedule_time_epoch}` for spawned workflow IDs.
**Rationale**: Follows existing MoonMind `mm:` prefix convention for workflow IDs. UUID ensures uniqueness. Including schedule time in workflow ID enables idempotent scheduling (Temporal rejects duplicate workflow IDs).
**Alternatives considered**: Using cron expression hash in the ID — rejected, IDs should be stable across cron changes.

## Policy Vocabulary Mapping

**Decision**: Map MoonMind policy modes directly to Temporal SDK enums/values. Drop `maxConcurrentRuns` — Temporal doesn't support numeric concurrency on schedules.
**Rationale**: Temporal's `ScheduleOverlapPolicy` enum covers the common cases. Numeric concurrency would require a workflow-level semaphore (out of scope for Phase 1).
**Alternatives considered**: Implementing a custom concurrency limiter — rejected, deferred to Phase 2 if needed.

## Exception Wrapping Strategy

**Decision**: Create a small exception hierarchy (`ScheduleAdapterError` base, `ScheduleNotFoundError`, `ScheduleAlreadyExistsError`, `ScheduleOperationError` subclasses) and catch Temporal SDK exceptions at the adapter boundary.
**Rationale**: Constitution IX requires explicit failure handling. Leaking raw `temporalio` exceptions would couple callers to the SDK. Existing adapter methods already set this pattern (e.g., `WorkflowAlreadyStartedError` is caught in `start_workflow`).
**Alternatives considered**: Re-raising SDK exceptions with a MoonMind wrapper — rejected, dedicated types are clearer and enable specific handling in `RecurringTasksService`.

## Temporal Schedule Template Syntax for Workflow IDs

**Decision**: Temporal's `ScheduleActionStartWorkflow.id` supports template syntax like `{{.ScheduleTime}}`. Verify exact format via SDK tests.
**Rationale**: The Go scheduler server evaluates these templates. The Python SDK passes the string through.
**Alternatives considered**: Computing workflow IDs in a custom start-workflow wrapper — rejected, Temporal's built-in template is simpler.
