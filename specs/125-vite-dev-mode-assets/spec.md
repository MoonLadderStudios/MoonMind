# Feature Specification: vite-dev-mode-assets

**Feature Branch**: `125-vite-dev-mode-assets`
**Created**: 2026-04-03
**Status**: In Progress
**Input**: User description: "Implement suggestion 2 from the UI improvement recommendations by making FastAPI-backed Mission Control pages support a real Vite dev-server mode instead of always resolving built manifest assets."

## User Scenarios & Testing

### User Story 1 - FastAPI pages can use the Vite dev server during development (Priority: P1)

Frontend contributors need FastAPI-backed Mission Control routes to load live Vite modules during development so browser state matches the TSX source they are editing.

**Why this priority**: The current manifest-only runtime path makes it easy to mistake stale built assets for live source changes.

**Independent Test**: With the dev-server env var configured, `ui_assets()` emits the Vite client and requested entrypoint module URLs instead of manifest-backed `dist/` tags.

### User Story 2 - Production stays strict and manifest-backed (Priority: P1)

Operators need production and CI behavior to remain strict, manifest-backed, and loud on missing assets.

**Why this priority**: Dev convenience must not weaken runtime correctness or the existing 503 failure posture.

**Independent Test**: Without the dev-server env var, `ui_assets()` still reads the manifest, validates referenced files, and existing strict failure tests continue to pass.

## Requirements

### Functional Requirements

- **FR-001**: Mission Control asset resolution MUST support an explicit opt-in FastAPI-backed Vite dev-server mode controlled by configuration.
- **FR-002**: In dev-server mode, the rendered HTML MUST include one module script for `@vite/client` and one module script for the requested entrypoint from the configured dev server origin.
- **FR-003**: Dev-server mode MUST remain compatible with the existing route-level asset dedupe so shared assets like `@vite/client` appear only once when multiple entrypoints are combined.
- **FR-004**: When dev-server mode is not enabled, Mission Control asset resolution MUST preserve the existing strict manifest-backed behavior.
- **FR-005**: Contributor documentation MUST describe how to use FastAPI-backed dev mode and make clear that manifest-backed `dist/` assets remain the production path.

## Success Criteria

- **SC-001**: A targeted backend unit test verifies that dev-server mode emits `@vite/client` and `/entrypoints/<page>.tsx` URLs from the configured origin.
- **SC-002**: Existing strict manifest tests continue to pass without modification to production semantics.
- **SC-003**: README and canonical UI docs document the env-driven FastAPI-backed dev workflow and the unchanged production manifest path.
