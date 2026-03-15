# Implementation Plan: Agent Runtime Phase 1 Contracts

**Branch**: `072-agent-run-contracts` | **Date**: 2026-03-14 | **Spec**: [specs/072-agent-run-contracts/spec.md](spec.md)  
**Input**: Feature specification from `/specs/072-agent-run-contracts/spec.md`

## Summary

Implement Phase 1 from `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` by adding production runtime contract code for unified agent execution requests, run lifecycle envelopes, adapter interface boundaries, and managed auth-profile policy models. Deliver canonical external adapter integration for Jules-backed runs on the new interface and add validation tests for contract semantics, idempotency handling, and artifact-reference discipline.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Pydantic v2, typing Protocol/ABC contracts, existing Temporal runtime adapter modules, Jules client integration  
**Storage**: N/A for Phase 1 (contract surfaces only; no new persistence schema)  
**Testing**: `./tools/test_unit.sh` (unit tests for schemas/adapters/activity-runtime integrations)  
**Target Platform**: Linux containerized MoonMind API/worker runtime with Temporal orchestration  
**Project Type**: Backend runtime contracts + adapter boundary normalization  
**Performance Goals**: Deterministic contract validation with minimal overhead; no long-blocking runtime behavior introduced in Phase 1  
**Constraints**: Runtime-only implementation scope (no docs-only completion), no raw credential fields in workflow-facing contracts, preserve existing runtime behavior compatibility  
**Scale/Scope**: `moonmind/schemas`, `moonmind/workflows/adapters`, `moonmind/workflows/temporal/activity_runtime.py`, and related unit tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. No new required infrastructure/services are introduced.
- **II. Avoid Vendor Lock-In**: PASS. Agent runtime contract is adapter-first and provider-agnostic.
- **III. Own Your Data**: PASS. Contracts remain portable and artifact-reference based.
- **IV. Skills Are First-Class and Easy to Add**: PASS. Changes are interface-focused and keep orchestration runtime-neutral.
- **V. The Bittersweet Lesson**: PASS. Thin scaffolding via replaceable adapter boundary, with tests as anchor.
- **VI. Powerful Runtime Configurability**: PASS. Existing runtime settings remain authoritative; no hardcoded provider-only path.
- **VII. Modular and Extensible Architecture**: PASS. New module boundaries isolate contract volatility.
- **VIII. Self-Healing by Default**: PASS. Idempotency semantics are explicit in start-like contracts.
- **IX. Facilitate Continuous Improvement**: PASS. Normalized status/result contracts improve telemetry comparability.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-*` traceability is mandatory in plan/tasks.

### Post-Design Re-Check

- PASS. Design keeps provider-specific behavior behind an adapter contract and preserves portability.
- PASS. Runtime implementation scope is explicit and includes automated validation tasks.
- PASS. No constitution exceptions require complexity waivers.

## Project Structure

### Documentation (this feature)

```text
specs/072-agent-run-contracts/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── agent-runtime-contracts.md
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── agent_runtime_models.py
└── workflows/
    ├── adapters/
    │   ├── agent_adapter.py
    │   └── jules_agent_adapter.py
    └── temporal/
        └── activity_runtime.py

tests/
├── unit/schemas/
│   └── test_agent_runtime_models.py
└── unit/workflows/
    ├── adapters/
    │   └── test_jules_agent_adapter.py
    └── temporal/
        └── test_activity_runtime.py
```

**Structure Decision**: Implement Phase 1 as additive runtime contracts and adapter surfaces in existing temporal/jules modules, minimizing behavior churn while establishing canonical types for later workflow phases.

## Phase 0 - Research Summary

Research conclusions captured in `research.md`:

1. Use Pydantic models for canonical request/handle/status/result/auth-profile contracts to match existing schema conventions.
2. Use a provider-neutral adapter interface in `moonmind/workflows/adapters` with async methods and canonical return types.
3. Bridge existing Jules integration through a dedicated external-adapter implementation to prove the contract without forcing workflow migration in Phase 1.
4. Encode idempotency semantics in contracts and adapter behavior without adding DB migrations in this phase.
5. Keep artifact/log discipline enforceable by contract validation (references and bounded summaries), deferring larger orchestration changes to later phases.

## Phase 1 - Design Outputs

- **Data Model**: `data-model.md` defines canonical entities and validation semantics for Phase 1.
- **Contract Surface**: `contracts/agent-runtime-contracts.md` defines request/result interface, status vocabulary, and adapter semantics.
- **Requirements Traceability**: `contracts/requirements-traceability.md` maps every `DOC-REQ-*` to FRs, implementation surfaces, and validation strategy.
- **Execution Guide**: `quickstart.md` defines deterministic local verification using repository-standard unit test command(s).

## Implementation Strategy

### 1. Add canonical Phase 1 schema contracts

- Introduce `AgentExecutionRequest`, `AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`, and `ManagedAgentAuthProfile` models.
- Enforce normalized status enums and terminal-state helper semantics.
- Enforce artifact-reference-first payload discipline and credential-safe field boundaries.

### 2. Add shared agent adapter boundary

- Introduce provider-neutral `AgentAdapter` interface with `start`, `status`, `fetch_result`, and `cancel`.
- Define adapter request/response typing to use canonical schema models.
- Keep interface explicitly async to match Temporal integration behavior.

### 3. Add concrete external adapter bridge (Jules)

- Implement `JulesAgentAdapter` that maps Jules client behavior to canonical contracts.
- Preserve existing idempotency reuse behavior for identical start idempotency keys.
- Normalize provider statuses into `AgentRunStatus` and result envelopes.

### 4. Integrate with Temporal activity runtime bridge

- Update Temporal Jules activity helper internals to reuse canonical adapter contracts where feasible without breaking existing behavior.
- Ensure existing activity outputs remain backward-compatible for current workflows.

### 5. Validate contracts and adapter behavior

- Add unit tests for schema validations, terminal-state semantics, and auth-profile constraints.
- Add adapter tests validating mapping, idempotency behavior, and cancel/fetch semantics.
- Extend temporal activity runtime tests to ensure canonical adapter integration path is exercised.

## Runtime vs Docs Mode Alignment

- Selected mode: **runtime**.
- Completion requires production runtime code updates and automated validation tests.
- Docs/spec artifacts are traceability aids and do not satisfy implementation completion by themselves.

## Remediation Gates

- Every `DOC-REQ-*` must map to FR(s), implementation surfaces, and explicit validation strategy.
- Tasks must include at least one runtime production code task and one validation task per `DOC-REQ-*`.
- Any missing contract mapping or validation strategy is a blocking planning failure.

## Risks & Mitigations

- **Risk: Adapter contract introduces semantic drift vs existing activity payloads.**  
  **Mitigation**: Preserve existing activity return shapes while internally adopting canonical models.
- **Risk: Over-constraining contracts could block current provider payload realities.**  
  **Mitigation**: Keep provider metadata and optional fields in canonical envelopes where normalization is incomplete.
- **Risk: Idempotency semantics are inconsistently applied across adapters.**  
  **Mitigation**: Standardize start idempotency behavior in shared adapter tests.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
