# Feature Specification: mission-control-single-entrypoint

**Feature Branch**: `127-mission-control-single-entrypoint`
**Created**: 2026-04-04
**Status**: Completed
**Input**: User description: "Collapse Mission Control to one frontend entrypoint. You do not need a full SPA rewrite to get the benefit. Keep FastAPI routes and the boot payload, but replace the many page entrypoints plus dashboard-alerts with one mission-control.tsx entry that reads payload.page, renders a single AppShell, and lazy-loads the appropriate page component. That would eliminate the extra React root, simplify ui_assets() usage to one manifest key, and make verify_vite_manifest.py mostly unnecessary."

## User Scenarios & Testing

### User Story 1 - FastAPI routes boot one Mission Control bundle (Priority: P1)

Mission Control maintainers need every FastAPI-served Mission Control page to inject one frontend bundle while preserving the existing route map and boot payload contract.

**Why this priority**: The current many-entrypoint manifest contract adds unnecessary backend branching, an extra alert bundle, and extra failure surface without changing the route model.

**Independent Test**: A backend route test confirms multiple `/tasks/*` pages still render their boot payloads while injecting only the shared `mission-control` bundle in both built-asset mode and dev-server mode.

**Acceptance Scenarios**:

1. **Given** FastAPI renders `/tasks/list`, `/tasks/new`, `/tasks/settings`, or a task detail route, **When** the HTML shell is returned, **Then** it includes the page-specific boot payload and injects only the shared Mission Control bundle assets.
2. **Given** `MOONMIND_UI_DEV_SERVER_URL` is configured, **When** a Mission Control route is rendered, **Then** FastAPI injects `@vite/client` and `/entrypoints/mission-control.tsx` instead of per-page entrypoint URLs.

---

### User Story 2 - One React root selects the page module lazily (Priority: P1)

Frontend maintainers need the client runtime to mount once, render a shared app shell, and choose the requested page module from `payload.page` without a second React root for alerts.

**Why this priority**: The current page-level entrypoints duplicate boot wiring and force a second root just to show dashboard alerts.

**Independent Test**: A frontend test confirms the shared Mission Control entry renders the alert shell and lazy-loads the correct page component from `payload.page`, while an unknown page fails with an explicit in-app error state instead of a silent blank region.

**Acceptance Scenarios**:

1. **Given** the boot payload contains `page: "tasks-list"`, **When** the shared Mission Control entry boots, **Then** it renders one React root, includes the alert shell, and lazy-loads the tasks list page component.
2. **Given** the boot payload contains `page: "task-detail"` plus page-specific initial data, **When** the shared Mission Control entry boots, **Then** it passes the existing boot payload through to the task detail page component unchanged.
3. **Given** the boot payload contains an unsupported `page` value, **When** the shared Mission Control entry boots, **Then** Mission Control renders an explicit error state naming the unknown page.

---

### User Story 3 - Manifest verification follows the single-entry contract (Priority: P2)

Frontend maintainers need manifest verification and docs to reflect the single-entry architecture so build checks stop treating every page module as an independent Vite entrypoint.

**Why this priority**: The current verification script mostly exists to prove the per-page entry list stayed synchronized with the manifest.

**Independent Test**: The manifest verification script succeeds on a synthetic repo with a single `mission-control` entry and fails when that shared entry or its emitted files are missing.

**Acceptance Scenarios**:

1. **Given** the Vite config defines only the shared Mission Control entry, **When** manifest verification runs, **Then** it validates the shared entry and its emitted files instead of enumerating one key per page.
2. **Given** contributors follow the frontend docs, **When** they run local build/dev commands, **Then** the docs describe the shared Mission Control entrypoint and page selection by `payload.page`.

### Edge Cases

- Route-specific boot payload data such as dashboard config and worker-pause endpoints continues to reach the selected page component unchanged.
- Pages that should render with the wider data panel keep that layout under the shared shell.
- Unknown or mistyped `payload.page` values fail fast inside the Mission Control app instead of silently rendering nothing.
- Lazy loading page modules must not recreate a second React root or re-import legacy `mountPage(...)` side effects.

## Requirements

### Functional Requirements

- **FR-001**: Mission Control MUST ship one Vite boot entrypoint at `frontend/src/entrypoints/mission-control.tsx`.
- **FR-002**: FastAPI Mission Control routes MUST continue to set `boot_payload.page` to the requested page identifier while loading frontend assets from the shared Mission Control entrypoint only.
- **FR-003**: The shared Mission Control frontend entry MUST mount exactly one React root per page and MUST render a shared app shell that includes the first-run dashboard alerts previously mounted separately.
- **FR-004**: The shared Mission Control frontend entry MUST choose the page component from `boot_payload.page` and lazy-load the matching module instead of eagerly bundling a separate page bootstrap file for each route.
- **FR-005**: Mission Control page modules used by the shared entry MUST be safe to import lazily, which means they MUST NOT call `mountPage(...)` or create their own React roots as module side effects.
- **FR-006**: Mission Control MUST preserve route-specific boot payload data and page-level layout behavior when moving under the shared app shell.
- **FR-007**: Mission Control MUST render an explicit error state when `boot_payload.page` does not map to a supported page module.
- **FR-008**: `api_service/ui_assets.py`, its tests, and backend route tests MUST reflect the single manifest key and single dev-server entrypoint URL.
- **FR-009**: `tools/verify_vite_manifest.py` and its tests MUST validate the shared Mission Control entry contract rather than a per-page entrypoint list.
- **FR-010**: Canonical frontend/operator docs that describe the Mission Control boot pipeline MUST be updated to describe the single-entry architecture and the continued use of `boot_payload.page`.

## Success Criteria

- **SC-001**: Mission Control route HTML no longer includes `dashboard-alerts-root` and no longer injects multiple page bundle URLs for one page render.
- **SC-002**: Backend tests confirm built-asset and dev-server mode route renders inject only the shared Mission Control entrypoint.
- **SC-003**: Frontend tests confirm the shared Mission Control app shell loads the correct page from `payload.page` and reports unknown pages explicitly.
- **SC-004**: Manifest verification passes with the shared entrypoint contract and no longer depends on enumerating every page module as a Vite entry.
- **SC-005**: `npm run ui:test`, `npm run ui:build:check`, and `./tools/test_unit.sh` pass after the consolidation.
