# Implementation Plan: Create Page Repository Dropdown

**Branch**: `203-repository-dropdown` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story feature specification from `/specs/203-repository-dropdown/spec.md`

**Note**: This plan follows the Moon Spec lifecycle for MM-393 and treats the feature as runtime implementation work.

## Summary

Task authors need repository suggestions on the Create page so they can select configured and credential-visible Git repositories without typing owner/repo values manually. The implementation will add a MoonMind-owned repository option model and API-backed discovery helper on the dashboard view-model boundary, expose repository option metadata in the Create page boot payload, and render those options as editable datalist suggestions in the repository field. Tests cover backend normalization/discovery and frontend selection/submission behavior.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React  
**Primary Dependencies**: FastAPI dashboard runtime config helpers, Pydantic settings, `httpx`, React, Vitest, pytest  
**Storage**: No new persistent storage; options are derived from configuration and best-effort GitHub API responses at runtime  
**Unit Testing**: `./tools/test_unit.sh` for final unit verification; focused pytest and Vitest commands during iteration  
**Integration Testing**: Existing Create page Vitest integration-style tests and FastAPI/dashboard view-model tests; no compose-backed integration required because no persistent service boundary changes  
**Target Platform**: MoonMind API service and Mission Control Create page  
**Project Type**: Web application with Python API/control plane and TypeScript frontend  
**Performance Goals**: Create page rendering remains responsive; repository option discovery is bounded and best-effort  
**Constraints**: Browser clients must call MoonMind APIs/configuration only and must never receive raw GitHub credentials, secret refs, or credential-bearing URLs  
**Scale/Scope**: One Create page repository field and runtime boot payload path; repository discovery is limited to configured repositories plus a bounded GitHub repository listing

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Repository discovery stays behind MoonMind control-plane boundaries and does not alter agent runtimes.
- II. One-Click Agent Deployment: PASS. Missing GitHub credentials degrade to configured/default repositories without blocking local startup.
- III. Avoid Vendor Lock-In: PASS. GitHub-specific discovery is optional and isolated behind repository option source metadata.
- IV. Own Your Data: PASS. The browser receives only non-secret repository option metadata.
- V. Skills Are First-Class: PASS. No skill contract changes.
- VI. Replaceable Scaffolding: PASS. The repository option helper is thin and can be replaced by richer connectors later.
- VII. Runtime Configurability: PASS. Configured repositories and credentials drive behavior at runtime.
- VIII. Modular and Extensible Architecture: PASS. The change is scoped to dashboard view-model/runtime config and Create page rendering.
- IX. Resilient by Default: PASS. Discovery errors are best-effort and do not block manual authoring.
- X. Facilitate Continuous Improvement: PASS. Tests and MoonSpec artifacts preserve evidence.
- XI. Spec-Driven Development: PASS. This plan implements the single-story spec.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Implementation tracking remains under `specs/` and `docs/tmp/`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden fallbacks are introduced for internal contracts.

## Project Structure

### Documentation (this feature)

```text
specs/203-repository-dropdown/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── repository-options.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
└── api/routers/task_dashboard_view_model.py

frontend/
└── src/entrypoints/task-create.tsx

tests/
└── unit/api/routers/test_task_dashboard_view_model.py

frontend/src/entrypoints/
└── task-create.test.tsx
```

**Structure Decision**: Use the existing Mission Control web app structure: Python builds runtime boot configuration and TypeScript renders the Create page form. No new package, table, migration, or runtime worker component is needed.

## Complexity Tracking

No constitution violations.
