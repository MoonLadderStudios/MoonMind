# Implementation Plan: Enforce Docker Workflow Modes and Registry Gating

**Branch**: `248-enforce-docker-workflow-modes-and-registry-gating` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/248-enforce-docker-workflow-modes-and-registry-gating/spec.md`

## Summary

Plan MM-499 as a runtime implementation story that replaces the current boolean workflow Docker gate with an explicit tri-mode deployment contract. The repository already contains Docker workload tooling, curated runner-profile validation, and disabled-mode denial paths in `moonmind/workloads/tool_bridge.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/workflows/temporal/worker_runtime.py`, but those paths are wired through `MOONMIND_WORKFLOW_DOCKER_ENABLED` and do not provide the required `disabled` / `profiles` / `unrestricted` configuration model or mode-aware registry exposure. The implementation plan is therefore to add the canonical `MOONMIND_WORKFLOW_DOCKER_MODE` setting, propagate normalized mode-aware policy into registration and runtime enforcement, preserve curated profile-backed tools as the normal path, and add explicit unit plus hermetic integration coverage for mode-specific discovery and denial behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `workflow_docker_enabled` in `moonmind/config/settings.py`; handler/runtime gating in `moonmind/workloads/tool_bridge.py` and `moonmind/workflows/temporal/activity_runtime.py` | replace boolean gate with canonical tri-mode workflow Docker policy and propagate through worker/runtime setup | unit + integration |
| FR-002 | missing | current default is boolean `True` via `workflow_docker_enabled`; no `profiles` mode setting exists | add normalized `MOONMIND_WORKFLOW_DOCKER_MODE` with default `profiles` and update configuration tests | unit |
| FR-003 | missing | no mode parser or unsupported-value validation exists; only boolean env parsing in `tests/unit/config/test_settings.py` | add fail-fast mode normalization and invalid-value tests | unit |
| FR-004 | partial | disabled-mode denial exists in `build_workload_tool_handler`, `TemporalAgentRuntimeActivities.workload_run`, and tests; handlers are still registered so discovery omission is missing | make disabled mode omit Docker-backed tools from registry exposure and keep non-retryable runtime denial for direct invocation | unit + integration |
| FR-005 | missing | curated profile-backed tools exist, but there is no profiles-mode distinction in settings or registration | introduce profiles-mode registration and enforcement so curated/profile-backed tools remain exposed while unrestricted tools stay hidden | unit + integration |
| FR-006 | missing | unrestricted container/Docker CLI contract is documented but not implemented on the workflow Docker mode path; no unrestricted tool exposure control exists | add unrestricted-mode exposure/enforcement contract without broadening session-side Docker authority | unit + integration |
| FR-007 | partial | runtime denial exists only for the global disabled boolean gate; discovery and execution can diverge by mode | centralize mode-aware policy so discovery and runtime execution share one decision path | unit + integration |
| FR-008 | partial | `spec.md` preserves MM-499 and the Jira brief; downstream plan, tasks, and verification artifacts do not yet all exist | preserve MM-499 through plan, tasks, verification, and delivery metadata | traceability review |
| DESIGN-REQ-001 | partial | deployment-owned boolean gate exists, but not the required explicit mode model | implement deployment-owned tri-mode policy surface | unit + integration |
| DESIGN-REQ-003 | missing | no canonical mode enum or normalized mode type exists | add canonical disabled/profiles/unrestricted mode model and update consumers | unit |
| DESIGN-REQ-007 | missing | omitted mode does not resolve to `profiles`; current omission resolves to boolean enabled | change default behavior to `profiles` and verify in settings + registration tests | unit |
| DESIGN-REQ-008 | missing | unsupported values are not validated against a mode enum | add fail-fast invalid-mode validation at settings load | unit |
| DESIGN-REQ-009 | partial | disabled mode denies execution, but registry exposure still includes Docker-backed tools | omit Docker-backed tools from discovery in disabled mode and preserve runtime denial | unit + integration |
| DESIGN-REQ-010 | missing | no profiles-mode allowlist of curated/profile-backed vs unrestricted tools exists | define profiles-mode exposure matrix and test tool registration/dispatch against it | unit + integration |
| DESIGN-REQ-011 | missing | no unrestricted-mode behavior or protection against session-authority widening is enforced via workflow mode | add unrestricted-mode contract and coverage that session-side Docker authority remains unchanged | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, existing Docker workload launcher/registry stack, pytest  
**Storage**: No new persistent storage; deployment config plus existing workload registry/runtime wiring only  
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` with focused config, workload-tool, and Temporal runtime suites  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage of mode-aware tool registration/dispatch behavior  
**Target Platform**: MoonMind worker runtime and Docker-backed workload tool path  
**Project Type**: Backend runtime/configuration story with worker/tool contract changes  
**Performance Goals**: Preserve bounded tool registration and validation overhead; mode checks remain cheap and deterministic at settings load, registry exposure, and runtime dispatch  
**Constraints**: Use the canonical `MOONMIND_WORKFLOW_DOCKER_MODE` surface; keep the mode deployment-owned; do not add compatibility aliases or hidden fallback behavior; preserve curated profile-backed tools as the normal path; keep session-side Docker authority unchanged in unrestricted mode  
**Scale/Scope**: One story covering workflow Docker mode normalization, registry exposure, runtime enforcement, and traceability for MM-499

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - extends the existing Docker workload tool/runtime boundary instead of introducing a new execution path.
- II. One-Click Agent Deployment: PASS - adds configuration normalization and tests only; no new service or external dependency is required.
- III. Avoid Vendor Lock-In: PASS - the story modifies MoonMind-owned workload policy, not vendor-specific behavior.
- IV. Own Your Data: PASS - no new data leaves operator-controlled systems.
- V. Skills Are First-Class and Easy to Add: PASS - preserves tool-driven workload routing and does not fork the skill/tool system.
- VI. Replaceable AI Scaffolding: PASS - work is centered on durable runtime configuration and testable policy boundaries.
- VII. Runtime Configurability: PASS - promotes the documented deployment-owned mode into a validated runtime configuration surface.
- VIII. Modular and Extensible Architecture: PASS - confines changes to settings, worker/runtime policy wiring, and workload tool registration.
- IX. Resilient by Default: PASS - explicit mode validation and deterministic denial behavior improve fail-fast safety.
- X. Facilitate Continuous Improvement: PASS - downstream verification can report concrete mode-coverage evidence and any remaining drift.
- XI. Spec-Driven Development: PASS - MM-499 spec and Jira brief remain the source of truth for the implementation plan.
- XII. Canonical Documentation Separation: PASS - desired-state source requirements remain in `docs/ManagedAgents/DockerOutOfDocker.md`; implementation planning stays feature-local.
- XIII. Pre-release Compatibility Policy: PASS - the plan assumes the legacy boolean workflow Docker setting is removed in favor of the canonical mode contract rather than preserved via aliasing.

## Project Structure

### Documentation (this feature)

```text
specs/248-enforce-docker-workflow-modes-and-registry-gating/
├── spec.md
├── plan.md
├── research.md
├── quickstart.md
├── contracts/
│   └── workflow-docker-mode-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/config/
└── settings.py

moonmind/schemas/
└── workload_models.py

moonmind/workloads/
├── docker_launcher.py
├── tool_bridge.py
└── registry.py

moonmind/workflows/temporal/
├── activity_runtime.py
└── worker_runtime.py

tests/unit/config/
└── test_settings.py

tests/unit/workloads/
├── test_workload_contract.py
└── test_workload_tool_bridge.py

tests/unit/workflows/temporal/
├── test_activity_runtime.py
├── test_temporal_worker_runtime.py
└── test_workload_run_activity.py

tests/integration/temporal/
└── test_integration_ci_tool_contract.py
```

**Structure Decision**: Keep MM-499 scoped to workflow configuration normalization, mode-aware workload tool registration, unrestricted request schema/policy support, and runtime denial wiring across the existing Docker workload path. No new storage or separate subsystem is needed; the main gap is replacing the boolean gate with one shared tri-mode policy model and proving discovery and execution stay aligned.

## Complexity Tracking

No constitution violations.
