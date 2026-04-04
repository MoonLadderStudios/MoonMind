# Implementation Plan: mission-control-single-entrypoint

**Branch**: `127-mission-control-single-entrypoint` | **Date**: 2026-04-04 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/127-mission-control-single-entrypoint/spec.md`

## Summary

Collapse Mission Control onto one Vite entrypoint and one React root by introducing a shared `mission-control.tsx` bootstrap that reads `payload.page`, renders an `AppShell`, lazy-loads the page module, and absorbs the former `dashboard-alerts` mount. Keep FastAPI routes, boot payload generation, and page-specific initial data intact while simplifying asset lookup and manifest verification to one shared entry key.

## Technical Context

**Language/Version**: Python 3.12 backend, TypeScript/React 19 frontend  
**Primary Dependencies**: FastAPI/Jinja templates, Vite, React Query, React lazy/Suspense, Vitest, pytest  
**Storage**: N/A  
**Testing**: `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx`, `./tools/test_unit.sh --python-only --no-xdist tests/api_service/test_ui_assets.py tests/api_service/test_ui_assets_strict.py tests/api_service/test_task_dashboard_ui_assets_errors.py tests/unit/api/routers/test_task_dashboard.py tests/tools/test_verify_vite_manifest.py`, `npm run ui:build:check`, `./tools/test_unit.sh`  
**Target Platform**: FastAPI-hosted Mission Control web UI  
**Project Type**: Web application with server-rendered HTML shell and Vite-built React frontend  
**Performance Goals**: Preserve current route load behavior while reducing duplicate boot code and avoiding extra initial bundle URLs per page  
**Constraints**: Keep FastAPI route map and boot payload contract, avoid a full SPA router rewrite, preserve page-specific initial data and wide-panel layout behavior, keep alert behavior inside the shared React shell  
**Scale/Scope**: Mission Control frontend bootstrap, backend asset injection, route shell template, related tests, and frontend architecture docs

## Constitution Check

- **II. One-Click Agent Deployment**: PASS. The change stays inside the existing repo-managed FastAPI/Vite build and does not add operator prerequisites.
- **VI. Design for Deletion / Thin Scaffolding**: PASS. This removes duplicate entrypoint scaffolding and the second alert root instead of layering more verification on top.
- **VII. Powerful Runtime Configurability**: PASS. Dev-server and built-asset behavior remain env-driven through the existing asset helper.
- **VIII. Modular and Extensible Architecture**: PASS. FastAPI keeps route ownership while the frontend centralizes boot logic behind one shared app shell.
- **IX. Resilient by Default**: PASS. The app will still fail loudly on missing assets, and the shared client entry adds an explicit unknown-page fallback instead of silent failure.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The route, asset, and frontend boot contract are captured here before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical Mission Control frontend docs will be updated to reflect the target-state single-entry architecture.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The per-page boot entries and separate alert root are removed rather than kept as compatibility aliases.

## Scope

### In Scope

- Add `frontend/src/entrypoints/mission-control.tsx` as the only Vite entrypoint
- Convert existing Mission Control page modules into lazy-loadable React components with no `mountPage(...)` side effects
- Move dashboard-alert rendering into the shared app shell under the single React root
- Update the FastAPI route/template path to always inject the shared entrypoint while preserving `boot_payload.page` and per-route initial data
- Preserve page-level layout state such as the wider data panel through the shared shell
- Simplify manifest verification and update tests/docs to reflect one manifest key

### Out of Scope

- Replacing FastAPI routes with a client-side router
- Reworking page internals such as task list filtering or task detail data loading beyond what the shared shell requires
- Changing API payload shapes beyond the boot payload additions needed for shared-shell layout metadata

## Research Summary

- `api_service/api/routers/task_dashboard.py` currently renders each route with its own entrypoint name and also injects `dashboard-alerts`, which forces one page render to load two Vite entries.
- `api_service/templates/react_dashboard.html` currently includes both `dashboard-alerts-root` and `mission-control-root`, so the browser boots two React roots.
- `frontend/vite.config.ts` enumerates one input per page, and `tools/verify_vite_manifest.py` exists mainly to verify that long list stayed synchronized with the manifest.
- Existing page modules already accept the boot payload; the primary blocker to lazy loading them is their module-level `mountPage(...)` side effect.

## Structure Decision

- Keep FastAPI route handlers and `generate_boot_payload(page=...)` semantics unchanged from the caller perspective, but have `_render_react_page(...)` always request `ui_assets("mission-control")`.
- Add a shared Mission Control app shell that reads `payload.initialData.layout.dataWidePanel` to recreate the existing narrow/wide page wrapper behavior while also rendering dashboard alerts under the same root.
- Keep the current page modules in `frontend/src/entrypoints/` as page components for now, but remove their module-side boot behavior and lazy-load them from the shared entrypoint. This preserves current imports and limits churn.
- Keep `tools/verify_vite_manifest.py` as a light smoke test for the single shared entry instead of deleting it outright; that matches the user goal of making it mostly unnecessary without dropping the build check completely.

## Verification Plan

1. Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` with `SPECIFY_FEATURE=127-mission-control-single-entrypoint`.
2. Run `npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx`.
3. Run `./tools/test_unit.sh --python-only --no-xdist tests/api_service/test_ui_assets.py tests/api_service/test_ui_assets_strict.py tests/api_service/test_task_dashboard_ui_assets_errors.py tests/unit/api/routers/test_task_dashboard.py tests/tools/test_verify_vite_manifest.py`.
4. Run `npm run ui:build:check`.
5. Run `./tools/test_unit.sh`.
