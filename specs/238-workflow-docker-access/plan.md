# Implementation Plan: Workflow Docker Access Setting

**Branch**: `238-workflow-docker-access` | **Date**: 2026-04-22 | **Spec**: `specs/238-workflow-docker-access/spec.md` 
**Input**: Single-story feature specification from `specs/238-workflow-docker-access/spec.md`

## Summary

Implement MM-476 by adding a workflow-level Docker access setting, enforcing it before Docker-backed workflow workload execution, and exposing a curated `moonmind.integration_ci` DooD tool that maps to `./tools/test_integration.sh` through the existing Docker workload result contract. Existing generic DooD tools, runner-profile validation, Docker workload launcher artifact publication, and agent-runtime workload routing are reused; this story adds the policy gate, curated integration-CI tool/profile mapping, and tests that prove disabled requests do not invoke registry validation or Docker launch.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/config/settings.py` defines `workflow_docker_enabled`; `tests/unit/config/test_settings.py` covers the field | no remaining work | unit |
| FR-002 | implemented_verified | `WorkflowSettings.workflow_docker_enabled` defaults to `True`; unit test covers no-env default | no remaining work | unit |
| FR-003 | implemented_verified | existing DooD routing preserved; focused tests and full unit suite pass | no remaining work | unit + integration boundary |
| FR-004 | implemented_verified | `moonmind/workloads/tool_bridge.py` and `moonmind/workflows/temporal/activity_runtime.py` deny before validation/launcher calls | no remaining work | unit |
| FR-005 | implemented_verified | denial returns `docker_workflows_disabled`; unit tests assert error details | no remaining work | unit |
| FR-006 | implemented_verified | generic tool handlers, direct `workload.run`, and Pentest Docker-backed boundary are gated | no remaining work | unit + integration boundary |
| FR-007 | implemented_verified | managed-session launch code was not changed; existing managed-session tests pass in full unit suite | no remaining work | unit |
| FR-008 | implemented_verified | `moonmind.integration_ci` tool/profile and request mapping are implemented | no remaining work | unit + integration boundary |
| FR-009 | implemented_verified | integration-CI tests assert existing workload refs and metadata result shape; failure context is carried through `diagnosticsRef` and `outputRefs` when emitted by the runner | no remaining work | unit + integration boundary |
| FR-010 | implemented_verified | `./tools/test_integration.sh` remains a standalone script | do not modify script | final verify |
| FR-011 | implemented_verified | MM-476 is present in orchestration input, spec, plan, tasks, and final verification evidence | no remaining work | final verify |
| SC-001 | implemented_verified | settings tests cover default and env override | no remaining work | unit |
| SC-002 | implemented_verified | denial tests use failing registry/launcher fakes to prove no downstream calls occur | no remaining work | unit |
| SC-003 | implemented_verified | existing workflow routing tests plus integration-CI dispatcher contract pass | no remaining work | integration boundary |
| SC-004 | implemented_verified | integration-CI unit and integration-boundary tests assert script mapping and result refs | no remaining work | unit + integration boundary |
| SC-005 | implemented_verified | managed-session code unchanged and full managed-session unit coverage passes | no remaining work | unit |
| SC-006 | implemented_verified | MM-476 traceability check passed across source input and active spec artifacts | no remaining work | final verify |

## Technical Context

**Language/Version**: Python 3.12 
**Primary Dependencies**: Pydantic v2 settings, Temporal Python SDK activity boundaries, existing Docker workload contracts 
**Storage**: No new persistent storage 
**Unit Testing**: pytest through `./tools/test_unit.sh` 
**Integration Testing**: pytest workflow/activity-boundary tests through `./tools/test_unit.sh`; full hermetic integration runner is `./tools/test_integration.sh` when Docker is available 
**Target Platform**: MoonMind API/worker containers and Docker-capable agent-runtime fleet 
**Project Type**: Python service/runtime contracts 
**Performance Goals**: Policy gate is in-memory and runs before workload validation or launch 
**Constraints**: Fail fast when disabled; no raw Docker socket for normal agents/sessions; preserve existing DooD runner-profile policy; no new storage; keep `./tools/test_integration.sh` behavior unchanged 
**Scale/Scope**: One workflow setting, generic DooD/tool activity gate, and one curated integration-CI tool/profile

## Constitution Check

- I Orchestrate, Don't Recreate: PASS - preserves direct tool/workload orchestration through existing Docker workload adapters.
- II One-Click Agent Deployment: PASS - default is enabled and no new required external service is introduced.
- III Avoid Vendor Lock-In: PASS - setting gates MoonMind's generic Docker workload boundary.
- IV Own Your Data: PASS - artifact-backed workload results remain under MoonMind-managed artifact paths.
- V Skills Are First-Class: PASS - exposes integration CI as a curated executable tool.
- VI Replaceable Scaffolding: PASS - implementation is a small policy gate around existing contracts.
- VII Runtime Configurability: PASS - behavior is controlled by `MOONMIND_WORKFLOW_DOCKER_ENABLED`.
- VIII Modular Architecture: PASS - changes stay in settings, workload bridge, worker runtime, and activity boundary.
- IX Resilient by Default: PASS - disabled state fails before side effects and returns deterministic policy denial.
- X Continuous Improvement: PASS - tests and verification provide evidence.
- XI Spec-Driven Development: PASS - spec, plan, tasks, code, and verification preserve MM-476.
- XII Canonical Docs vs Tmp: PASS - orchestration input stays under `local-only handoffs`; no canonical docs migration narrative is added.
- XIII Pre-release Compatibility: PASS - no compatibility alias or fallback semantics are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/238-workflow-docker-access/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── workflow-docker-access-tool-contract.md
├── checklists/
│ └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── config/
│ └── settings.py
├── workflows/
│ └── temporal/
│ ├── activity_runtime.py
│ └── worker_runtime.py
└── workloads/
 └── tool_bridge.py

config/
└── workloads/
 └── default-runner-profiles.yaml

tests/
├── integration/
│ └── temporal/
│ └── test_integration_ci_tool_contract.py
└── unit/
 ├── config/
 │ └── test_settings.py
 ├── workloads/
 │ └── test_workload_tool_bridge.py
 └── workflows/
 └── temporal/
 └── test_workload_run_activity.py
```

**Structure Decision**: Extend the existing Docker workload plane instead of adding a new execution substrate. `moonmind.integration_ci` is a curated tool wrapper in `moonmind.workloads.tool_bridge`, while direct `workload.run` activity requests are gated in `TemporalAgentRuntimeActivities`.

## Complexity Tracking

No constitution violations.
