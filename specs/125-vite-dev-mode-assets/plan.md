# Implementation Plan: vite-dev-mode-assets

**Branch**: `125-vite-dev-mode-assets` | **Date**: 2026-04-03 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/125-vite-dev-mode-assets/spec.md`

## Summary

Add an explicit env-driven dev-server branch to `api_service/ui_assets.py` so FastAPI-backed Mission Control routes can load `@vite/client` and page entrypoints directly from the Vite dev server during development, while keeping manifest validation and 503 behavior unchanged everywhere else.

## Technical Context

**Language/Version**: Python 3.12, Markdown, Vite entrypoint URLs  
**Primary Dependencies**: FastAPI task dashboard router, Vite dev server, pytest  
**Testing**: `./tools/test_unit.sh --python-only --no-xdist tests/api_service/test_ui_assets.py tests/api_service/test_ui_assets_strict.py tests/api_service/test_task_dashboard_ui_assets_errors.py tests/unit/api/routers/test_task_dashboard.py`  
**Project Type**: Backend-served web application with Vite-built frontend entrypoints  
**Constraints**: Preserve strict manifest-backed production behavior, keep config explicit, avoid hidden fallback logic

## Constitution Check

- **II. One-Click Agent Deployment**: PASS. Docker and production runtime behavior stay source-built and unchanged.
- **VI. Design for Deletion / Thin Scaffolding**: PASS. This removes ambiguity with a single explicit dev path instead of extending manifest-era glue.
- **VII. Powerful Runtime Configurability**: PASS. Dev mode is controlled by one explicit env var with deterministic behavior.
- **VIII. Modular and Extensible Architecture**: PASS. Asset ownership remains isolated in the UI asset helper and route shell.
- **IX. Resilient by Default**: PASS. Strict 503 behavior remains the default when dev mode is not enabled.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. The runtime contract change is captured before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical UI docs will describe the steady-state dev workflow directly.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. The change adds one explicit path instead of compatibility fallbacks.

## Scope

### In Scope

- `api_service/ui_assets.py` dev-server asset branch
- Task-dashboard route tests covering the new branch
- README and UI architecture/docs updates for the dev workflow

### Out of Scope

- Converting Mission Control to a single frontend entrypoint
- Changing Vite production bundle output paths
- Relaxing manifest validation or production error handling

## Structure Decision

- Introduce a helper in `api_service/ui_assets.py` that returns a normalized Vite dev-server origin when `MOONMIND_UI_DEV_SERVER_URL` is configured.
- Have `ui_assets(entrypoint)` short-circuit to dev-server tags before any manifest I/O when that origin is present.
- Keep route-level dedupe in `task_dashboard.py` unchanged so combined entrypoints still collapse duplicate `@vite/client` tags.
- Update docs to position `npm run ui:dev` plus `MOONMIND_UI_DEV_SERVER_URL=http://127.0.0.1:5173` as the FastAPI-backed development path.

## Verification Plan

1. Run `./tools/test_unit.sh --python-only --no-xdist tests/api_service/test_ui_assets.py tests/api_service/test_ui_assets_strict.py tests/api_service/test_task_dashboard_ui_assets_errors.py tests/unit/api/routers/test_task_dashboard.py`.
2. Run `npm run ui:typecheck`.
3. Run `npm run ui:lint`.
