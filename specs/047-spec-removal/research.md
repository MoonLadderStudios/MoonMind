# Research: Canonical Workflow Surface Naming

## Decision 1: Runtime orchestration mode is authoritative for this feature

- **Decision**: Treat runtime implementation intent (`DOC-REQ-011`) as the governing mode for planning and acceptance.
- **Rationale**: The task objective requires production runtime code changes and validation tests, not docs/spec-only delivery.
- **Alternatives considered**:
  - Docs/spec-only execution: rejected because it conflicts with runtime delivery objective.
  - Split into two independent features now: rejected to avoid traceability drift and duplicate migration logic.

## Decision 2: Keep docs mode aligned to runtime mode via shared canonical map and gates

- **Decision**: Use one canonical token map and one traceability matrix for both runtime and docs surfaces.
- **Rationale**: Prevents behavioral/documentation divergence and keeps validation deterministic.
- **Alternatives considered**:
  - Separate maps per mode: rejected due to inconsistency risk.
  - Informal alignment by reviewer judgment only: rejected due to weak repeatability.

## Decision 3: Canonical naming migration must be semantics-preserving

- **Decision**: Rename configuration/routes/schemas/metrics/artifact references without changing queue semantics, billing-relevant values, model identifiers, or effort pass-through.
- **Rationale**: Compatibility policy forbids transforms that alter execution semantics.
- **Alternatives considered**:
  - Opportunistic behavior refactor during rename: rejected as out of scope and high risk.
  - Silent compatibility aliases for legacy runtime inputs: rejected; fail-fast behavior is preferred.

## Decision 4: Validate with dual-surface checks and required unit test command

- **Decision**: Require both token-scan checks and `./tools/test_unit.sh` for acceptance.
- **Rationale**: Scans enforce naming outcomes; unit tests protect runtime behavior.
- **Alternatives considered**:
  - Scan-only validation: rejected because runtime regressions could pass undetected.
  - Direct `pytest` invocation: rejected per repository test policy.

## Decision 5: Runtime migration contract is explicit

- **Decision**: Define canonical runtime expectations in `contracts/workflow-naming-contract.md` for env keys, API routes, schema identifiers, metrics namespace, and artifact roots.
- **Rationale**: Explicit contract boundaries reduce ambiguity for implementation and review.
- **Alternatives considered**:
  - Keep contract implicit in prose only: rejected due to review ambiguity.
  - Defer contract until implementation: rejected due to reduced planning quality.

## Decision 6: Traceability must cover every source requirement including DOC-REQ-011

- **Decision**: Maintain one row per `DOC-REQ-*` with mapped FRs, implementation surfaces, and concrete validation strategy.
- **Rationale**: Skill requirements mandate complete mapping and validation planning.
- **Alternatives considered**:
  - Aggregate rows by theme: rejected for insufficient audit granularity.
  - Exclude runtime-intent requirement from contract table: rejected as non-compliant.

## Decision 7: Migration rollout order prioritizes safety

- **Decision**: Execute in order: planning controls -> docs/spec rename -> runtime rename -> verification/report.
- **Rationale**: Early guardrails and traceability reduce risk before runtime-touching edits.
- **Alternatives considered**:
  - Runtime-first migration: rejected due to higher breakage and review complexity.
  - Parallel full-surface edits from start: rejected due to difficult rollback and auditability.

## Verification Snapshot (2026-03-01)

- Docs/spec verification helper run passed with approved exceptions scoped to migration-context files.
- Runtime verification helper run failed due to remaining legacy runtime naming; runtime parity work remains required.
- Unit validation run via `./tools/test_unit.sh` passed (`895 passed, 8 subtests passed`).
