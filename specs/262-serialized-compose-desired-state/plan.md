# Implementation Plan: Serialized Compose Desired-State Execution

**Branch**: `262-serialized-compose-desired-state` | **Date**: 2026-04-26 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/262-serialized-compose-desired-state/spec.md`

## Summary

Implement `MM-520` by adding a hermetic deployment update execution layer for the existing typed `deployment.update_compose_stack` tool. The execution layer will serialize updates per stack, persist a desired-state record before Compose recreation, construct only policy-controlled pull/up command shapes, capture structured evidence refs, and expose a registered tool handler boundary. Existing MM-518 API validation and MM-519 tool-contract work are reused; this story adds the post-validation lifecycle behavior and tests it through injectable fake stores/runners.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `DeploymentUpdateLockManager`, `test_same_stack_lock_contention_fails_before_side_effects`, integration dispatch lock test | complete | unit + integration passed |
| FR-002 | implemented_verified | `DeploymentUpdateLockManager.acquire()` raises non-retryable `DEPLOYMENT_LOCKED`; unit and integration tests | complete | unit + integration passed |
| FR-003 | implemented_verified | executor captures before state before persistence; ordering unit test | complete | unit passed |
| FR-004 | implemented_verified | executor persists desired state before `runner.up`; ordering unit test and integration dispatch | complete | unit + integration passed |
| FR-005 | implemented_verified | desired-state payload includes stack, repository, requested ref, digest, reason, timestamp, source run ID | complete | unit passed |
| FR-006 | implemented_verified | `build_compose_command_plan()` omits `--force-recreate` for `changed_services` | complete | unit passed |
| FR-007 | implemented_verified | `build_compose_command_plan()` adds `--force-recreate` only for `force_recreate` | complete | unit passed |
| FR-008 | implemented_verified | command builder only adds/removes `--remove-orphans` and `--wait` from booleans | complete | unit passed |
| FR-009 | implemented_verified | executor writes before, command, verification, and after evidence refs; integration dispatch validates output shape | complete | unit + integration passed |
| FR-010 | implemented_verified | verification failure test returns failed tool result and `outputs.status = FAILED` | complete | unit passed |
| FR-011 | implemented_verified | forbidden input and unsupported runner-mode tests; worker dispatcher registration uses fail-closed disabled runner by default | complete | unit passed |
| FR-012 | implemented_verified | `MM-520` preserved in spec, plan, tasks, contract, quickstart, verification, code/test traceability | complete | traceability grep passed |
| SCN-001 | implemented_verified | lock contention unit and integration tests | complete | unit + integration passed |
| SCN-002 | implemented_verified | lifecycle ordering unit test | complete | unit passed |
| SCN-003 | implemented_verified | changed-services command unit test | complete | unit passed |
| SCN-004 | implemented_verified | force-recreate command unit test | complete | unit passed |
| SCN-005 | implemented_verified | flag omission/addition unit test | complete | unit passed |
| SCN-006 | implemented_verified | verification failure unit test | complete | unit passed |
| SCN-007 | implemented_verified | forbidden input and closed runner-mode tests | complete | unit passed |
| DESIGN-REQ-001 | implemented_verified | desired-state payload + ordering tests | complete | unit + integration passed |
| DESIGN-REQ-002 | implemented_verified | per-stack lock manager + lock tests | complete | unit + integration passed |
| DESIGN-REQ-003 | implemented_verified | before/persist ordering test and no caller file inputs | complete | unit passed |
| DESIGN-REQ-004 | implemented_verified | pull/up command builder tests | complete | unit passed |
| DESIGN-REQ-005 | implemented_verified | evidence refs and verification failure result tests | complete | unit + integration passed |
| DESIGN-REQ-006 | implemented_verified | closed runner mode and worker handler registration | complete | unit passed |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, existing MoonMind tool dispatcher and `ToolResult` / `ToolFailure` contracts  
**Storage**: Existing artifact-backed/file-backed boundaries only; no new database tables  
**Unit Testing**: pytest through `./tools/test_unit.sh` or focused `pytest` during iteration  
**Integration Testing**: pytest `integration_ci` marker through `./tools/test_integration.sh` when Docker is available; focused hermetic pytest for iteration  
**Target Platform**: Linux server / MoonMind deployment-control runtime  
**Project Type**: Backend service and workflow tool execution support  
**Performance Goals**: Lock acquisition and command construction are constant-time; lifecycle ordering does not add polling beyond runner implementation  
**Constraints**: No raw shell inputs, no caller-selected Compose paths, no caller-selected runner images, no arbitrary file mutation, no new persistent database table  
**Scale/Scope**: One allowlisted MoonMind stack and one typed deployment tool lifecycle slice

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Execution stays behind the typed tool boundary and does not reimplement agent behavior.
- II. One-Click Agent Deployment: PASS. Tests are hermetic; privileged Docker mutation is injectable and not required for local unit tests.
- III. Avoid Vendor Lock-In: PASS. Runner and store boundaries are protocols rather than Docker CLI-specific business logic.
- IV. Own Your Data: PASS. Desired-state and evidence records stay in operator-controlled stores/artifacts.
- V. Skills Are First-Class and Easy to Add: PASS. The work registers a typed executable tool handler.
- VI. Replaceable Scaffolding: PASS. Runner implementation is isolated behind a small protocol.
- VII. Runtime Configurability: PASS. Runner mode is explicit and closed; unsupported values fail fast.
- VIII. Modular and Extensible Architecture: PASS. New behavior is isolated in a deployment execution module.
- IX. Resilient by Default: PASS. Locking, desired-state persistence before mutation, and verification failure handling are core requirements.
- X. Facilitate Continuous Improvement: PASS. Structured results and artifacts support later reporting.
- XI. Spec-Driven Development: PASS. This plan follows `spec.md` and preserves `MM-520`.
- XII. Canonical Documentation: PASS. Implementation notes live in this feature directory; canonical docs are not rewritten as a migration diary.

## Project Structure

### Documentation (this feature)

```text
specs/262-serialized-compose-desired-state/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── deployment-update-execution.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/skills/
├── deployment_execution.py
├── deployment_tools.py
└── tool_dispatcher.py

moonmind/workflows/temporal/
└── worker_runtime.py

tests/unit/workflows/skills/
├── test_deployment_tool_contracts.py
└── test_deployment_update_execution.py

tests/integration/temporal/
└── test_deployment_update_execution_contract.py
```

**Structure Decision**: Add the execution lifecycle to `moonmind/workflows/skills/` because `deployment.update_compose_stack` is an executable tool contract and the behavior must be testable below Temporal while still available to Temporal skill dispatch.

## Complexity Tracking

No constitution violations.
