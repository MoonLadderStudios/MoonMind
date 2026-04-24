# Implementation Plan: DooD Phase 5 Hardening

**Branch**: `158-dood-phase5-hardening` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/158-dood-phase5-hardening/spec.md`

## Summary

Harden Docker-backed workload tools so MoonMind can treat DooD as a safe runtime capability by default. The implementation extends the existing workload contract, runner profile registry, Docker workload launcher, tool bridge, and agent-runtime worker bootstrap with fail-closed policy enforcement, explicit no-privileged launch posture, auth-volume isolation, profile/fleet concurrency controls, TTL-based orphan cleanup, and structured operator diagnostics. Runtime code changes and validation tests are mandatory; docs-only work is insufficient.

## Technical Context

**Language/Version**: Python 3.12 runtime, Pydantic v2 schemas, TypeScript/Vitest only if UI surfaces are touched 
**Primary Dependencies**: Existing MoonMind workload modules, Temporal activity runtime, Docker CLI through the configured Docker proxy, pytest/pytest-asyncio 
**Storage**: Existing workflow artifacts and bounded result metadata; no new database tables planned 
**Testing**: `./tools/test_unit.sh` for final unit verification, targeted `pytest tests/unit/workloads ...` for iteration 
**Target Platform**: Linux Docker Compose worker deployment with Docker-capable `agent_runtime` fleet 
**Project Type**: Single backend/runtime project with existing API/dashboard projection surfaces 
**Performance Goals**: Deny invalid workload requests before container launch; capacity checks must be constant-time in the local worker process; cleanup must scan only MoonMind workload-labeled containers 
**Constraints**: Fail closed for missing profile/capability/policy, do not leak secrets in diagnostics, do not give managed session containers Docker authority, keep workload identity separate from session identity 
**Scale/Scope**: One-shot workload containers only; per-profile and per-fleet limits bound heavy jobs such as Unreal workloads; bounded helper containers remain out of scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Plan Alignment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Keeps specialized workloads behind MoonMind orchestration and executable-tool contracts rather than rebuilding agent behavior. |
| II. One-Click Agent Deployment | PASS | Uses existing Docker Compose worker/Docker proxy assumptions and safe defaults. |
| III. Avoid Vendor Lock-In | PASS | Hardening is generic to Docker-backed workload profiles; Unreal remains a workload class, not a hardcoded platform dependency. |
| IV. Own Your Data | PASS | Runtime evidence remains in operator-owned artifacts and bounded metadata. |
| V. Skills Are First-Class | PASS | Preserves `tool.type = "skill"` entry for Docker-backed workload tools. |
| VI. Replaceable Scaffolding | PASS | Adds tests around contracts so launcher/policy implementation can evolve safely. |
| VII. Runtime Configurability | PASS | Operator-facing registry allowlist and fleet capacity controls are runtime configurable with safe defaults. |
| VIII. Modular Architecture | PASS | Changes stay within workload schemas, registry, launcher, tool bridge, and worker bootstrap boundaries. |
| IX. Resilient by Default | PASS | Adds timeout/cancel cleanup support, orphan sweeping, and explicit denial metadata. |
| X. Continuous Improvement | PASS | Denial and cleanup diagnostics make failures reviewable and actionable. |
| XI. Spec-Driven Development | PASS | This plan follows the current feature spec and requires validation tests. |
| XII. Canonical Docs vs Tmp | PASS | Runtime work is primary; temporary tracking stays under `local-only handoffs` if touched. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases or fallback semantics are introduced for internal workload contracts. |

## Project Structure

### Documentation (this feature)

```text
specs/158-dood-phase5-hardening/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── workload-hardening-contract.schema.json
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│ └── workload_models.py
├── workloads/
│ ├── __init__.py
│ ├── docker_launcher.py
│ ├── registry.py
│ └── tool_bridge.py
└── workflows/
 └── temporal/
 ├── activity_catalog.py
 ├── activity_runtime.py
 ├── worker_runtime.py
 └── workers.py

tests/
└── unit/
 ├── workloads/
 │ ├── test_docker_workload_launcher.py
 │ ├── test_workload_contract.py
 │ └── test_workload_tool_bridge.py
 └── workflows/
 └── temporal/
 ├── test_activity_catalog.py
 ├── test_temporal_worker_runtime.py
 ├── test_temporal_workers.py
 └── test_workload_run_activity.py
```

**Structure Decision**: Use the existing workload runtime module boundaries. Workload contract and policy validation stay in schemas/registry, Docker process behavior stays in the launcher, tool-path errors stay in the tool bridge, and fleet capability bootstrap stays in Temporal worker runtime/topology.

## Phase 0: Research

Research output is captured in [research.md](./research.md). Key decisions:

- Enforce policy before Docker launch and return stable non-secret denial reasons.
- Keep registry allowlists deployment-owned and fail closed.
- Use explicit no-privileged Docker launch posture for default workload containers.
- Use local worker-process concurrency guards for Phase 5 and rely on existing fleet topology for routing.
- Sweep only containers carrying MoonMind workload ownership labels and expired TTL metadata.

## Phase 1: Design and Contracts

Design artifacts:

- [data-model.md](./data-model.md)
- [contracts/workload-hardening-contract.schema.json](./contracts/workload-hardening-contract.schema.json)
- [quickstart.md](./quickstart.md)

No `DOC-REQ-*` identifiers are present in `spec.md`, so no source-document traceability contract is required for this feature.

## Post-Design Constitution Check

| Principle | Status | Re-check Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Design keeps workloads as MoonMind-owned tool invocations. |
| II. One-Click Agent Deployment | PASS | No new mandatory external services are introduced. |
| III. Avoid Vendor Lock-In | PASS | Contracts are runner-profile based and not Unreal-only. |
| IV. Own Your Data | PASS | Results remain local artifacts/metadata. |
| V. Skills Are First-Class | PASS | Tool bridge remains the invocation boundary. |
| VI. Replaceable Scaffolding | PASS | Validation is contract-oriented. |
| VII. Runtime Configurability | PASS | Policy/capacity settings remain operator-configurable. |
| VIII. Modular Architecture | PASS | Existing module boundaries are preserved. |
| IX. Resilient by Default | PASS | Cleanup and capacity denial paths are explicit. |
| X. Continuous Improvement | PASS | Diagnostics make policy denials reviewable. |
| XI. Spec-Driven Development | PASS | Plan maps to FR/SC coverage. |
| XII. Canonical Docs vs Tmp | PASS | No canonical doc rewrite is required. |
| XIII. Delete, Don't Deprecate | PASS | Unsupported inputs fail; no compatibility aliases are planned. |

## Complexity Tracking

No Constitution violations require justification.
