# Research: Docker-Out-of-Docker Workload Contract

## Decision 1: Keep Phase 1 Docker-free

- **Decision**: Implement only schema, registry loading, and validation in Phase 1.
- **Rationale**: The DooD plan's exit criteria require a validated request before Docker exists, which makes policy failures testable without side effects.
- **Alternatives considered**: Starting a minimal launcher now was rejected because it would mix Phase 1 and Phase 2 ownership and weaken the TDD boundary.

## Decision 2: Use Pydantic models for the canonical contract

- **Decision**: Define `WorkloadRequest`, `WorkloadResult`, `RunnerProfile`, and `WorkloadOwnershipMetadata` as Pydantic v2 models.
- **Rationale**: MoonMind already uses Pydantic for Temporal and agent-runtime contracts, and the Temporal data converter is already Pydantic-aware.
- **Alternatives considered**: Dataclasses were rejected because cross-field validation, aliases, and serialization contracts are already standardized on Pydantic.

## Decision 3: Put registry validation outside the request model

- **Decision**: Keep shape validation inside models and profile-aware policy validation inside a `RunnerProfileRegistry`.
- **Rationale**: Request validity depends on the selected profile and deployment workspace root, which are external policy context.
- **Alternatives considered**: Embedding a global registry in model validators was rejected because it would hide runtime configuration and make tests order-dependent.

## Decision 4: Support JSON and YAML deployment registry files

- **Decision**: Load registry files from JSON or YAML mappings/lists and validate each profile through the canonical `RunnerProfile` model.
- **Rationale**: PyYAML is already a dependency, while JSON remains a portable fallback for operators and tests.
- **Alternatives considered**: Static code-only registry was rejected because the plan selects deployment-owned registry files as the simplest starting point.

## Decision 5: Fail closed when no registry is configured

- **Decision**: The default empty registry allows no workload profiles.
- **Rationale**: Empty/default behavior must not silently permit arbitrary workload images.
- **Alternatives considered**: A built-in permissive debug profile was rejected because it violates the runner-profile replacement rule.

## Decision 6: Validate workspace paths through a configured workspace root

- **Decision**: Request validation resolves `repo_dir` and `artifacts_dir` against a registry-owned workspace root and rejects paths outside it.
- **Rationale**: Phase 1 must reject invalid mounts and workspace traversal before launcher mount generation exists.
- **Alternatives considered**: Checking only string prefixes was rejected because normalized path resolution catches traversal more reliably.
