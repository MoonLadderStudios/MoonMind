# Implementation Plan: Canonical Workflow Surface Naming

**Branch**: `040-spec-removal` | **Date**: 2026-03-01 | **Spec**: [`/specs/040-spec-removal/spec.md`](specs/040-spec-removal/spec.md)
**Input**: Feature specification from `/specs/040-spec-removal/spec.md`

## Summary

Standardize workflow terminology from legacy `SPEC_*`/`spec_*` naming to canonical `WORKFLOW_*`/`workflow*` across documentation, specs/contracts, and production runtime naming surfaces without changing billing semantics, queue behavior, model identifiers, or effort pass-through. Runtime orchestration mode is authoritative for this feature, and docs-mode guidance is kept aligned by using the same canonical map, validation commands, and traceability rules.

## Technical Context

**Language/Version**: Python 3.11, Bash, Markdown/YAML
**Primary Dependencies**: FastAPI, Celery 5.4, RabbitMQ 3.x, PostgreSQL, ripgrep (`rg`)
**Storage**: PostgreSQL (`workflow_runs` naming surfaces), filesystem artifacts under `var/artifacts/workflow_runs`
**Testing**: `./tools/test_unit.sh` plus deterministic token-scan verification commands
**Target Platform**: Linux containers via Docker Compose
**Project Type**: Monorepo backend/services/docs
**Performance Goals**: No measurable runtime throughput/latency regression from naming updates; verification scans complete in CI/local pre-merge workflow
**Constraints**: No compatibility transforms that alter execution semantics or billing-relevant values; fail-fast on unsupported legacy runtime inputs; no silent aliasing in active operational guidance
**Scale/Scope**: Migration touches high-volume `docs/` and `specs/` assets and selected runtime code/config/test surfaces for parity

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. One-Click Deployment with Smart Defaults | PASS | Naming changes retain existing default behavior and documented startup path. |
| II. Powerful Runtime Configurability | PASS | Runtime env/config keys are canonicalized with explicit precedence and documented behavior. |
| III. Modular and Extensible Architecture | PASS | Changes are scoped to contracts, adapters, routers, config surfaces, and docs without cross-cutting refactor. |
| IV. Avoid Exclusive Proprietary Vendor Lock-In | PASS | No vendor lock-in introduced; terminology updates remain adapter-neutral. |
| V. Self-Healing by Default | PASS | Retry and task-state semantics unchanged; only naming surfaces are normalized. |
| VI. Facilitate Continuous Improvement | PASS | Verification report and residual-risk checklist are explicit deliverables. |
| VII. Spec-Driven Development Is the Source of Truth | PASS | `spec.md` + this plan + traceability map every `DOC-REQ-*` including runtime intent. |
| VIII. Skills Are First-Class and Easy to Add | PASS | Skill/runtime references remain explicit and canonicalized in planning assets. |

## Project Structure

### Documentation (this feature)

```text
specs/040-spec-removal/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── workflow-naming-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/
├── core/
├── services/
└── config.template.toml

services/
└── orchestrator/

tests/
├── unit/
├── integration/
└── api_service/

docs/
specs/
tools/
```

**Structure Decision**: Use the existing monorepo layout. Apply naming updates in-place across docs/specs plus targeted runtime routers/config/service entrypoints and related tests.

## Phase 0: Research Outcomes

- Canonical map, runtime-vs-docs mode alignment, migration constraints, and verification strategy are documented in `research.md`.
- All prior `NEEDS CLARIFICATION` items are resolved via explicit decisions and validation rules.

## Phase 1: Design Outputs

- `data-model.md` defines migration entities, boundaries, runtime naming surfaces, and verification records.
- `contracts/workflow-naming-contract.md` defines canonical runtime contract expectations for env keys, routes, metrics, and artifact paths.
- `contracts/requirements-traceability.md` maps every `DOC-REQ-*` to FRs, implementation surfaces, and validation strategy.
- `quickstart.md` provides deterministic execution and verification flow for runtime mode and docs mode.

## Runtime vs Docs Mode Alignment

- Selected orchestration mode: **runtime implementation mode** (authoritative for this feature).
- Docs mode behavior is aligned by requiring the same canonical naming map, traceability coverage, and verification rules.
- Runtime mode includes production code + test updates; docs mode does not bypass runtime acceptance criteria for this feature.
- Validation gates:
  - Docs/spec gate: legacy-token scan across `docs/` + `specs/` surfaces.
  - Runtime gate: legacy-token scan across runtime code/config/tests + unit test execution via `./tools/test_unit.sh`.

## Post-Design Constitution Re-Check

| Principle | Status | Notes |
|---|---|---|
| I-VIII | PASS | No principle violations introduced by the design outputs; migration remains bounded, observable, and testable. |

## Implementation Readiness

- Planning artifacts are complete for `/speckit.tasks` generation and implementation execution.
- No unresolved clarifications remain.
- `DOC-REQ-*` coverage is explicit and includes runtime-intent requirement `DOC-REQ-011`.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Runtime mode scope gate is explicitly satisfied by production runtime code tasks (`T004-T006`, `T017-T021`) and validation tasks (`T015`, `T016`, `T022`, `T023`) in `tasks.md`.
- `DOC-REQ-*` traceability now includes deterministic implementation-task and validation-task mappings for every source requirement in `contracts/requirements-traceability.md`.
- Cross-artifact determinism is preserved: spec intent (`DOC-REQ-011` runtime authority), plan constraints, and task execution coverage align without contradictory scope language.

### Completed MEDIUM/LOW remediations

- Added explicit Prompt B scope controls in `tasks.md` to make runtime/validation gating auditable before implementation.
- Added explicit runtime-gate statement in task summary so readiness checks remain visible after task regeneration.

### Residual risks

- Large migration surface in `docs/` and `specs/` can still reintroduce legacy wording in future edits; this is mitigated by `T003`, `T022`, `T023`, and `T029`.
- Runtime naming updates may expose hidden legacy env usage at deploy time; this is mitigated by runtime validation tasks and fail-fast behavior in `T018`.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| None | N/A | N/A |
