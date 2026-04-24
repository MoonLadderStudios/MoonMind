# Implementation Plan: Workspace, Mount, and Session-Boundary Isolation

**Branch**: `251-workspace-mount-session-boundary-isolation` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/251-workspace-mount-session-boundary-isolation/spec.md`

## Summary

MM-502 is a runtime verification-first story. The repository already contains the core workspace-boundary, session-association, and credential-mount controls in `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workloads/tool_bridge.py`, and `moonmind/workflows/temporal/activity_runtime.py`, plus strong unit coverage for workspace-root rejection, declared-output confinement, session association metadata, and explicit credential-mount policy. The remaining planning focus is to preserve MM-502 in MoonSpec artifacts, add or confirm hermetic integration coverage for session-assisted workload isolation at the dispatcher/runtime boundary, and verify that existing runtime behavior satisfies the source-design requirements without widening managed-session authority.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workloads/registry.py`, `moonmind/schemas/workload_models.py`, `tests/unit/workloads/test_workload_contract.py` | none beyond final verify | unit |
| FR-002 | implemented_unverified | `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | add verification tests for session-assisted isolation at dispatcher/runtime boundary; implement only if verification exposes a gap | unit + integration |
| FR-003 | implemented_unverified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | add boundary proof that session metadata remains association-only and does not surface session continuity outputs | unit + integration |
| FR-004 | implemented_verified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py` | none beyond final verify | unit |
| FR-005 | implemented_verified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py` | none beyond final verify | unit |
| FR-006 | implemented_unverified | `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | add integration proof that session-assisted tool routing respects the same workload isolation boundaries; implementation contingency only if mismatch appears | unit + integration |
| FR-007 | implemented_verified | `spec.md` (Input), `specs/251-workspace-mount-session-boundary-isolation/spec.md`, `plan.md`, `research.md`, `contracts/workload-isolation-contract.md`, `quickstart.md` | preserve MM-502 through tasks and final verification output | traceability review |
| DESIGN-REQ-002 | implemented_unverified | `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | add dispatcher/runtime verification for control-plane-owned workload identity | unit + integration |
| DESIGN-REQ-004 | implemented_unverified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | add session-assisted isolation verification | unit + integration |
| DESIGN-REQ-005 | implemented_verified | `moonmind/workloads/registry.py`, `moonmind/schemas/workload_models.py`, `tests/unit/workloads/test_workload_contract.py` | none beyond final verify | unit |
| DESIGN-REQ-013 | implemented_unverified | `moonmind/workloads/docker_launcher.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py` | add explicit verification that workload requests do not grant managed-session Docker authority | unit + integration |
| DESIGN-REQ-014 | implemented_verified | `moonmind/workloads/registry.py`, `moonmind/schemas/workload_models.py`, `tests/unit/workloads/test_workload_contract.py` | none beyond final verify | unit |
| DESIGN-REQ-015 | implemented_unverified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | add boundary verification for association-only metadata behavior | unit + integration |
| DESIGN-REQ-016 | implemented_verified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py` | none beyond final verify | unit |
| DESIGN-REQ-022 | implemented_unverified | `moonmind/workloads/registry.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py` | add integration boundary proof that routing and policy enforcement stay aligned | unit + integration |

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing Docker workload launcher/registry stack, pytest
**Storage**: No new persistent storage; existing workload metadata, artifact-backed workload outputs, and runtime labels only
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workloads/test_docker_workload_launcher.py`
**Integration Testing**: `./tools/test_integration.sh`
**Target Platform**: MoonMind worker runtime and Docker-backed workload tool path
**Project Type**: Backend runtime and verification story for Docker-backed workload isolation contracts
**Performance Goals**: Preserve deterministic request validation and workload dispatch with no additional orchestration layers or storage
**Constraints**: Keep workload launches inside MoonMind-owned task paths; preserve session/workload identity separation; keep auth volume inheritance explicit rather than implicit; preserve MM-502 traceability; do not widen session authority or add compatibility layers
**Scale/Scope**: One story covering workspace-root enforcement, mount/output confinement, session association metadata, default credential isolation, and dispatcher/runtime policy alignment for Docker-backed workload execution

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - stays on the existing workload tool and Temporal runtime boundaries instead of inventing a parallel session or container path.
- II. One-Click Agent Deployment: PASS - introduces no new service or operator prerequisite.
- III. Avoid Vendor Lock-In: PASS - work remains inside MoonMind-owned workload contracts and artifact-backed evidence.
- IV. Own Your Data: PASS - workload outputs and diagnostics remain on operator-controlled artifact storage.
- V. Skills Are First-Class and Easy to Add: PASS - Docker-backed workloads remain on the tool/skill path.
- VI. Replaceable AI Scaffolding: PASS - focuses on durable runtime validation and verification evidence, not agent-specific scaffolding.
- VII. Runtime Configurability: PASS - preserves deployment-owned Docker mode behavior and policy enforcement.
- VIII. Modular and Extensible Architecture: PASS - changes stay localized to workload contract/runtime boundaries and feature-local planning artifacts.
- IX. Resilient by Default: PASS - policy denials, isolation rules, and bounded metadata stay explicit and testable.
- X. Facilitate Continuous Improvement: PASS - final verification will produce concrete MM-502 evidence and remaining gaps if any remain.
- XI. Spec-Driven Development: PASS - MM-502 Jira brief and spec remain the source of truth.
- XII. Canonical Documentation Separation: PASS - source requirements remain in `docs/ManagedAgents/DockerOutOfDocker.md`; implementation planning stays feature-local.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliasing or hidden fallback behavior is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/251-workspace-mount-session-boundary-isolation/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── workload-isolation-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/schemas/
└── workload_models.py

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
└── test_workload_run_activity.py

tests/integration/temporal/
├── test_integration_ci_tool_contract.py
└── test_profile_backed_workload_contract.py
```

**Structure Decision**: MM-502 stays entirely on the existing Docker-backed workload path. No new API surface, persistent model, or workflow type is required; the likely code change, if any, is a focused integration boundary test or small boundary hardening discovered during verification.

## Complexity Tracking

No constitution violations.
