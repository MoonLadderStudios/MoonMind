# Phase 0 Research: Agent Runtime Phase 1 Contracts

## Decision 1: Canonical contracts should be Pydantic schema models

- **Decision**: Implement Phase 1 contract entities as Pydantic v2 models under `moonmind/schemas/agent_runtime_models.py`.
- **Rationale**: Existing Temporal and integration contracts already use Pydantic models, so validation, serialization aliases, and test patterns are consistent.
- **Alternatives considered**:
  - Plain dataclasses: lighter but weaker runtime validation guarantees and schema parity.
  - TypedDict-only contracts: static typing only, no runtime validation or normalized field behavior.

## Decision 2: Shared adapter boundary should be provider-neutral and async

- **Decision**: Add an async `AgentAdapter` protocol/interface with `start`, `status`, `fetch_result`, and `cancel` methods returning canonical contract models.
- **Rationale**: Matches source document requirements and keeps workflow-orchestration surfaces decoupled from provider specifics.
- **Alternatives considered**:
  - Reuse Jules client methods directly: creates provider lock-in and violates adapter boundary.
  - Sync interface with async wrappers: adds complexity without benefit because runtime integrations are already async.

## Decision 3: Use Jules as initial external adapter conformance proof

- **Decision**: Implement `JulesAgentAdapter` as the first concrete external adapter mapping Jules provider payloads to canonical models.
- **Rationale**: Jules integration already exists and can validate interface viability with low migration risk.
- **Alternatives considered**:
  - Implement managed adapter first: higher scope and runtime process supervision dependencies not needed for Phase 1.
  - No concrete adapter in Phase 1: would leave shared interface unvalidated in production runtime code.

## Decision 4: Idempotency policy should be explicit and fail-fast

- **Decision**: Require non-empty idempotency key handling for side-effecting start paths and preserve deterministic response reuse on repeated keys.
- **Rationale**: Temporal retries must not duplicate launched external runs.
- **Alternatives considered**:
  - Best-effort idempotency: ambiguous duplicate-run behavior under retries.
  - Provider-specific idempotency only: inconsistent cross-adapter semantics.

## Decision 5: Enforce artifact-reference discipline at contract layer

- **Decision**: Keep large payload/log/transcript content out of canonical workflow-facing payloads and require references in result/request structures.
- **Rationale**: Aligns with document artifact/log discipline and keeps workflow payloads compact.
- **Alternatives considered**:
  - Allow optional inline blobs: invites workflow history bloat and inconsistent runtime behavior.
  - Enforce only in later workflow phase: loses immediate guardrails for new adapter code.
