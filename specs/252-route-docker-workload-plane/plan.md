# Implementation Plan: Shared Docker Workload Execution Plane

**Branch**: `252-route-docker-workload-plane` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/252-route-docker-workload-plane/spec.md`

## Summary

MM-503 is a runtime verification-first planning story focused on proving and, if needed, tightening the shared Docker workload execution plane that already exists in the repo. The current implementation already routes Docker-backed tools through `mm.tool.execute` with `docker_workload` capability, derives deterministic workload ownership labels, publishes bounded workload metadata, and applies launcher-owned timeout and cleanup behavior in `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/registry.py`, and `moonmind/workflows/temporal/activity_runtime.py`. The main planning gaps are to verify the shared contract across curated and unrestricted tool classes, confirm metadata coverage for runtime mode and access class, and preserve MM-503 plus the original Jira preset brief through the feature-local MoonSpec artifacts.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_activity_runtime.py` | add verification across curated and unrestricted tool registrations/activity bindings; implement only if execution paths diverge | unit + integration |
| FR-002 | partial | `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_docker_workload_launcher.py` | verify whether current metadata fully covers runtime mode plus workload access class for every launch class; add missing metadata if needed and test it | unit + integration |
| FR-003 | implemented_unverified | `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | add cross-class verification for profile-backed, helper, unrestricted container, and Docker CLI outcome shaping; implement contingency if shapes diverge | unit + integration |
| FR-004 | implemented_unverified | `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | add focused verification for timeout/cancellation semantics on unrestricted and structured paths; harden launcher only if verification exposes inconsistency | unit + integration |
| FR-005 | implemented_unverified | `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workloads/test_workload_contract.py` | add proof that structured cleanup remains owned while arbitrary Docker-created resources require reliable ownership evidence; implement only if janitor/cleanup boundaries are incomplete | unit + integration |
| FR-006 | implemented_unverified | `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_activity_runtime.py` | add verification that logical capability requirements, not current fleet placement, define the observable contract; no production refactor unless verification shows fleet-coupled behavior | unit |
| FR-007 | implemented_verified | `docs/tmp/jira-orchestration-inputs/MM-503-moonspec-orchestration-input.md`, `specs/252-route-docker-workload-plane/spec.md`, `plan.md`, `research.md`, `contracts/shared-docker-workload-plane-contract.md`, `quickstart.md` | preserve MM-503 through tasks and final verification output | traceability review |
| DESIGN-REQ-006 | implemented_unverified | `moonmind/workloads/tool_bridge.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_activity_runtime.py` | verify all DooD tool definitions and runtime bindings stay on `docker_workload` capability | unit + integration |
| DESIGN-REQ-019 | partial | `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_docker_workload_launcher.py` | confirm deterministic metadata covers all required label dimensions, especially runtime mode and access class, across launch classes | unit + integration |
| DESIGN-REQ-020 | implemented_unverified | `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workflows/temporal/test_activity_runtime.py` | verify logical capability remains the public contract independent of physical fleet placement assumptions | unit |
| DESIGN-REQ-023 | implemented_unverified | `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | add boundary proof for timeout/cancellation/cleanup consistency across structured and unrestricted classes | unit + integration |
| DESIGN-REQ-024 | implemented_unverified | `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_docker_workload_launcher.py` | verify structured cleanup ownership versus arbitrary Docker resource cleanup limits; implement contingency if cleanup boundaries are too permissive | unit + integration |

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing Docker workload launcher/registry stack, pytest
**Storage**: No new persistent storage; existing workload metadata, artifact-backed workload outputs, and runtime labels only
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_activity_runtime.py`
**Integration Testing**: `./tools/test_integration.sh`
**Target Platform**: MoonMind worker runtime and Docker-backed workload tool path
**Project Type**: Backend runtime and verification story for Docker-backed workload routing, metadata, timeout, and cleanup contracts
**Performance Goals**: Preserve deterministic workload dispatch and bounded launcher behavior without adding new orchestration layers or persistence
**Constraints**: Keep the logical `docker_workload` capability stable; preserve shared `mm.tool.execute` routing; maintain bounded timeout/cancellation/cleanup semantics; avoid fleet-coupled behavior; preserve MM-503 traceability; do not add compatibility layers
**Scale/Scope**: One story covering the shared execution plane for profile-backed workloads, helpers, unrestricted runtime containers, and unrestricted Docker CLI execution where deployment policy allows them

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - stays on the existing tool/activity execution path instead of inventing a separate Docker orchestration surface.
- II. One-Click Agent Deployment: PASS - introduces no new operator prerequisite or service dependency.
- III. Avoid Vendor Lock-In: PASS - behavior remains behind MoonMind-owned workload contracts rather than vendor-specific runtime logic.
- IV. Own Your Data: PASS - workload metadata, diagnostics, and artifacts remain on operator-controlled storage.
- V. Skills Are First-Class and Easy to Add: PASS - Docker-backed tools remain modeled as executable skills on the pinned tool registry path.
- VI. Replaceable AI Scaffolding: PASS - work is on durable runtime boundaries, not transient agent cognition scaffolding.
- VII. Runtime Configurability: PASS - preserves deployment-owned Docker mode and capability routing behavior.
- VIII. Modular and Extensible Architecture: PASS - changes remain localized to workload contracts, launcher behavior, and Temporal bindings.
- IX. Resilient by Default: PASS - timeout, cancellation, and cleanup semantics stay explicit and testable.
- X. Facilitate Continuous Improvement: PASS - final verification can produce concrete MM-503 evidence and surface any remaining cross-class gaps.
- XI. Spec-Driven Development: PASS - MM-503 Jira brief and feature spec remain the planning source of truth.
- XII. Canonical Documentation Separation: PASS - runtime source requirements stay in `docs/ManagedAgents/DockerOutOfDocker.md`; implementation planning remains feature-local.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliases or hidden fallback paths are proposed.

## Project Structure

### Documentation (this feature)

```text
specs/252-route-docker-workload-plane/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── shared-docker-workload-plane-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workloads/
├── docker_launcher.py
├── registry.py
└── tool_bridge.py

moonmind/workflows/temporal/
└── activity_runtime.py

tests/unit/workloads/
├── test_docker_workload_launcher.py
├── test_workload_contract.py
└── test_workload_tool_bridge.py

tests/unit/workflows/temporal/
└── test_activity_runtime.py

tests/integration/temporal/
└── test_profile_backed_workload_contract.py
```

**Structure Decision**: MM-503 stays entirely on the existing Docker-backed workload tool and Temporal activity path. No new API surface, database model, or workflow type is required; the likely implementation is targeted test additions plus small boundary hardening only if cross-class verification exposes metadata or cleanup inconsistencies.

## Complexity Tracking

No constitution violations.
