# Implementation Plan: Jira Runtime Config Tests

**Branch**: `159-jira-runtime-config-tests` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/159-jira-runtime-config-tests/spec.md`

## Summary

Implement Phase 3 of the Jira UI plan in runtime mode by making Create-page Jira capability discoverable through the existing dashboard runtime configuration boot payload. The implementation will keep Jira UI discovery disabled by default, separate it from trusted backend Jira tool enablement, publish only MoonMind-owned endpoint templates and browser-safe defaults when enabled, and validate disabled/enabled/default behavior with focused unit tests.

## Technical Context

**Language/Version**: Python 3.11 application code; TypeScript consumers are downstream and unchanged in this phase  
**Primary Dependencies**: Pydantic settings, FastAPI-hosted dashboard runtime config, pytest  
**Storage**: N/A; runtime configuration is generated from process settings and request context  
**Testing**: `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py` for focused verification; `./tools/test_unit.sh` for final unit-suite verification  
**Target Platform**: MoonMind API service boot payload consumed by Mission Control Create page  
**Project Type**: Web application with backend-owned runtime configuration and frontend consumers  
**Performance Goals**: Runtime config generation remains synchronous and constant-time relative to existing dashboard configuration size  
**Constraints**: Runtime-mode deliverables are required; Jira UI must be disabled by default, independent from backend Jira tool enablement, browser-safe, and additive to existing Create-page behavior  
**Scale/Scope**: One optional runtime config block, existing feature-flag/default settings, repository config template entries, and unit tests; no Jira browser UI, Jira browser router, database persistence, or direct Jira calls in this phase

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Assessment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Jira remains an external instruction source and does not become a MoonMind runtime or task source. |
| II. One-Click Agent Deployment | PASS | Jira UI discovery is disabled by default and baseline startup requires no Jira credentials. |
| III. Avoid Vendor Lock-In | PASS | Jira-specific discovery is isolated behind an optional integration block and MoonMind-owned endpoint templates. |
| IV. Own Your Data | PASS | Browser clients discover MoonMind APIs only; Jira credentials and direct Jira access remain outside the browser. |
| V. Skills Are First-Class and Easy to Add | PASS | Skill contracts, skill materialization, and executable tools are untouched. |
| VI. Replaceable Scaffolding | PASS | The contract is thin, explicit, and easy to replace before public release if later Jira browser phases change. |
| VII. Runtime Configurability | PASS | Operator-facing enabled/default behavior is controlled by runtime settings with safe defaults. |
| VIII. Modular and Extensible Architecture | PASS | Work stays within settings, runtime config assembly, config template defaults, and tests. |
| IX. Resilient by Default | PASS | Disabled or unavailable Jira cannot block manual Create-page task authoring. |
| X. Facilitate Continuous Improvement | PASS | Validation tests catch rollout-boundary and contract-shape regressions. |
| XI. Spec-Driven Development | PASS | Spec, plan, data model, contracts, traceability, quickstart, and tests define the change path. |
| XII. Canonical Documentation Separation | PASS | Canonical `docs/UI/CreatePage.md` is referenced as desired state; implementation planning lives under `specs/`. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases, fallback contract names, or duplicate legacy paths are introduced. |

## Project Structure

### Documentation (this feature)

```text
specs/159-jira-runtime-config-tests/
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
└── unit/api/routers/test_task_dashboard_view_model.py
```

**Structure Decision**: Use the existing backend-owned runtime config path. Phase 3 does not add frontend components or Jira browser endpoints; later phases can consume this contract from the Create page entrypoint.

## Phase 0: Research

See [research.md](./research.md).

## Phase 1: Design And Contracts

See:

- [data-model.md](./data-model.md)
- [runtime-config-jira.schema.json](./contracts/runtime-config-jira.schema.json)
- [requirements-traceability.md](./contracts/requirements-traceability.md)
- [quickstart.md](./quickstart.md)

## Implementation Strategy

1. Add or preserve tests first for disabled Jira UI omission, enabled endpoint contract exposure, configured default project/board/session-memory values, and separation from backend Jira tool enablement.
2. Ensure Create-page Jira rollout settings remain safe-by-default and browser-safe, including normalized default project and board values.
3. Ensure the runtime config builder returns no Jira source/system blocks when disabled and merges the Jira source/system blocks only when enabled.
4. Ensure all Jira browser endpoint templates are MoonMind API paths and do not expose raw Jira credentials, Jira base URLs, or browser-side auth details.
5. Keep existing non-Jira runtime config keys and Create-page behavior unchanged when Jira UI discovery is disabled.
6. Run focused router unit tests, then the repo unit runner as final verification.

## Post-Design Constitution Check

| Principle | Status | Assessment |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Design exposes discovery only; Jira is not modeled as execution. |
| II. One-Click Agent Deployment | PASS | Disabled defaults preserve fresh-start behavior without external Jira dependencies. |
| III. Avoid Vendor Lock-In | PASS | Jira-specific config is isolated and optional. |
| IV. Own Your Data | PASS | Browser clients receive only MoonMind API templates, not Jira credentials. |
| V. Skills Are First-Class and Easy to Add | PASS | Skills are unaffected. |
| VI. Replaceable Scaffolding | PASS | The config contract is compact and explicitly validated. |
| VII. Runtime Configurability | PASS | Operator settings control enabled state and defaults. |
| VIII. Modular and Extensible Architecture | PASS | Implementation is contained in existing settings and view-model modules. |
| IX. Resilient by Default | PASS | Jira disabled/unavailable cannot block manual Create-page authoring in this phase. |
| X. Facilitate Continuous Improvement | PASS | Tests catch rollout-boundary and contract regressions. |
| XI. Spec-Driven Development | PASS | Requirements traceability maps source document requirements to implementation and validation. |
| XII. Canonical Documentation Separation | PASS | No canonical doc migration checklist is added. |
| XIII. Delete, Don't Deprecate | PASS | No legacy aliases or fallback contract names are planned. |

## Complexity Tracking

No constitution violations.
