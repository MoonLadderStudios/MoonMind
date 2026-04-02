# Feature Specification: typescript-system-completion

**Feature Branch**: `123-typescript-system-completion`
**Created**: 2026-04-02
**Status**: Complete
**Input**: User description: "Complete all remaining work in the transition to the typescript system described in docs\\tmp\\063-UI-TypeScriptSystem.md. Use Test-Driven Development whenever possible. Reference docs\\UI\\TypeScriptSystem.md for general guidance. Create a PR when done."

## Source Requirements

- **DOC-REQ-001**: All remaining Mission Control pages must use TypeScript/Vite entrypoints before legacy retirement can complete. Source: `docs/tmp/063-UI-TypeScriptSystem.md` Phase 2 / Phase 3.
- **DOC-REQ-002**: Server-owned routes remain canonical; no SPA takeover is allowed during the migration. Source: `docs/UI/TypeScriptSystem.md` §§4, 5, 11, 15.
- **DOC-REQ-003**: The legacy `dashboard.js` monolith and `task_dashboard.html` fallback shell must be removed once no routes require them. Source: `docs/tmp/063-UI-TypeScriptSystem.md` Phase 3.
- **DOC-REQ-004**: Dashboard CSS ownership must move to the Vite/PostCSS pipeline instead of the standalone `dashboard:css` path. Source: `docs/tmp/063-UI-TypeScriptSystem.md` Phase 3 and `docs/UI/TypeScriptSystem.md` §13.2.
- **DOC-REQ-005**: Operator and contributor docs must describe the TypeScript/Vite path as the adopted Mission Control frontend system, with migration tracker state updated or closed out. Source: `docs/tmp/063-UI-TypeScriptSystem.md` Phase 3 and `docs/UI/TypeScriptSystem.md`.

## User Scenarios & Testing

### User Story 1 - Operators Can Use Every Remaining Mission Control Page Without Legacy JS (Priority: P1)

Operators need `/tasks/new`, `/tasks/create`, `/tasks/manifests/new`, and `/tasks/skills` to render through the TypeScript system so the remaining legacy shell can be removed.

**Why this priority**: The migration is not complete while these routes still depend on `dashboard.js` and `task_dashboard.html`.

**Independent Test**: Request each route from FastAPI and verify it renders the React/Vite shell instead of the legacy task dashboard shell markers.

**Acceptance Scenarios**:

1. **Given** an authenticated operator requests `/tasks/new`, **When** the page renders, **Then** it includes the Vite boot payload and React mount shell instead of `task-dashboard-config` and the legacy monolith.
2. **Given** an authenticated operator requests `/tasks/manifests/new`, **When** the page renders, **Then** it mounts a TypeScript entrypoint for manifest submission.
3. **Given** an authenticated operator requests `/tasks/skills`, **When** the page renders, **Then** it mounts a TypeScript entrypoint for skills list/create behavior.

### User Story 2 - Task Creation Works Through the React Surface (Priority: P1)

Operators need the unified create page to submit Temporal executions through the React/Vite system, including runtime/profile selection and large-instructions artifact upload.

**Why this priority**: The create flow is a core Mission Control action and is one of the remaining legacy-owned pages.

**Independent Test**: Browser or component coverage submits the create form, verifies the request shape, and verifies redirect/error behavior.

**Acceptance Scenarios**:

1. **Given** a user fills the create form with instructions, repository, and skill, **When** they submit successfully, **Then** the UI posts the queue-style Temporal payload to `/api/executions` and redirects to the Temporal task detail route.
2. **Given** the selected runtime has provider profiles, **When** the runtime changes, **Then** the provider-profile selector updates to the profiles for that runtime.
3. **Given** instructions exceed inline limits, **When** the user submits, **Then** the UI creates/uploads/links an artifact and sends `inputArtifactRef` instead of the oversized inline instructions.

### User Story 3 - Legacy Retirement Removes the Parallel Frontend Story (Priority: P1)

Maintainers need one frontend system and one build path so future UI work does not keep paying legacy-maintenance cost.

**Why this priority**: The stated goal of the migration is a single TypeScript/Vite/React system under FastAPI-owned routes.

**Independent Test**: Build/test tooling and repo inspection confirm the old shell/bundle/CSS pipeline are no longer required.

**Acceptance Scenarios**:

1. **Given** the frontend is built from source, **When** FastAPI resolves page assets, **Then** the shared Mission Control CSS comes from the Vite manifest rather than `/static/task_dashboard/dashboard.css`.
2. **Given** the repository test workflow runs, **When** unit/frontend tests execute, **Then** they no longer execute `dashboard.js` runtime tests.
3. **Given** the docs are reviewed after implementation, **When** contributors read the UI system docs, **Then** they see the TypeScript/Vite system as the adopted baseline and the tmp tracker reflects completion.

## Requirements

### Functional Requirements

- **FR-001**: `/tasks/new`, `/tasks/create`, `/tasks/manifests/new`, and `/tasks/skills` MUST render React/Vite entrypoints from `react_dashboard.html`.
- **FR-002**: The create page MUST preserve the current Temporal submit behavior for instructions, runtime/model/effort, repository, publish mode, skill selection, provider profile selection, and large-input artifact upload.
- **FR-003**: The manifest submit page MUST allow submitting manifest-shaped Temporal execution requests from the React/Vite frontend.
- **FR-004**: The skills page MUST list available skills, preview markdown content, and create new local skills through `/api/tasks/skills`.
- **FR-005**: `task_dashboard.html` and `api_service/static/task_dashboard/dashboard.js` MUST be removed once no routes depend on them.
- **FR-006**: Shared Mission Control CSS MUST be owned by the frontend source tree and emitted through Vite/PostCSS, not by standalone `dashboard:css` scripts.
- **FR-007**: React-rendered Mission Control pages MUST still load the dashboard alerts surface.
- **FR-008**: Test tooling MUST run TypeScript frontend tests instead of legacy `dashboard.js` runtime scripts.
- **FR-009**: UI docs and migration tracking MUST be updated to describe the completed TypeScript system accurately.

### Key Entities

- **React Mission Control Shell**: `react_dashboard.html` plus Vite entrypoint assets injected by `ui_assets`.
- **Task Create Payload**: Queue-shaped `POST /api/executions` request for Temporal-backed task submission, optionally carrying `inputArtifactRef`.
- **Mission Control Shared CSS**: The canonical Tailwind/PostCSS stylesheet imported from the frontend source tree and emitted by Vite.

## Success Criteria

### Measurable Outcomes

- **SC-001**: FastAPI route tests show the remaining previously-legacy routes now render the React shell.
- **SC-002**: Frontend tests and browser-facing tests cover successful create submission, runtime-profile switching, artifact-backed long instructions, manifest submission, and skill creation/list behavior.
- **SC-003**: The repository no longer contains a live `dashboard.js`-driven Mission Control shell or a standalone `dashboard:css` build path.
- **SC-004**: Contributor/operator docs describe the TypeScript system as adopted and the temporary migration tracker reflects the completed state.
