# Phase 0 Research – MoonMind Orchestrator Implementation

**Date**: 2025-11-13  
**Context**: Resolve technical unknowns from the implementation plan and capture best practices for critical dependencies and integrations prior to design.

---

## Clarifications

### 1. Testing strategy for mm-orchestrator
- **Decision**: Validate orchestrator behavior through docker-compose-based integration tests that spin up RabbitMQ, the dedicated Celery worker, and the API service (`docker compose up rabbitmq celery-worker api orchestrator-test`) plus targeted unit tests for ActionPlan logic.
- **Rationale**: The feature’s success criteria hinge on verifying patch/build/relaunch flows exactly as they run in production; composing the same stack in CI ensures Docker commands, allow lists, approvals, and artifact writes behave correctly. Lightweight unit tests keep ActionPlan decision trees fast to iterate.
- **Alternatives considered**: (a) Only unit tests inside the Python package—rejected because they cannot validate Docker socket interactions or compose semantics. (b) Manually triggered end-to-end tests after deployment—rejected because they delay regression detection and violate the “autonomous fix” promise.

### 2. Expected run concurrency / worker capacity
- **Decision**: Start with a single orchestrator Celery worker per host (sequential run processing) while allowing horizontal scaling later by running additional worker containers behind the same queue.
- **Rationale**: Compose is operating against a single Docker daemon; serializing runs prevents interleaved builds/restarts from stepping on each other. Celery still provides the option to scale by adding workers once safeguards like service-level locking and resource quotas are implemented.
- **Alternatives considered**: (a) Immediately allow multiple workers—rejected due to risk of concurrent builds restarting the same service simultaneously. (b) Enforce concurrency purely via database locking—deferred until instrumentation proves the need.

---

## Dependency Best Practices

### Docker CLI + Compose plugin
- **Decision**: Invoke Compose with explicit project name (`--project-name moonmind`) and service scoping (`build <service>`, `up -d --no-deps <service>`) while streaming stdout/stderr to per-run log files.
- **Rationale**: Explicit project names keep orchestrator runs isolated even if multiple compose files exist; scoping prevents unintended restarts. Capturing the raw command output fulfills audit and rollback requirements.
- **Alternatives considered**: Using ad-hoc `docker build`/`docker run` commands was rejected because it diverges from existing MoonMind operations and complicates Compose dependency management.

### Celery 5.4 task runner
- **Decision**: Model each orchestrator step (analyze, patch, build, restart, verify, rollback) as dedicated Celery tasks chained via signatures so MoonMind UI can observe per-step states.
- **Rationale**: Aligns with current Spec Workflow architecture (Celery Chains) and allows retries/resumes at the failing step, matching requirements for transparency and auditability.
- **Alternatives considered**: A monolithic long-running task was rejected because it hides intermediate progress and complicates retries.

### StatsD metrics emission
- **Decision**: Wrap run lifecycle events in a thin instrumentation layer that emits counters/timers when `STATSD_HOST/PORT` or `SPEC_WORKFLOW_METRICS_*` are configured, and degrade gracefully (log-only) when unset.
- **Rationale**: Keeps instrumentation optional yet consistent; ensures no runs fail due to missing StatsD while still collecting MTTR metrics when available.
- **Alternatives considered**: Mandatory metrics configuration—rejected to avoid blocking local development; ignoring metrics entirely—rejected because success criteria depend on operational insight.

### Artifact storage (filesystem/object store)
- **Decision**: Use the established `var/artifacts/spec_workflows/<run_id>` directory mounted into the orchestrator container, with file naming conventions (`patch.diff`, `build.log`, `verify.log`, `rollback.log`) and checksum recording in the run metadata.
- **Rationale**: Reuses the path referenced in existing Spec Workflow tooling and keeps artifacts colocated with other runs for easier operator access and retention policies.
- **Alternatives considered**: Introducing a new object store bucket now—rejected as unnecessary initial complexity; storing artifacts only in the database—rejected due to log size.

---

## Integration Patterns

### RabbitMQ broker
- **Decision**: Reuse the existing RabbitMQ instance defined in `docker-compose.yaml`, placing mm-orchestrator tasks on a dedicated queue (e.g., `orchestrator.run`) with `prefetch_count=1` to enforce sequential execution.
- **Rationale**: Keeps infrastructure consistent with current Celery deployments while allowing workload isolation via queue naming and QoS settings.
- **Alternatives considered**: Provisioning a separate broker—rejected for initial scope; using Redis as a broker—rejected because the stack standardizes on RabbitMQ.

### PostgreSQL result backend
- **Decision**: Persist run/step state in the existing `spec_workflow_runs` and `spec_workflow_task_states` tables, adding orchestrator-specific fields (approval id, artifact paths, rollback outcome) as needed.
- **Rationale**: Satisfies the requirement to surface orchestrator progress inside the MoonMind UI and avoids duplicating persistence layers.
- **Alternatives considered**: Creating new tables just for orchestrator—rejected because Spec Workflow history already tracks similar data; writing only to the filesystem—rejected because structured status queries are required.

### MoonMind API + approvals
- **Decision**: Extend the API so operators can submit instructions, provide approvals, and fetch run artifacts; the orchestrator will call back into the API to update run states and honor approval tokens before editing files.
- **Rationale**: Centralizes operator interaction in the existing MoonMind UI/API surface while fulfilling policy enforcement.
- **Alternatives considered**: Direct CLI-only approvals—rejected to keep workflows discoverable within MoonMind.

### Service health endpoints
- **Decision**: Require each target service to expose a documented health URL (e.g., `/health` via API container networking); orchestrator health checks will poll the service hostname inside the compose network with exponential backoff and a configurable timeout.
- **Rationale**: Matches the verification steps defined in the spec and avoids coupling to hostnames outside the compose network; exponential backoff balances readiness wait vs. failure detection.
- **Alternatives considered**: Relying solely on container state (`docker ps`)—rejected because it misses application-level readiness; hitting public ingress endpoints—rejected because orchestrator runs inside the compose network.

---

## Outcomes

- Testing and scale uncertainties are resolved: integration tests will exercise the full compose stack, and orchestrator runs process sequentially per worker with the option to add more workers later.
- Dependency best practices ensure Docker/Celery/StatsD/artifact handling align with existing MoonMind operations.
- Integration patterns define how the orchestrator uses RabbitMQ, PostgreSQL, MoonMind APIs, and service health endpoints, providing the blueprint required for Phase 1 design deliverables.
