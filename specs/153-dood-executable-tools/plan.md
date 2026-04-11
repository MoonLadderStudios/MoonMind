# Implementation Plan: DooD Executable Tool Exposure

**Branch**: `153-dood-executable-tools` | **Date**: 2026-04-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/153-dood-executable-tools/spec.md`

## Summary

Expose Phase 3 Docker-out-of-Docker workloads through MoonMind's existing executable tool path. The implementation adds curated tool definitions for `container.run_workload` and `unreal.run_tests`, routes their `docker_workload` capability to the existing Docker-capable `agent_runtime` fleet, converts tool inputs into validated `WorkloadRequest` payloads, invokes the Phase 2 workload launcher, and returns normal `ToolResult` metadata while preserving the managed-session/workload identity boundary.

## Technical Context

**Language/Version**: Python 3.10+ runtime code, Pydantic v2 schemas, pytest/Vitest test stack
**Primary Dependencies**: Existing executable tool registry/dispatcher, Temporal activity catalog and worker topology, Phase 1 workload models/runner profile registry, Phase 2 `DockerWorkloadLauncher`
**Storage**: No new durable database tables; uses pinned tool registry artifacts and existing workload result metadata. Artifact publication expansion remains a later phase.
**Testing**: `./tools/test_unit.sh` with focused pytest coverage for workload tool definitions, handler conversion, capability routing, worker initialization, and `MoonMind.Run` workflow-boundary routing
**Target Platform**: MoonMind Temporal deployment using the existing `agent_runtime` worker fleet with Docker proxy access
**Project Type**: Python service/runtime module within the existing MoonMind monorepo
**Performance Goals**: Tool-dispatch overhead should be small relative to workload container startup; workflow payloads remain bounded by returning compact workload metadata rather than raw large logs
**Constraints**: Runtime mode; required deliverables include production runtime code changes and validation tests; do not expose raw image, mount, device, or arbitrary env parameters to general plans; keep `tool.type = "agent_runtime"` reserved for true long-lived agent runtimes; keep Docker authority on the control-plane-owned worker fleet
**Scale/Scope**: One-shot workload tool exposure for two tools (`container.run_workload`, `unreal.run_tests`); no Phase 4 artifact/live-log expansion, Phase 5 hardening, Phase 6 Unreal image pilot, or Phase 7 bounded helper lifecycle

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Coverage |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Uses existing executable tool orchestration and workload launcher rather than creating a new agent model. |
| II. One-Click Agent Deployment | PASS | Reuses existing Docker Compose worker/proxy topology; no new mandatory external service. |
| III. Avoid Vendor Lock-In | PASS | Generic runner-profile-backed tool remains provider-neutral; Unreal is a curated domain wrapper over the same contract. |
| IV. Own Your Data | PASS | Results and metadata remain local/operator-controlled; no external SaaS storage. |
| V. Skills Are First-Class | PASS | Adds discoverable executable tools with declared input/output contracts and validation tests. |
| VI. Bittersweet Lesson | PASS | Keeps a thin bridge around stable contracts so the implementation remains easy to replace. |
| VII. Powerful Runtime Configurability | PASS | Runner profile selection remains deployment-owned policy rather than hardcoded arbitrary images/mounts. |
| VIII. Modular and Extensible | PASS | Adds workload tool bridge and routing updates without changing managed-session controllers. |
| IX. Resilient by Default | PASS | Uses existing validated workload request, timeout, cancellation, and cleanup semantics; adds workflow-boundary tests. |
| X. Continuous Improvement | PASS | Normal tool results expose structured workload status metadata suitable for run summaries. |
| XI. Spec-Driven Development | PASS | Spec, plan, research, data model, contracts, and quickstart trace runtime requirements. |
| XII. Canonical Docs vs tmp | PASS | Canonical docs remain desired-state references; implementation tracking stays in `docs/tmp/remaining-work/`. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility alias or legacy Docker workload path is introduced. |

**Post-Design Recheck**: PASS. The design artifacts preserve the same boundaries: `tool.type = "skill"` for Docker-backed workloads, existing `agent_runtime` fleet for Docker authority, and no managed-session verb overload.

## Project Structure

### Documentation (this feature)

```text
specs/153-dood-executable-tools/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
└── contracts/
    └── dood-executable-tools-contract.md
```

### Source Code (repository root)

```text
moonmind/
├── workloads/
│   ├── __init__.py
│   └── tool_bridge.py
└── workflows/
    └── temporal/
        ├── activity_catalog.py
        ├── activity_runtime.py
        └── worker_runtime.py

tests/
└── unit/
    ├── workloads/
    │   └── test_workload_tool_bridge.py
    └── workflows/
        └── temporal/
            ├── test_activity_catalog.py
            ├── test_activity_runtime.py
            ├── test_temporal_worker_runtime.py
            └── workflows/test_run_integration.py
```

**Structure Decision**: Keep the tool bridge under `moonmind/workloads/` because it converts executable-tool inputs into the workload request contract. Keep routing and worker initialization changes in the existing Temporal modules so Docker authority remains tied to the current `agent_runtime` fleet.

## Complexity Tracking

No constitution violations require complexity waivers.
