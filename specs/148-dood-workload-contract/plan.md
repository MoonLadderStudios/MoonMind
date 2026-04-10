# Implementation Plan: Docker-Out-of-Docker Workload Contract

**Branch**: `148-dood-workload-contract` | **Date**: 2026-04-10 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/148-dood-workload-contract/spec.md`

## Summary

Preserve the completed Phase 0 DooD documentation boundary and implement the Phase 1 control-plane workload contract without launching Docker. The implementation adds canonical Pydantic models for workload requests, results, runner profiles, ownership metadata, and policy validation, plus a deployment-owned registry loader that fails closed and rejects unsafe profile/request shapes. Tests are written first around request/profile validation and the existing Phase 0 doc contract.

## Technical Context

**Language/Version**: Python 3.10+
**Primary Dependencies**: Pydantic v2, PyYAML, stdlib `json`, `pathlib`, `datetime`
**Storage**: Deployment-owned YAML/JSON files for runner profile registry input; no database changes
**Testing**: pytest through `./tools/test_unit.sh`
**Target Platform**: MoonMind control plane and Docker-capable `agent_runtime` worker fleet
**Project Type**: Python service modules
**Performance Goals**: Registry loading and request validation are synchronous bounded operations for small curated profile sets
**Constraints**: No Docker launch code in Phase 1; no arbitrary image strings in normal execution; fail closed on absent/invalid registry input; preserve Phase 0 docs boundary
**Scale/Scope**: One-shot workload contract and registry validation only, with launcher/tool/artifact phases left for later specs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Coverage |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Workloads remain MoonMind-orchestrated executable tools, not a competing agent model. |
| II. One-Click Agent Deployment | PASS | Registry defaults fail closed and do not add mandatory external services. |
| III. Avoid Vendor Lock-In | PASS | The contract is generic for curated workload profiles, not Unreal-only. |
| IV. Own Your Data | PASS | Durable truth remains bounded metadata and artifact refs. |
| V. Skills Are First-Class | PASS | Phase 1 preserves the future `tool.type = "skill"` path and does not change skill semantics. |
| VI. Bittersweet Lesson | PASS | The stable contract isolates future launcher implementation churn. |
| VII. Powerful Runtime Configurability | PASS | Runner profiles are deployment-owned configuration with deterministic validation. |
| VIII. Modular and Extensible | PASS | New models/registry live behind explicit modules and contracts. |
| IX. Resilient by Default | PASS | Validation rejects unsafe or unsupported inputs before launch. |
| X. Continuous Improvement | PASS | Results carry bounded diagnostics refs for later observability phases. |
| XI. Spec-Driven Development | PASS | Spec, plan, tasks, tests, and code are included in this feature. |
| XII. Canonical Docs vs tmp | PASS | Phase tracking updates remain in `docs/tmp/remaining-work/`. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases or fallback image behavior are introduced. |

## Project Structure

### Documentation (this feature)

```text
specs/148-dood-workload-contract/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── workload-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── workload_models.py
└── workloads/
    ├── __init__.py
    └── registry.py

tests/
└── unit/
    ├── docs/
    │   └── test_dood_phase0_contract.py
    └── workloads/
        └── test_workload_contract.py

docs/
└── tmp/
    └── remaining-work/
        └── ManagedAgents-DockerOutOfDocker.md
```

**Structure Decision**: Canonical payloads belong under `moonmind/schemas/` with other Pydantic contracts. Registry loading and cross-object policy validation belong under `moonmind/workloads/` so later Docker launcher and tool integration can depend on it without coupling schema definitions to Temporal activity code.

## Complexity Tracking

No constitution violations require complexity waivers.
