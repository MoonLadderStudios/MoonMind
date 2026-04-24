# Implementation Plan: Profile-Backed Workload Contracts

**Branch**: `249-profile-backed-workload-contracts` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/249-profile-backed-workload-contracts/spec.md`

## Summary

MM-500 is a runtime verification-first story. The repository already contains the core profile-backed workload contract in `moonmind/schemas/workload_models.py`, `moonmind/workloads/registry.py`, `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py`, and `moonmind/workflows/temporal/activity_runtime.py`, plus strong unit coverage for one-shot workloads, bounded helpers, disabled-mode denial, and curated workload tools. The remaining orchestration work is to capture MM-500 in MoonSpec artifacts, add a hermetic integration boundary that proves the existing contract at the dispatcher/runtime edge, and verify that the current code satisfies the source-design requirements without widening the profile-backed path.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `moonmind/schemas/workload_models.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | none beyond final verify | unit + integration |
| FR-002 | implemented_verified | `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | none beyond final verify | unit + integration |
| FR-003 | implemented_verified | `moonmind/schemas/workload_models.py`, `moonmind/workloads/tool_bridge.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | none beyond final verify | unit + integration |
| FR-004 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `moonmind/workloads/docker_launcher.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | none beyond final verify | unit + integration |
| FR-005 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py` | none beyond final verify | unit + integration |
| FR-006 | implemented_verified | `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/workloads/tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | none beyond final verify | unit + integration |
| FR-007 | implemented_verified | `docs/tmp/jira-orchestration-inputs/MM-500-moonspec-orchestration-input.md`, `specs/249-profile-backed-workload-contracts/spec.md`, `plan.md`, `research.md`, `contracts/profile-backed-workload-contract.md`, `quickstart.md`, `tasks.md` | preserve MM-500 through final verification output | traceability review |
| DESIGN-REQ-012 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | none beyond final verify | unit + integration |
| DESIGN-REQ-017 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `moonmind/schemas/workload_models.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | none beyond final verify | unit + integration |
| DESIGN-REQ-018 | implemented_verified | `moonmind/workloads/registry.py`, `moonmind/workloads/docker_launcher.py`, `tests/unit/workloads/test_workload_contract.py`, `tests/unit/workloads/test_docker_workload_launcher.py`, `tests/integration/temporal/test_profile_backed_workload_contract.py` | none beyond final verify | unit + integration |
| DESIGN-REQ-025 | implemented_verified | `moonmind/workloads/tool_bridge.py`, `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/integration/temporal/test_integration_ci_tool_contract.py` | none beyond final verify | unit + integration |

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing Docker workload launcher/registry stack, pytest
**Storage**: No new persistent storage; existing workload registry, launcher, and artifact-backed workload outputs only
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py tests/unit/workloads/test_docker_workload_launcher.py`
**Integration Testing**: `./tools/test_integration.sh`
**Target Platform**: MoonMind worker runtime and Docker-backed workload tool path
**Project Type**: Backend runtime and verification story for Docker-backed workload contracts
**Performance Goals**: Preserve deterministic low-overhead request validation and workload dispatch with no additional runtime storage or orchestration layers
**Constraints**: Keep the workload and helper tool contracts profile-backed; do not widen `container.run_workload`; keep helper lifecycle explicitly bounded; preserve disabled-mode deterministic denial; keep curated tools aligned with the same runner-profile model; preserve MM-500 traceability
**Scale/Scope**: One story covering profile-backed one-shot workloads, bounded helpers, curated-tool alignment, disabled-mode denial, and MoonSpec traceability for MM-500

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - continues to use the existing workload tool and Temporal runtime boundaries instead of inventing a new execution path.
- II. One-Click Agent Deployment: PASS - no new service or operator prerequisite is introduced.
- III. Avoid Vendor Lock-In: PASS - all changes stay inside MoonMind-owned workload contracts.
- IV. Own Your Data: PASS - workload outputs remain artifact-backed on operator-controlled infrastructure.
- V. Skills Are First-Class and Easy to Add: PASS - the story keeps workload execution on the tool/skill path.
- VI. Replaceable AI Scaffolding: PASS - work focuses on durable runtime policy and evidence, not agent-specific scaffolding.
- VII. Runtime Configurability: PASS - preserves deployment-owned Docker mode behavior already normalized in runtime settings.
- VIII. Modular and Extensible Architecture: PASS - changes stay localized to feature artifacts and an integration boundary test.
- IX. Resilient by Default: PASS - disabled-mode denial and bounded helper ownership remain explicit and test-covered.
- X. Facilitate Continuous Improvement: PASS - final verification will produce concrete MM-500 evidence and gaps if any remain.
- XI. Spec-Driven Development: PASS - MM-500 Jira brief and spec remain the source of truth.
- XII. Canonical Documentation Separation: PASS - source requirements remain in `docs/ManagedAgents/DockerOutOfDocker.md`; implementation planning stays feature-local.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliasing or fallback behavior is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/249-profile-backed-workload-contracts/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── profile-backed-workload-contract.md
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

**Structure Decision**: MM-500 stays entirely on the existing Docker-backed workload path. No new data model, API router, or workflow type is needed; the only code addition is a focused `integration_ci` dispatcher-boundary test file that proves the already-implemented profile-backed contract at the integration layer.

## Complexity Tracking

No constitution violations.
