# Implementation Plan: Skills and Plans Runtime Contracts

**Branch**: `045-skills-plan-contracts` | **Date**: 2026-03-05 | **Spec**: `specs/045-skills-plan-contracts/spec.md`  
**Input**: Feature specification from `/specs/045-skills-plan-contracts/spec.md`

## Summary

Implement runtime-grade skills and plans contracts from `docs/Skills/SkillAndPlanContracts.md` by delivering contract models, immutable artifact references, pinned registry snapshots, deep and structural plan validation, deterministic DAG interpretation, skill dispatch routing, progress reporting, and validation tests. Delivery remains runtime-authoritative: production code plus tests, not docs-only updates.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: dataclasses/asyncio runtime, PyYAML for registry loading, existing MoonMind settings/config stack, JSON-schema-like validation helpers under `moonmind/workflows/skills/`  
**Storage**: Artifact store abstractions (`InMemoryArtifactStore`, file-backed `LocalArtifactStore`) for immutable payloads; existing MoonMind persistence remains unchanged for this feature scope  
**Testing**: `./tools/test_unit.sh` with unit coverage in `tests/unit/workflows/test_skill_plan_runtime.py` and adjacent workflow skills tests  
**Target Platform**: Linux containerized MoonMind services (API, workers, orchestrator/celery runtime surfaces)  
**Project Type**: Multi-module backend runtime package (`moonmind/workflows/skills`)  
**Performance Goals**: Deterministic topological execution, dependency-correct scheduling, and bounded concurrency with small workflow payloads + artifact offloading for large outputs  
**Constraints**: Preserve deterministic orchestration semantics; fail fast on unsupported runtime inputs; enforce pinned snapshot resolution; keep runtime vs docs mode aligned to runtime implementation mode  
**Scale/Scope**: Skill/plan contract runtime in `moonmind/workflows/skills/*` plus tests for validation, dispatch, interpreter failure policy, and progress/summary artifacts

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Agent Deployment**: PASS. No additional operator prerequisites; feature is implemented within existing Python/test workflow and compose runtime.
- **II. Avoid Vendor Lock-In**: PASS. Contracts are portable JSON/YAML data with runtime-neutral artifacts and schema-based definitions.
- **III. Own Your Data**: PASS. Artifact refs and registry snapshots remain locally inspectable and immutable.
- **IV. Skills Are First-Class and Easy to Add**: PASS. Planning and execution are both modeled as skill invocations with explicit contracts.
- **V. Design for Replaceability**: PASS. Thin orchestration + thick contracts keeps execution adapters replaceable.
- **VI. Powerful Runtime Configurability**: PASS. Runtime behavior remains policy/input driven (failure mode, concurrency, retries/timeouts) instead of hidden constants.
- **VII. Modular and Extensible Architecture**: PASS. Changes are isolated to `moonmind/workflows/skills/` contract modules and tests.
- **VIII. Self-Healing by Default**: PASS. Retry/error model is explicit and policy-driven; cancellation/failure paths are deterministic.
- **IX. Facilitate Continuous Improvement**: PASS. Structured progress and summary artifacts improve run observability and diagnostics.
- **X. Spec-Driven Development Is the Source of Truth**: PASS. `DOC-REQ-*` mappings and traceability artifacts are retained as implementation gates.

### Post-Design Re-Check

- PASS. Phase 1 artifacts preserve deterministic orchestration boundaries and activity-side effects.
- PASS. No hidden fallback/compatibility transforms are introduced for skill or plan contract values.
- PASS. Runtime mode is still authoritative and requires production code + validation tests.

## Project Structure

### Documentation (this feature)

```text
specs/045-skills-plan-contracts/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── skills-plan-runtime.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
docs/Skills/SkillAndPlanContracts.md

moonmind/workflows/skills/
├── artifact_store.py
├── contracts.py
├── plan_executor.py
├── plan_validation.py
├── registry.py
├── skill_dispatcher.py
├── skill_plan_contracts.py
└── skill_registry.py

tests/unit/workflows/
├── test_skill_plan_runtime.py
├── test_skills_registry.py
└── test_skills_runner.py
```

**Structure Decision**: Keep all runtime implementation in the existing `moonmind/workflows/skills/` package and enforce behavior through unit tests under `tests/unit/workflows/` to avoid architectural drift.

## Phase 0 - Research Summary

Research outcomes in `specs/045-skills-plan-contracts/research.md` establish:

1. Keep registry/plan/skill contracts as immutable data, not executable workflow code.
2. Split structural validation (workflow-safe checks) and deep validation (activity path) before execution starts.
3. Enforce pinned snapshot resolution and activity-type-declared dispatch with no inferred routing.
4. Keep large payloads in artifact storage and expose progress/summary as structured contracts.
5. Keep runtime mode as the governing completion criterion (runtime code + tests).

## Phase 1 - Design Outputs

- **Data Model**: `data-model.md` defines ArtifactRef, SkillDefinition, RegistrySnapshot, SkillInvocation, PlanDefinition, validation/reporting, and execution summary entities.
- **API Contract**: `contracts/skills-plan-runtime.openapi.yaml` defines registry snapshot, validation, execution, progress, and summary endpoints for runtime surfaces.
- **Traceability**: `contracts/requirements-traceability.md` maps all `DOC-REQ-001` through `DOC-REQ-016` to FRs, implementation surfaces, implementation task coverage, validation task coverage, and validation strategy.
- **Execution Guide**: `quickstart.md` documents runtime-mode validation flow and repository-standard unit test path.

## Implementation Strategy

### 1. Contract model hardening

- Keep canonical contract types in `skill_plan_contracts.py` for:
  - `ArtifactRef`, `SkillDefinition`, `SkillInvocation`, `SkillResult`, `SkillFailure`
  - `PlanDefinition`, `PlanPolicy`, `PlanEdge`, `PlanRegistrySnapshot`
- Ensure parser/validator code rejects unsupported plan versions, failure modes, and malformed references.

### 2. Registry loading, validation, and snapshot pinning

- Validate required skill fields and uniqueness in `skill_registry.py`.
- Generate deterministic registry digest and immutable snapshot artifact refs.
- Ensure consumers resolve definitions from pinned snapshots only (`load_registry_snapshot_from_artifact`).

### 3. Deep plan validation activity path

- Keep structural checks deterministic and deep checks centralized in validation helpers.
- Maintain `plan_validate_activity` as authoritative deep validator before interpreter start.
- Reject plans on missing skills, schema violations, cycles, or invalid inter-node references.

### 4. Deterministic plan executor semantics

- Execute only dependency-ready nodes up to `max_concurrency`.
- Enforce `FAIL_FAST` vs `CONTINUE` behavior exactly as policy declares.
- Keep deterministic input reference resolution and summary aggregation.

### 5. Skill dispatch and binding safety

- Use declared registry activity type for routing (`mm.skill.execute` plus curated explicit activity types).
- Keep dispatch failure normalization in `SkillFailure` envelopes.
- Prevent inferred/guessed bindings; missing handlers fail explicitly.

### 6. Observability and payload discipline

- Keep progress query payloads structured (`total/pending/running/succeeded/failed/last_event/updated_at`).
- Publish optional `progress.json` and `plan_summary.json` artifacts for durable retrieval.
- Keep large outputs in artifact references, not inline workflow payload state.

### 7. Validation strategy

- Unit tests for registry validation and snapshot invariants.
- Unit tests for plan validation (schema checks, cycle checks, reference resolution).
- Unit tests for dispatcher routing and error handling.
- Unit tests for interpreter dependency ordering, failure policy, and progress/summary behavior.
- Run acceptance via `./tools/test_unit.sh`.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode**.
- This feature must ship production runtime changes and validation tests; docs/spec artifacts alone are non-compliant.
- Docs mode references remain informative only and cannot satisfy FR-014/FR-015 completion gates.

## Remediation Gates (Prompt B)

- Every `DOC-REQ-*` row must map to at least one FR, one implementation task, and one validation task.
- Runtime mode requires both production runtime code tasks and validation tasks in `tasks.md`; docs-only task sets are invalid.
- `spec.md`, `plan.md`, `tasks.md`, and `contracts/requirements-traceability.md` must keep deterministic `DOC-REQ-*` mappings with consistent file/module naming.
- Runtime validation evidence must remain explicit: `./tools/test_unit.sh` plus runtime scope checks (`--check tasks` and `--check diff` in runtime mode).

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime mode scope gate is explicitly satisfied by production runtime code tasks (`T001`, `T004-T013`, `T017-T020`, `T025-T030`, `T034-T037`) and validation tasks (`T014-T016`, `T021-T024`, `T031-T033`, `T039-T041`) in `tasks.md`.
- `DOC-REQ-*` traceability now includes deterministic implementation-task and validation-task mappings for every source requirement (`DOC-REQ-001` through `DOC-REQ-016`) in `contracts/requirements-traceability.md`.
- Cross-artifact determinism is preserved: spec intent (runtime-authoritative delivery), plan constraints, and task execution coverage align without contradictory scope language.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope controls in `tasks.md` so runtime/validation gating remains auditable.
- Added explicit Prompt B remediation status in `spec.md` so requirement-coverage enforcement is visible before implementation.

### Residual risks

- Skills runtime behavior spans registry, validation, dispatch, and interpreter modules; hidden coupling can still surface during implementation.
- Validation quality depends on depth of coverage in `tests/unit/workflows`; missing edge-case tests can permit policy/ordering regressions.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
