# Implementation Plan: Policy-Gated Deployment Update API

**Branch**: `260-policy-gated-deployment-update` | **Date**: 2026-04-25 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/260-policy-gated-deployment-update/spec.md`

## Summary

Implement `MM-518` by adding a typed FastAPI deployment operations surface under `/api/v1/operations/deployment`, backed by a policy service that validates administrator authorization, allowlisted stack/repository/reference/mode/options, and required reason before returning queued deployment run metadata. Read-only endpoints expose typed current stack state and image target policy without caller-controlled host paths, Compose files, runner images, or shell command text. Test strategy is API-router coverage plus service validation through the router, with final full unit-suite verification.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/api/routers/deployment_operations.py`, `tests/unit/api/routers/test_deployment_operations.py` | no new implementation | final verify |
| FR-002 | implemented_verified | update response model and submit test in `tests/unit/api/routers/test_deployment_operations.py` | no new implementation | final verify |
| FR-003 | implemented_verified | `_require_admin`, non-admin rejection test | no new implementation | final verify |
| FR-004 | implemented_verified | dedicated deployment route and admin check independent of task submission routes | no new implementation | final verify |
| FR-005 | implemented_verified | `DeploymentOperationsService.validate_update_request`, invalid input tests | no new implementation | final verify |
| FR-006 | implemented_verified | explicit policy checks for stack, repository, reference, mode, reason plus schema extra-field rejection | no new implementation | final verify |
| FR-007 | implemented_verified | `DeploymentOperationError` to HTTP error-code mapping and policy error tests | no new implementation | final verify |
| FR-008 | implemented_verified | `/stacks/{stack}` endpoint and typed state response test | no new implementation | final verify |
| FR-009 | implemented_verified | `/image-targets` endpoint and typed target response test | no new implementation | final verify |
| FR-010 | implemented_verified | `digestPinningRecommended` response assertion | no new implementation | final verify |
| FR-011 | implemented_verified | Pydantic `extra="forbid"` request models and arbitrary command/path rejection test | no new implementation | final verify |
| FR-012 | implemented_verified | `MM-518` preserved in MoonSpec artifacts and final report plan | no new implementation | final verify |
| DESIGN-REQ-003 | implemented_verified | update endpoint contract and queued response tests | no new implementation | final verify |
| DESIGN-REQ-004 | implemented_verified | state and image target endpoints and tests | no new implementation | final verify |
| DESIGN-REQ-005 | implemented_verified | pre-execution validation service and tests | no new implementation | final verify |
| DESIGN-REQ-008 | implemented_verified | admin-only submit and allowlisted stack policy | no new implementation | final verify |
| DESIGN-REQ-018 | implemented_verified | typed request model rejects arbitrary command/path fields | no new implementation | final verify |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: FastAPI, Pydantic v2, existing `get_current_user()` auth dependency  
**Storage**: No new persistent storage; policy-backed deterministic responses for this story  
**Unit Testing**: pytest through `./tools/test_unit.sh`  
**Integration Testing**: FastAPI `TestClient` router tests in unit tier; no compose-backed dependency required for this policy/API story  
**Target Platform**: MoonMind API service on Linux containers  
**Project Type**: Backend web service  
**Performance Goals**: Policy validation remains in-process and bounded; no external calls before rejection  
**Constraints**: Admin-only mutation, no arbitrary shell/host path inputs, no hidden fallback for unsupported values, preserve `MM-518` traceability  
**Scale/Scope**: One allowlisted deployment stack policy surface and three API operations

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS - the story exposes bounded operation orchestration inputs and does not implement a competing agent/runtime.
- II. One-Click Agent Deployment: PASS - no required external cloud dependency or new persistent service.
- VII. Powerful Runtime Configurability: PASS for the story slice - policy is isolated in a service boundary; future runtime config can replace the default policy without changing the API contract.
- IX. Resilient by Default: PASS - invalid inputs fail before workflow/tool side effects.
- XI. Spec-Driven Development: PASS - `spec.md` preserves the canonical `MM-518` Jira brief and source coverage IDs.
- XIII. Pre-release Compatibility: PASS - no compatibility aliases or hidden fallback semantics introduced.

## Project Structure

### Documentation (this feature)

```text
specs/260-policy-gated-deployment-update/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── deployment-operations.openapi.yaml
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/deployment_operations.py
├── main.py
└── services/deployment_operations.py

tests/
└── unit/api/routers/test_deployment_operations.py
```

**Structure Decision**: Use the existing API router plus service pattern. Keep request/response models near the router because they define a small public HTTP contract, and keep policy validation in `api_service/services/deployment_operations.py` for focused unit/router testing and future configuration replacement.

## Complexity Tracking

No constitution violations require justification.
