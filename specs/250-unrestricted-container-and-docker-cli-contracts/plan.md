# Implementation Plan: Unrestricted Container and Docker CLI Contracts

**Branch**: `250-unrestricted-container-and-docker-cli-contracts` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/250-unrestricted-container-and-docker-cli-contracts/spec.md`

## Summary

MM-501 is a runtime verification-first story. The repository already contains the unrestricted request schemas in `moonmind/schemas/workload_models.py`, unrestricted launcher behavior in `moonmind/workloads/docker_launcher.py`, mode-aware tool definitions and handler gating in `moonmind/workloads/tool_bridge.py`, runtime enforcement in `moonmind/workflows/temporal/activity_runtime.py`, normalized deployment mode settings in `moonmind/config/settings.py`, and unit plus hermetic integration coverage for unrestricted registration, validation, and dispatcher behavior. The planning work is therefore to preserve MM-501 traceability in MoonSpec artifacts, document the unrestricted contract explicitly, and carry forward focused unit and integration verification rather than plan broad production refactors.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `moonmind/schemas/workload_models.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py` | none beyond final verify | unit + integration |
| FR-002 | implemented_unverified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_docker_workload_launcher.py` | strengthen verification of structured unrestricted boundaries; implementation contingency only if verification exposes a gap | unit + integration |
| FR-003 | implemented_verified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | none beyond final verify | unit + integration |
| FR-004 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py` | none beyond final verify | unit + integration |
| FR-005 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `moonmind/workflow_docker_mode.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py` | none beyond final verify | unit + integration |
| FR-006 | implemented_unverified | `docs/ManagedAgents/DockerOutOfDocker.md`, `moonmind/schemas/workload_models.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py` | final verification must compare documented unrestricted example flows and contract boundaries against current runtime behavior | unit + final verify |
| FR-007 | implemented_verified | `spec.md` (Input), `specs/250-unrestricted-container-and-docker-cli-contracts/spec.md`, `plan.md`, `research.md`, `contracts/unrestricted-docker-workload-contract.md`, `quickstart.md` | preserve MM-501 through tasks and final verification output | traceability review |
| DESIGN-REQ-003 | implemented_verified | `moonmind/workflow_docker_mode.py`, `moonmind/config/settings.py`, `moonmind/workloads/tool_bridge.py`, `tests/unit/config/test_settings.py`, `tests/unit/workloads/test_workload_tool_bridge.py` | none beyond final verify | unit + integration |
| DESIGN-REQ-010 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py` | none beyond final verify | unit + integration |
| DESIGN-REQ-017 | implemented_verified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/tool_bridge.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workloads/test_docker_workload_launcher.py` | none beyond final verify | unit + integration |
| DESIGN-REQ-022 | implemented_unverified | `docs/ManagedAgents/DockerOutOfDocker.md`, `moonmind/schemas/workload_models.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py` | compare the documented unrestricted example-flow request shapes against current contract and add tests only if the review finds drift | unit + final verify |
| DESIGN-REQ-025 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py` | none beyond final verify | unit + integration |

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing Docker workload launcher/registry stack, pytest
**Storage**: No new persistent storage; existing workload registry, launcher, worker runtime wiring, and artifact-backed workload outputs only
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workloads/test_docker_workload_launcher.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/config/test_settings.py`
**Integration Testing**: `./tools/test_integration.sh`
**Target Platform**: MoonMind worker runtime and Docker-backed workload tool path
**Project Type**: Backend runtime contract and verification story for unrestricted Docker-backed workload execution
**Performance Goals**: Preserve deterministic request validation and low-overhead mode-aware tool dispatch with no new orchestration layers or storage
**Constraints**: Keep unrestricted execution deployment-gated and auditable; preserve the profile-backed meaning of `container.run_workload`; do not widen unrestricted execution into generic shell or session-side Docker authority; keep `container.run_docker` Docker-specific; preserve MM-501 traceability
**Scale/Scope**: One story covering unrestricted runtime-container requests, unrestricted Docker CLI requests, mode-aware denial, profile-backed-path preservation, and MM-501 traceability

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - stays on the existing Docker workload tool/runtime boundary rather than inventing a new execution plane.
- II. One-Click Agent Deployment: PASS - adds no new service or operator prerequisite.
- III. Avoid Vendor Lock-In: PASS - the work stays within MoonMind-owned workload contracts and Docker-facing runtime behavior.
- IV. Own Your Data: PASS - workload outputs remain artifact-backed on operator-controlled infrastructure.
- V. Skills Are First-Class and Easy to Add: PASS - unrestricted execution remains a tool-surface contract instead of a shell-side escape mechanism.
- VI. Replaceable AI Scaffolding: PASS - work focuses on durable policy boundaries and verifiable runtime behavior.
- VII. Runtime Configurability: PASS - preserves deployment-owned `MOONMIND_WORKFLOW_DOCKER_MODE` behavior.
- VIII. Modular and Extensible Architecture: PASS - changes stay localized to workload schemas, tool registration, runtime activity enforcement, and tests.
- IX. Resilient by Default: PASS - mode-aware denial remains explicit and non-retryable when unrestricted tools are forbidden.
- X. Facilitate Continuous Improvement: PASS - final verification can report concrete MM-501 evidence and any remaining drift between source design and runtime behavior.
- XI. Spec-Driven Development: PASS - MM-501 spec and Jira preset brief remain the source of truth.
- XII. Canonical Documentation Separation: PASS - desired-state source requirements remain in `docs/ManagedAgents/DockerOutOfDocker.md`; feature-local planning stays under `specs/250-unrestricted-container-and-docker-cli-contracts/`.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliasing or fallback behavior is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/250-unrestricted-container-and-docker-cli-contracts/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
└── contracts/
    └── unrestricted-docker-workload-contract.md
```

### Source Code (repository root)

```text
moonmind/config/
└── settings.py

moonmind/schemas/
└── workload_models.py

moonmind/workloads/
├── docker_launcher.py
├── registry.py
└── tool_bridge.py

moonmind/workflows/temporal/
├── activity_runtime.py
└── worker_runtime.py

tests/unit/config/
└── test_settings.py

tests/unit/workloads/
├── test_docker_workload_launcher.py
├── test_workload_contract.py
└── test_workload_tool_bridge.py

tests/unit/workflows/temporal/
├── test_temporal_worker_runtime.py
└── test_workload_run_activity.py

tests/integration/temporal/
└── test_integration_ci_tool_contract.py
```

**Structure Decision**: MM-501 stays entirely on the existing workflow Docker mode, unrestricted request schema, tool bridge, and Temporal runtime path. No new subsystem or persistent data model is needed; the remaining work is to keep unrestricted behavior traceable, contract-documented, and explicitly verified against the source design.

## Complexity Tracking

No constitution violations.
