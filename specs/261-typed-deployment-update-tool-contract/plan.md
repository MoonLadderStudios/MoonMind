# Implementation Plan: Typed Deployment Update Tool Contract

**Branch**: `261-typed-deployment-update-tool-contract` | **Date**: 2026-04-25 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `specs/261-typed-deployment-update-tool-contract/spec.md`

## Summary

Implement MM-519 by adding a canonical deployment update executable tool definition payload and using its shared name/version from the policy-gated deployment API queued-run plan node. Validate the contract with unit tests for registry parsing and integration-style plan validation against a pinned registry snapshot. Existing MM-518 API behavior remains the submission and policy-validation slice; this story adds the tool-contract boundary needed by the plan/tool system.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/skills/deployment_tools.py`; `tests/unit/workflows/skills/test_deployment_tool_contracts.py` | complete | unit passed |
| FR-002 | implemented_verified | strict input schema in `deployment_tools.py`; plan validation tests reject unknown fields | complete | unit + integration-style passed |
| FR-003 | implemented_verified | output schema assertions in `test_deployment_tool_contracts.py` | complete | unit passed |
| FR-004 | implemented_verified | capabilities/admin assertions in `test_deployment_tool_contracts.py` | complete | unit passed |
| FR-005 | implemented_verified | executor assertions in `test_deployment_tool_contracts.py` | complete | unit passed |
| FR-006 | implemented_verified | retry policy assertions in `test_deployment_tool_contracts.py` | complete | unit passed |
| FR-007 | implemented_verified | valid pinned registry plan validation test in `test_deployment_tool_contracts.py` | complete | integration-style passed |
| FR-008 | implemented_verified | invalid plan validation parameterized tests reject shell/path/runner override fields | complete | integration-style passed |
| FR-009 | implemented_verified | `deployment_operations.py` imports shared constants; API unit test asserts name/version | complete | unit passed |
| FR-010 | implemented_verified | MM-519 is preserved in spec/tasks/verification and traceability check passed | complete | final verify passed |
| DESIGN-REQ-001..009 | implemented_verified | contract helper, API binding, targeted tests, and traceability check cover the source mappings | complete | unit + integration-style passed |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2 contracts, existing MoonMind skill/tool plan contract helpers, pytest  
**Storage**: No new persistent storage  
**Unit Testing**: pytest through `./tools/test_unit.sh` and targeted pytest  
**Integration Testing**: pytest integration-style plan validation; no compose-backed services required for this contract slice  
**Target Platform**: Linux server / MoonMind managed runtime  
**Project Type**: Python backend/orchestration contract  
**Performance Goals**: Registry and plan validation remain deterministic in-process checks  
**Constraints**: No raw host shell exposure, no arbitrary runner image/path inputs, no hidden image reference transformation, no new compatibility aliases  
**Scale/Scope**: One deployment update tool contract and one queued-run plan-node binding

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. The feature exposes an executable tool contract for orchestration instead of rebuilding Docker behavior in agent prompts.
- II One-Click Agent Deployment: PASS. The contract supports operator-owned Docker Compose deployment without adding external dependencies.
- III Avoid Vendor Lock-In: PASS. The tool contract stays behind MoonMind's generic plan/tool surface.
- IV Own Your Data: PASS. The output schema uses artifact refs and structured results under operator control.
- V Skills Are First-Class: PASS. The executable tool is registered through the existing tool definition contract.
- VI Replaceable Scaffolding: PASS. The contract is thin and test-anchored.
- VII Runtime Configurability: PASS. Stack/repository policy remains configuration-driven in the existing deployment API slice.
- VIII Modular Architecture: PASS. New behavior is isolated to deployment tool contract helpers and tests.
- IX Resilient by Default: PASS. Retry policy is explicit and non-retryable privileged failures fail closed.
- X Continuous Improvement: PASS. Verification artifacts record outcome and traceability.
- XI Spec-Driven Development: PASS. This plan follows the MM-519 spec before code changes.
- XII Documentation Separation: PASS. Migration/execution notes stay under `specs/261-*`, not canonical docs.
- XIII Delete, Don't Deprecate: PASS. No compatibility alias is introduced.

## Project Structure

### Documentation (this feature)

```text
specs/261-typed-deployment-update-tool-contract/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── deployment-update-tool-contract.md
├── tasks.md
└── verification.md
```

### Source Code (repository root)

```text
moonmind/workflows/skills/
├── deployment_tools.py
├── tool_plan_contracts.py
└── tool_registry.py

api_service/services/
└── deployment_operations.py

tests/unit/workflows/skills/
└── test_deployment_tool_contracts.py

tests/unit/api/routers/
└── test_deployment_operations.py
```

**Structure Decision**: Add a small shared contract helper under `moonmind/workflows/skills` because the contract belongs to the executable plan/tool system and can be consumed by API queuing code without creating a new storage path.

## Complexity Tracking

No constitution violations.
