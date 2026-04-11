# Implementation Plan: Jira Create Page Integration

**Branch**: `154-jira-create-page` | **Date**: 2026-04-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/154-jira-create-page/spec.md`

## Summary

Implement the Jira Create-page integration as an additive runtime feature: a rollout-gated dashboard boot contract, MoonMind-owned browser endpoints backed by the existing trusted Jira auth/client/policy boundary, and one shared Create-page browser/import surface that writes normalized Jira text into existing preset or step instruction fields. The submission contract remains unchanged; Jira provenance stays local UI metadata for the initial delivery; validation covers runtime config, browser service/router behavior, policy and redaction safety, frontend navigation/import flows, preset reapply semantics, accessibility, and failure isolation.

## Technical Context

**Language/Version**: Python 3.12 backend, TypeScript/React 19 frontend
**Primary Dependencies**: FastAPI routers, Pydantic v2 models, existing `moonmind.integrations.jira` auth/client/tool boundary, TanStack Query, Vitest, pytest
**Storage**: No database or durable task-payload schema changes; optional browser session storage for last selected Jira project/board; existing task submission/artifact fallback unchanged
**Testing**: `pytest`, `./tools/test_unit.sh`, `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`, focused router/service tests
**Target Platform**: Docker/Compose-hosted MoonMind API service and Mission Control Create page
**Project Type**: Backend API/service plus frontend Create-page runtime feature
**Performance Goals**: Keep runtime config generation synchronous and bounded; load Jira board/issue data only on browser demand; keep Create page manual editing responsive while Jira requests are pending or failed
**Constraints**: Runtime mode; production runtime code and validation tests required; browser clients must never call Jira directly or receive raw credentials; trusted Jira tooling enablement remains separate from Create-page UI rollout; submission payload shape remains unchanged
**Scale/Scope**: One Jira connection/profile path for MVP using existing trusted settings, project allowlists, and policy; browse project/board/column/issue detail; import into preset or selected step; no Jira mutation, no multi-issue merge, no persisted provenance, no task type changes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Coverage |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Jira remains an instruction source for MoonMind-authored tasks; no new agent or Jira-native task model is introduced. |
| II. One-Click Agent Deployment | PASS | The integration is disabled by default and uses existing optional Atlassian/Jira settings; no mandatory external service is added. |
| III. Avoid Vendor Lock-In | PASS | Jira-specific behavior is isolated behind browser service/API boundaries; Create page task composition remains MoonMind-native. |
| IV. Own Your Data | PASS | Imported text becomes local task draft content; browser clients receive normalized MoonMind responses and no credentials. |
| V. Skills Are First-Class | PASS | Step skill selection and task preset behavior remain unchanged; Jira import does not alter skill semantics. |
| VI. Thin Scaffolding, Thick Contracts | PASS | The plan adds strict browser read models and tests around a trusted integration boundary. |
| VII. Powerful Runtime Configurability | PASS | Create-page exposure, defaults, and session-memory behavior are runtime-configured and safe by default. |
| VIII. Modular and Extensible Architecture | PASS | Backend browser logic is isolated beside existing Jira integration primitives and frontend logic stays in the Create page boundary. |
| IX. Resilient by Default | PASS | Jira failures stay local to the browser, retry/timeout/redaction reuse trusted Jira client behavior, and manual creation remains available. |
| X. Facilitate Continuous Improvement | PASS | Focused tests and local provenance improve operator clarity without changing run submission semantics. |
| XI. Spec-Driven Development Is the Source of Truth | PASS | `spec.md`, this plan, design artifacts, contracts, and later tasks define the runtime change. |
| XII. Canonical Documentation Separates Desired State from Migration Backlog | PASS | `docs/UI/CreatePage.md` remains the desired-state contract; implementation details live under `specs/154-jira-create-page/`. |
| XIII. Pre-Release Velocity: Delete, Don't Deprecate | PASS | No compatibility aliases or duplicate Jira task model are introduced; superseded internal shapes should be replaced directly during implementation. |

**Post-Design Recheck**: PASS. Research and design artifacts keep the same constraints: rollout-gated runtime config, trusted MoonMind-owned Jira browser operations, no browser credentials, no Jira task type, no submission-contract change, and test-first runtime implementation.

## Project Structure

### Documentation (this feature)

```text
specs/154-jira-create-page/
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
        ├── browser.py                  # ADD: browser-facing read service and normalization
        ├── auth.py                     # REUSE: SecretRef-aware auth resolution
        ├── client.py                   # REUSE: bounded retry, timeout, and redaction path
        ├── errors.py                   # REUSE/EXTEND: structured safe Jira errors if needed
        └── tool.py                     # REUSE: policy helpers or extract shared policy helper if needed

api_service/
└── api/
    └── routers/
        ├── jira_browser.py             # ADD: MoonMind-owned browser endpoints
        └── task_dashboard_view_model.py # MODIFY: runtime config contract already started; keep tests aligned

frontend/
└── src/
    └── entrypoints/
        ├── task-create.tsx             # MODIFY: shared Jira browser, state, imports, provenance
        └── task-create.test.tsx        # MODIFY: Create-page Jira behavior coverage

tests/
└── unit/
    ├── api/
    │   └── routers/
    │       ├── test_jira_browser.py
    │       └── test_task_dashboard_view_model.py
    ├── config/
    │   └── test_settings.py
    └── integrations/
        ├── test_jira_browser_service.py
        └── test_jira_tool_service.py   # MODIFY only if policy helper extraction changes behavior
```

**Structure Decision**: Add `moonmind/integrations/jira/browser.py` for presentation-oriented read models beside the existing strict tool service, and expose it through `api_service/api/routers/jira_browser.py`. Keep frontend implementation in `task-create.tsx` initially because the Create page already owns preset, step, objective, dependency, runtime, and submission state; refactor into local components only if the implementation becomes too large during tasks.

## Phase 0: Research

See [research.md](./research.md).

## Phase 1: Design & Contracts

See [data-model.md](./data-model.md), [contracts/jira-browser-openapi.yaml](./contracts/jira-browser-openapi.yaml), [contracts/requirements-traceability.md](./contracts/requirements-traceability.md), and [quickstart.md](./quickstart.md).

## Implementation Plan

1. Extend failing runtime-config tests as needed and keep Jira Create-page exposure gated by `system.jiraIntegration.enabled`, not trusted Jira tool enablement.
2. Add browser service models and normalization tests for project, board, column, issue summary, issue detail, import text, empty states, status mapping, and policy denial.
3. Add `jira_browser` router tests for connection verification, projects, project boards, board columns, board issues, issue detail, structured error mapping, authentication dependency behavior, and redaction-safe failures.
4. Implement browser service/router using existing Jira auth resolution, `JiraClient`, retry/timeout/redaction behavior, and project allowlist policy.
5. Add Create-page frontend tests for hidden controls, browser open targets, project/board/column/issue navigation, no draft mutation on issue selection, replace/append imports, template-bound step detachment, preset reapply signal, provenance chips, session memory, accessibility affordances, and local Jira failure handling.
6. Implement the shared Jira browser state and UI in `task-create.tsx`, writing imports through existing `setTemplateFeatureRequest` and `updateStep()` paths so objective precedence and template detachment stay intact.
7. Run focused backend and frontend suites, then the standard unit script; document any unrelated pre-existing failures separately.

## Verification Plan

### Automated Tests

1. `pytest tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/config/test_settings.py -q`
2. `pytest tests/unit/integrations/test_jira_browser_service.py tests/unit/api/routers/test_jira_browser.py -q`
3. `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
4. `./tools/test_unit.sh`
5. `SPECIFY_FEATURE=154-jira-create-page ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
6. `SPECIFY_FEATURE=154-jira-create-page ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`

### Manual Validation

1. Start with Jira UI disabled and confirm `/tasks/new` has no Jira entry points while manual task creation still works.
2. Enable Jira UI with a test Jira connection and confirm project, board, column, issue, and issue detail browsing occurs only through MoonMind endpoints.
3. Import an issue into preset instructions with replace and append; confirm objective resolution and preset reapply messaging.
4. Import an issue into a primary and secondary step; confirm only the selected step changes and template-bound step identity detaches when expected.
5. Simulate Jira request failures and confirm the browser shows local errors while the Create button and manual editing remain available.

## Complexity Tracking

No constitution violations require complexity waivers.
