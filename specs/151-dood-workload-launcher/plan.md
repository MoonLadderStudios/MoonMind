# Implementation Plan: Docker-Out-of-Docker Workload Launcher

**Branch**: `151-dood-workload-launcher` | **Date**: 2026-04-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/151-dood-workload-launcher/spec.md`

## Summary

Implement Phase 2 of the Docker-out-of-Docker plan by adding a MoonMind-owned workload launcher that consumes the existing validated workload contract, runs one bounded Docker workload container through the Docker-capable `agent_runtime` fleet, captures bounded execution metadata, and performs deterministic cleanup. The runtime path remains separate from managed-session operations and is covered by focused unit tests for launch construction, timeout cleanup, orphan lookup, activity binding, and worker topology.

## Technical Context

**Language/Version**: Python 3.10+  
**Primary Dependencies**: Pydantic v2 workload models, Python `asyncio`, Docker CLI through the existing `DOCKER_HOST` / docker-proxy wiring  
**Storage**: Existing `agent_workspaces` Docker volume for task workspaces; optional profile-declared Docker cache volumes; no database changes  
**Testing**: `./tools/test_unit.sh` with focused pytest unit tests for workload launch and Temporal worker routing  
**Target Platform**: MoonMind Temporal `agent_runtime` worker fleet in the local Docker Compose deployment  
**Project Type**: Python service/runtime module inside the existing MoonMind repository  
**Performance Goals**: Launcher overhead remains bounded relative to container startup; stdout/stderr captured as bounded metadata to avoid unbounded workflow payloads  
**Constraints**: Runtime mode; deliver production runtime code and validation tests, not docs/spec-only work; preserve managed-session/workload separation; use approved runner profiles instead of arbitrary request images or mounts; cleanup must be bounded on timeout/cancel  
**Scale/Scope**: One-shot workload containers only; no generic executable tool exposure, artifact publishing expansion, policy hardening, Unreal pilot, or bounded helper container lifecycle in this phase

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Coverage |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | MoonMind orchestrates workload launch and cleanup while preserving managed-agent boundaries. |
| II. One-Click Agent Deployment | PASS | Uses existing Docker Compose worker, docker-proxy, and `agent_workspaces` wiring; no mandatory new external service. |
| III. Avoid Vendor Lock-In | PASS | Profile-driven launcher is generic and not tied to Unreal or a vendor-specific runtime. |
| IV. Own Your Data | PASS | Workspace, stdout/stderr metadata, and diagnostics remain local/operator-controlled. |
| V. Skills Are First-Class | PASS | Keeps this as a distinct workload capability suitable for later executable tool integration. |
| VI. Bittersweet Lesson | PASS | Docker execution details are isolated behind a replaceable launcher module with contract tests. |
| VII. Powerful Runtime Configurability | PASS | Docker host, Docker binary, profile registry, workspace root, and profile-selected limits are runtime-configured. |
| VIII. Modular and Extensible | PASS | New behavior lives under workload modules and worker/activity routing boundaries. |
| IX. Resilient by Default | PASS | Timeout/cancel cleanup and orphan lookup are planned and validated. |
| X. Continuous Improvement | PASS | Results include bounded diagnostics metadata for later artifact and observability phases. |
| XI. Spec-Driven Development | PASS | Spec, plan, design artifacts, and later tasks trace the runtime scope. |
| XII. Canonical Docs vs tmp | PASS | Phase tracking remains in `docs/tmp/remaining-work/`; canonical docs are not converted into migration checklists. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility shim or legacy workload launch alias is introduced. |

**Post-Design Recheck**: PASS. Research and design artifacts preserve the same boundaries: one-shot workload launcher, existing Docker-capable fleet, no managed-session verb overload, runtime code plus validation tests.

## Project Structure

### Documentation (this feature)

```text
specs/151-dood-workload-launcher/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── workload-launcher-contract.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
├── workloads/
│   ├── __init__.py
│   ├── docker_launcher.py
│   └── registry.py
├── schemas/
│   └── workload_models.py
└── workflows/
    └── temporal/
        ├── activity_catalog.py
        ├── activity_runtime.py
        ├── worker_runtime.py
        └── workers.py

tests/
└── unit/
    ├── workloads/
    │   ├── test_workload_contract.py
    │   └── test_docker_workload_launcher.py
    └── workflows/
        └── temporal/
            ├── test_activity_catalog.py
            ├── test_temporal_worker_runtime.py
            ├── test_temporal_workers.py
            └── test_workload_run_activity.py
```

**Structure Decision**: Keep the launcher under `moonmind/workloads/` beside the Phase 1 registry and schema contracts. Keep Temporal routing and activity binding changes in the existing `moonmind/workflows/temporal/` modules so the `agent_runtime` fleet owns Docker workload execution without expanding managed-session controller responsibilities.

## Complexity Tracking

No constitution violations require complexity waivers.
