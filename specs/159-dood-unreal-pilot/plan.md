# Implementation Plan: DooD Unreal Pilot

**Branch**: `159-dood-unreal-pilot` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/159-dood-unreal-pilot/spec.md`

## Summary

Implement Phase 6 by making the existing DooD executable-tool path usable for a curated Unreal pilot. The change adds a deployment-owned default workload profile registry with `unreal-5_3-linux`, extends the `unreal.run_tests` domain contract with deterministic report outputs, and verifies launcher/artifact/cache behavior through focused unit tests. The work remains a tool-backed workload path, not a managed session runtime.

## Technical Context

**Language/Version**: Python 3.12, Pydantic v2, YAML registry config  
**Primary Dependencies**: `moonmind.workloads`, Temporal agent-runtime worker bootstrap, pytest  
**Storage**: Existing workload artifacts under `artifactsDir`; approved Docker named cache volumes only  
**Testing**: Targeted pytest for workload contract/tool bridge/launcher/worker bootstrap; final `./tools/test_unit.sh` when feasible  
**Target Platform**: Docker-capable `agent_runtime` fleet with Docker proxy and `agent_workspaces` volume  
**Constraints**: No raw Docker authority in sessions, no arbitrary image/mount/device inputs, cache volumes are non-durable operational state, fail closed on invalid inputs  

## Constitution Check

| Principle | Status | Plan Alignment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Unreal execution remains a MoonMind tool-backed workload. |
| II. One-Click Agent Deployment | PASS | A default profile file is shipped; operators can override with env config. |
| III. Avoid Vendor Lock-In | PASS | Unreal is a curated profile/tool layered on generic workload contracts. |
| IV. Own Your Data | PASS | Durable outputs remain artifacts, not container-local state. |
| V. Skills Are First-Class | PASS | Uses `tool.type = "skill"` and existing DooD tool bridge. |
| VI. Replaceable Scaffolding | PASS | Tests bind the contract while image implementation can evolve. |
| VII. Runtime Configurability | PASS | Registry path and image registry allowlist remain environment-configurable. |
| VIII. Modular Architecture | PASS | Changes stay in config, workload bridge, and worker bootstrap. |
| IX. Resilient by Default | PASS | Existing timeout/cancel cleanup and artifact publication paths are reused. |
| X. Continuous Improvement | PASS | Bounded metadata and reports make pilot failures diagnosable. |
| XI. Spec-Driven Development | PASS | This spec/plan/tasks set governs runtime work. |
| XII. Canonical Docs vs Tmp | PASS | Operator enablement notes stay in tmp rollout tracking. |
| XIII. Delete, Don't Deprecate | PASS | Unsupported input values fail validation; no compatibility aliases. |

## Project Structure

```text
specs/159-dood-unreal-pilot/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── unreal-run-tests-contract.schema.json
└── tasks.md

config/workloads/default-runner-profiles.yaml
moonmind/workloads/tool_bridge.py
moonmind/workflows/temporal/worker_runtime.py
tests/unit/workloads/test_workload_contract.py
tests/unit/workloads/test_workload_tool_bridge.py
tests/unit/workloads/test_docker_workload_launcher.py
tests/unit/workflows/temporal/test_temporal_worker_runtime.py
```

## Implementation Notes

- Load the built-in registry when `MOONMIND_WORKLOAD_PROFILE_REGISTRY` is unset.
- Preserve operator override semantics when `MOONMIND_WORKLOAD_PROFILE_REGISTRY` is set.
- Keep report paths relative to `artifactsDir` and publish them through `declaredOutputs`.
- Use profile env allowlisting for Unreal-specific env keys; do not add generic pass-through.
