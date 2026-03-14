# Research: MoonMind.AgentRun Workflow

## Decision 1: Workflow Framework
- **Decision**: Temporal IO.
- **Rationale**: MoonMind already uses Temporal for the root `MoonMind.Run` workflow. Creating `MoonMind.AgentRun` as a child workflow integrates seamlessly.
- **Alternatives considered**: Celery or simple async tasks, but they lack durable execution and robust cancellation scopes.

## Decision 2: Managed vs. External Routing
- **Decision**: Define a shared `AgentAdapter` interface in Temporal, with concrete implementations for `ExternalAgentAdapter` and `ManagedAgentAdapter`.
- **Rationale**: Keeps `MoonMind.AgentRun` agnostic to the runtime mechanism.
- **Alternatives considered**: Separate workflows for external vs. managed runs, but that violates the unified execution model objective.

## Decision 3: Cancellation Safety
- **Decision**: Temporal's non-cancellable scopes.
- **Rationale**: Ensures adapter-level cleanup is invoked even if the parent workflow is cancelled by the operator mid-flight.
- **Alternatives considered**: None robust enough for Temporal workflows.
