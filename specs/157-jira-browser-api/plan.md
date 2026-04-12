# Implementation Plan: Jira Browser API

**Branch**: `157-jira-browser-api` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/157-jira-browser-api/spec.md`

## Summary

Implement the server-side Jira browser API for the Create page as a runtime feature. The API will expose MoonMind-owned read operations for verifying the configured Jira connection, browsing allowed projects and project boards, resolving board columns, grouping board issues by normalized columns, and returning normalized issue detail with recommended import text. The implementation will reuse the existing trusted Jira authentication, low-level client, retry, timeout, redaction, and project-policy boundary so browser clients never receive Jira credentials or parse Jira rich-text data directly.

## Technical Context

**Language/Version**: Python 3.12 backend
**Primary Dependencies**: FastAPI routers, Pydantic v2 response models, existing Jira auth/client primitives, HTTP client behavior already used by the Jira integration
**Storage**: N/A; no database or durable workflow payload changes
**Testing**: pytest unit tests plus `./tools/test_unit.sh` for final verification
**Target Platform**: Docker/Compose-hosted MoonMind API service and Mission Control Create page runtime
**Project Type**: Backend API/service feature inside the existing MoonMind monorepo
**Performance Goals**: Keep browser operations demand-loaded and bounded; avoid synchronous Jira calls during normal Create-page boot when Jira browsing is not opened
**Constraints**: Runtime mode; production code changes and validation tests are required; browser clients must never receive raw Jira credentials; Jira failures must stay local and non-blocking for manual task creation
**Scale/Scope**: One configured trusted Jira connection path for this phase; project, board, column, issue list, and issue detail reads only; no Jira mutation, no persisted Jira provenance, no Create-page import UI changes in this phase

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Coverage |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Jira remains an external instruction source; no new Jira-native task model or agent behavior is introduced. |
| II. One-Click Agent Deployment | PASS | The feature is optional and uses existing Jira configuration; no new mandatory external service is added for default startup. |
| III. Avoid Vendor Lock-In | PASS | Jira-specific behavior is isolated behind a browser service/API boundary and does not change MoonMind task submission semantics. |
| IV. Own Your Data | PASS | Browser clients receive normalized MoonMind responses, and imported data remains operator-controlled task draft content in later phases. |
| V. Skills Are First-Class | PASS | Step skills and executable skill contracts are unaffected. |
| VI. Thin Scaffolding, Thick Contracts | PASS | The work adds explicit read models and tests around the trusted integration boundary. |
| VII. Powerful Runtime Configurability | PASS | Create-page exposure remains runtime-configured and separate from trusted Jira tooling. |
| VIII. Modular and Extensible Architecture | PASS | The browser read model is isolated beside existing Jira integration primitives and exposed through a dedicated router. |
| IX. Resilient by Default | PASS | Existing retry/timeout behavior is reused, errors are structured, and manual task creation is not blocked by Jira failures. |
| X. Facilitate Continuous Improvement | PASS | Focused tests provide a repeatable validation path for the new browser contract. |
| XI. Spec-Driven Development Is the Source of Truth | PASS | This plan is derived from `spec.md` and preserves source-document traceability. |
| XII. Canonical Documentation Separates Desired State from Migration Backlog | PASS | Canonical docs remain desired-state references; this implementation plan lives under `specs/`. |
| XIII. Pre-Release Velocity: Delete, Don't Deprecate | PASS | No compatibility aliases or duplicate legacy browser contract are introduced. |

**Post-Design Recheck**: PASS. Design artifacts preserve the same runtime scope, trusted Jira boundary, no credential exposure, no Jira mutation, no task submission changes, and validation-test requirement.

## Project Structure

### Documentation (this feature)

```text
specs/157-jira-browser-api/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── jira-browser-openapi.yaml
│   └── requirements-traceability.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
└── integrations/
    └── jira/
        ├── browser.py
        ├── auth.py
        ├── client.py
        ├── errors.py
        └── tool.py

api_service/
└── api/
    └── routers/
        ├── jira_browser.py
        └── task_dashboard_view_model.py

tests/
└── unit/
    ├── api/
    │   └── routers/
    │       ├── test_jira_browser.py
    │       └── test_task_dashboard_view_model.py
    └── integrations/
        ├── test_jira_browser_service.py
        ├── test_jira_auth.py
        ├── test_jira_client.py
        └── test_jira_tool_service.py
```

**Structure Decision**: Add a browser-facing Jira read service beside existing Jira integration primitives and expose it through a dedicated FastAPI router. Keep runtime-config publication centralized in the existing dashboard view-model surface. Validate service behavior separately from router behavior so normalization, policy, and safe error mapping are covered at the correct boundaries.

## Phase 0: Research

See [research.md](./research.md).

## Phase 1: Design & Contracts

See [data-model.md](./data-model.md), [contracts/jira-browser-openapi.yaml](./contracts/jira-browser-openapi.yaml), [contracts/requirements-traceability.md](./contracts/requirements-traceability.md), and [quickstart.md](./quickstart.md).

## Implementation Plan

1. Add browser read models and service methods for connection verification, project listing, board listing, column normalization, issue grouping, and issue detail normalization.
2. Reuse the trusted Jira auth and client path for all Jira requests, including timeout, retry, and redaction behavior.
3. Enforce project allowlists before returning project-scoped board or issue data.
4. Add FastAPI router endpoints under the MoonMind API surface and map Jira failures to structured safe responses.
5. Register the router without changing existing Create-page submission or manual authoring behavior.
6. Add unit tests for service normalization, policy denial, disabled rollout, router response shapes, safe error mapping, and existing Jira integration regression coverage.
7. Run focused backend tests and the standard unit wrapper.

## Verification Plan

1. `pytest tests/unit/integrations/test_jira_browser_service.py tests/unit/api/routers/test_jira_browser.py -q`
2. `pytest tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py -q`
3. `pytest tests/unit/integrations/test_jira_auth.py tests/unit/integrations/test_jira_client.py tests/unit/integrations/test_jira_tool_service.py tests/unit/api/test_mcp_tools_router.py -q`
4. `./tools/test_unit.sh`
5. `SPECIFY_FEATURE=157-jira-browser-api ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

## Complexity Tracking

No constitution violations require complexity waivers.
