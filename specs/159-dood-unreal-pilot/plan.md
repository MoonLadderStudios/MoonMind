# Implementation Plan: DooD Unreal Pilot

**Branch**: `159-dood-unreal-pilot` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/159-dood-unreal-pilot/spec.md`

## Summary

Implement Phase 6 of the Docker-out-of-Docker rollout by making the existing executable-tool workload path usable for a curated Unreal pilot. The plan adds a deployment-owned default `unreal-5_3-linux` runner profile, extends the `unreal.run_tests` domain contract with deterministic report outputs, preserves approved cache-volume semantics, and validates the runtime path with focused workload and worker-bootstrap tests. Runtime mode is selected, so deliverables include production runtime code/config changes plus validation tests, not docs-only changes.

## Technical Context

**Language/Version**: Python 3.12 runtime, Pydantic v2 schemas, YAML profile config
**Primary Dependencies**: Existing `moonmind.workloads` modules, Temporal agent-runtime worker bootstrap, pytest/pytest-asyncio
**Storage**: Existing workload artifacts under `artifactsDir`; approved Docker named cache volumes for non-durable Unreal cache state
**Testing**: Targeted pytest for workload contract/tool bridge/launcher/worker bootstrap; final `./tools/test_unit.sh` for full unit verification
**Target Platform**: Linux Docker Compose deployment with Docker-capable `agent_runtime` fleet and Docker proxy access
**Project Type**: Single backend/runtime project with existing workload and Temporal worker modules
**Performance Goals**: Load the curated Unreal profile at worker startup without external lookup; reject invalid report/env/profile inputs before launch; preserve cache reuse without publishing cache contents as artifacts
**Constraints**: No direct Docker authority in managed session containers; no raw image/mount/device inputs through `unreal.run_tests`; no host networking or implicit devices; pinned non-`latest` image policy; real Unreal repo execution remains local/deployment validation because CI cannot assume Unreal assets or licenses
**Scale/Scope**: One-shot Unreal test workloads only; bounded helper containers and additional domain tools are out of scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Plan Alignment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Keeps Unreal execution behind MoonMind's executable-tool workload path instead of creating a bespoke agent runtime. |
| II. One-Click Agent Deployment | PASS | Ships a default profile file while preserving operator override through environment configuration. |
| III. Avoid Vendor Lock-In | PASS | Unreal is a curated profile/tool layered on the generic workload contract; the launcher remains profile-driven. |
| IV. Own Your Data | PASS | Durable logs, diagnostics, and reports remain artifacts under operator-controlled storage. |
| V. Skills Are First-Class | PASS | `unreal.run_tests` remains a `tool.type = "skill"` workload tool. |
| VI. Replaceable Scaffolding | PASS | Tests bind profile, report, cache, and artifact contracts so the runner image can evolve. |
| VII. Runtime Configurability | PASS | Operators can replace the default registry with `MOONMIND_WORKLOAD_PROFILE_REGISTRY` and keep image allowlists configurable. |
| VIII. Modular Architecture | PASS | Changes stay within workload config, tool bridge, and agent-runtime bootstrap boundaries. |
| IX. Resilient by Default | PASS | Existing timeout/cancel cleanup, bounded metadata, and artifact publication paths are reused. |
| X. Continuous Improvement | PASS | Failure diagnosis remains possible from artifacts and bounded workload metadata. |
| XI. Spec-Driven Development | PASS | This plan is generated from the current feature spec and maps to validation artifacts. |
| XII. Canonical Docs vs Tmp | PASS | Operator rollout notes stay in temporary remaining-work tracking, not canonical architecture prose. |
| XIII. Delete, Don't Deprecate | PASS | Unsupported runtime inputs fail validation; no compatibility aliases or fallback image semantics are added. |

## Project Structure

### Documentation (this feature)

```text
specs/159-dood-unreal-pilot/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── unreal-run-tests-contract.schema.json
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
config/
└── workloads/
    └── default-runner-profiles.yaml

moonmind/
├── workflows/
│   └── temporal/
│       └── worker_runtime.py
└── workloads/
    └── tool_bridge.py

tests/
└── unit/
    ├── workflows/
    │   └── temporal/
    │       └── test_temporal_worker_runtime.py
    └── workloads/
        ├── test_docker_workload_launcher.py
        ├── test_workload_contract.py
        └── test_workload_tool_bridge.py
```

**Structure Decision**: Use existing workload runtime boundaries. Profile availability belongs in deployment config and worker bootstrap; domain input conversion belongs in the workload tool bridge; launch/cache/artifact validation stays in workload tests.

## Phase 0: Research

Research output is captured in [research.md](./research.md). Key decisions:

- Ship a default deployment-owned registry file for the pilot profile.
- Pin an external Unreal runner image reference under approved registry policy.
- Model Unreal reports as declared workload outputs under `artifactsDir`.
- Keep Unreal caches as approved profile mounts, not tool inputs or durable outputs.

## Phase 1: Design and Contracts

Design artifacts:

- [data-model.md](./data-model.md)
- [contracts/unreal-run-tests-contract.schema.json](./contracts/unreal-run-tests-contract.schema.json)
- [quickstart.md](./quickstart.md)

No `DOC-REQ-*` identifiers are present in `spec.md`, and the request is not document-backed, so no `contracts/requirements-traceability.md` artifact is required.

## Post-Design Constitution Check

| Principle | Status | Re-check Notes |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Design keeps the Unreal pilot on the executable workload path. |
| II. One-Click Agent Deployment | PASS | Default config is checked in and override remains optional. |
| III. Avoid Vendor Lock-In | PASS | Runner profile contract remains generic outside the curated Unreal profile. |
| IV. Own Your Data | PASS | Reports/logs are declared artifacts; caches are explicitly non-durable. |
| V. Skills Are First-Class | PASS | Tool contract remains `unreal.run_tests`. |
| VI. Replaceable Scaffolding | PASS | Runner image can be rebuilt or mirrored without changing the tool contract. |
| VII. Runtime Configurability | PASS | Profile registry path and image allowlist remain runtime configuration. |
| VIII. Modular Architecture | PASS | No cross-cutting rewrite is planned. |
| IX. Resilient by Default | PASS | Cleanup and artifact behavior reuse existing hardened workload launcher paths. |
| X. Continuous Improvement | PASS | Pilot outcomes are diagnosable from bounded outputs. |
| XI. Spec-Driven Development | PASS | Design artifacts align with the current spec. |
| XII. Canonical Docs vs Tmp | PASS | Rollout notes stay in `docs/ManagedAgents/DockerOutOfDocker.md`. |
| XIII. Delete, Don't Deprecate | PASS | No legacy compatibility path is introduced. |

## Complexity Tracking

No Constitution violations require justification.
