# Implementation Plan: Jira UI Runtime Config

**Branch**: `156-jira-ui-runtime-config` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/156-jira-ui-runtime-config/spec.md`

## Summary

Add a Phase 1 Create-page Jira rollout contract to the dashboard runtime configuration. The implementation keeps Jira browser discovery disabled by default, gates it behind a Create-page-specific feature flag, exposes only MoonMind-owned endpoint templates and browser-safe defaults when enabled, and validates the disabled/enabled/default-value behavior through focused unit tests.

## Technical Context

**Language/Version**: Python 3.11 application code; TypeScript consumers are downstream only for this phase  
**Primary Dependencies**: Pydantic settings, FastAPI-hosted dashboard boot payload, pytest  
**Storage**: N/A; runtime configuration is generated from process settings and request context  
**Testing**: `./tools/test_unit.sh` with focused pytest coverage for runtime config; full unit runner remains the final verification path  
**Target Platform**: MoonMind API service and Mission Control Create page runtime configuration  
**Project Type**: Web application with backend-owned boot/runtime config and frontend consumers  
**Performance Goals**: Runtime config generation remains synchronous and constant-time relative to existing dashboard configuration size  
**Constraints**: Jira UI exposure must be disabled by default, independent from backend Jira tool enablement, and browser-safe with no raw Jira credentials or Jira-domain URLs  
**Scale/Scope**: One runtime config contract block, feature-flag settings, config defaults, and unit tests; no Jira browser API router or frontend browser UI in this phase

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Assessment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Jira remains an external instruction source and does not become a MoonMind task runtime. |
| II. One-Click Agent Deployment | PASS | Feature is disabled by default and requires no Jira credentials for baseline startup. |
| III. Avoid Vendor Lock-In | PASS | Jira-specific browser discovery is isolated behind a clearly named optional integration block. |
| IV. Own Your Data | PASS | Browser discovery points to MoonMind-owned endpoints only, preserving the trusted server boundary for future data access. |
| V. Skills Are First-Class and Easy to Add | PASS | No skill runtime behavior is changed. |
| VI. Replaceable Scaffolding | PASS | The contract is thin and explicit, allowing later browser/API phases to evolve without hidden coupling. |
| VII. Runtime Configurability | PASS | Rollout and defaults are operator-controlled via namespaced runtime settings. |
| VIII. Modular and Extensible Architecture | PASS | Changes stay within settings, dashboard runtime config, and tests. |
| IX. Resilient by Default | PASS | Disabled behavior is a safe no-op and manual Create page behavior remains available. |
| X. Facilitate Continuous Improvement | PASS | Validation tests make rollout regressions visible. |
| XI. Spec-Driven Development | PASS | Spec, plan, contracts, traceability, and tests define the implementation path. |
| XII. Canonical Documentation Separation | PASS | This plan is under `specs/`; canonical docs are referenced, not rewritten as migration logs. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases or deprecated parallel config names are introduced. |

## Project Structure

### Documentation (this feature)

```text
specs/156-jira-ui-runtime-config/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── runtime-config-jira.schema.json
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/task_dashboard_view_model.py
└── config.template.toml

moonmind/
└── config/settings.py

tests/
├── config/test_atlassian_settings.py
└── unit/api/routers/test_task_dashboard_view_model.py
```

**Structure Decision**: Use the existing backend-owned runtime config path. Phase 1 does not add frontend components or Jira browser endpoints; later phases can consume this contract from the existing Create page entrypoint.

## Phase 0: Research

See [research.md](./research.md).

## Phase 1: Design And Contracts

See:

- [data-model.md](./data-model.md)
- [runtime-config-jira.schema.json](./contracts/runtime-config-jira.schema.json)
- [requirements-traceability.md](./contracts/requirements-traceability.md)
- [quickstart.md](./quickstart.md)

## Implementation Strategy

1. Add tests first for runtime config disabled behavior, enabled endpoint contract, and configured default project/board/session-memory values.
2. Add namespaced Create-page Jira rollout settings with safe defaults and validation for operator-provided defaults.
3. Add a small Jira runtime-config builder that returns no block when disabled and the source/system contract when enabled.
4. Merge the Jira source and system blocks into the existing runtime config without changing the non-Jira shape.
5. Document safe defaults in the repo config template.
6. Run focused unit tests, then the repo unit runner as final verification.

## Post-Design Constitution Check

| Principle | Status | Assessment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Design exposes discovery only; Jira is not modeled as an execution runtime. |
| II. One-Click Agent Deployment | PASS | Disabled defaults preserve fresh-start behavior without external Jira dependencies. |
| III. Avoid Vendor Lock-In | PASS | Vendor-specific config is isolated and optional. |
| IV. Own Your Data | PASS | Browser clients receive only MoonMind API templates, not Jira credentials. |
| V. Skills Are First-Class and Easy to Add | PASS | Skill contracts and runtime materialization are untouched. |
| VI. Replaceable Scaffolding | PASS | The contract is compact and can be replaced cleanly if later browser endpoints change before public release. |
| VII. Runtime Configurability | PASS | Operator settings control enabled state and defaults. |
| VIII. Modular and Extensible Architecture | PASS | Implementation is contained in existing settings and view-model modules. |
| IX. Resilient by Default | PASS | Jira unavailable/disabled cannot block manual task creation in this phase. |
| X. Facilitate Continuous Improvement | PASS | Tests catch rollout-boundary and contract-shape regressions. |
| XI. Spec-Driven Development | PASS | Requirements traceability maps source document requirements to implementation and validation. |
| XII. Canonical Documentation Separation | PASS | No canonical doc migration checklist is added. |
| XIII. Delete, Don't Deprecate | PASS | No legacy aliases or fallback contract names are planned. |

## Complexity Tracking

No constitution violations.
