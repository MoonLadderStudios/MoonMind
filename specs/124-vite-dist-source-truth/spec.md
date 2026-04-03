# Feature Specification: vite-dist-source-truth

**Feature Branch**: `124-vite-dist-source-truth`
**Created**: 2026-04-03
**Status**: In Progress
**Input**: User description: "Implement the Option 1 highest ROI simplification: kill checked-in `dist/` as a source of truth, delete the sync workflow, stop gating PRs on `dist/` diffs, and keep CI/Docker building UI bundles from source."

## User Scenarios & Testing

### User Story 1 - Frontend bundles have one authoritative source (Priority: P1)

Maintainers need Mission Control bundles to come only from frontend source plus the build pipeline so repo history and CI are not split between source and committed build output.

**Why this priority**: The checked-in `dist/` tree and sync workflow create a second source of truth for the frontend.

**Independent Test**: Repository scripts, CI config, and tracked files show that `api_service/static/task_dashboard/dist/` is built from source and no longer checked into git or diff-gated in PR validation.

### User Story 2 - Contributors still have deterministic verification (Priority: P1)

Contributors need a clear way to verify both tracked generated files and runtime frontend bundles locally and in CI.

**Why this priority**: Removing committed `dist/` should not weaken build verification or hide manifest errors.

**Independent Test**: Local/frontend CI runs still typecheck, lint, test, build, and verify the Vite manifest; backend unit tests no longer rely on committed `dist/`.

## Requirements

### Functional Requirements

- **FR-001**: `api_service/static/task_dashboard/dist/` MUST be treated as generated runtime output only, not as a checked-in source of truth.
- **FR-002**: The repository MUST NOT include the `Sync Vite dist` workflow or any PR gate that requires `git diff` cleanliness for `api_service/static/task_dashboard/dist/`.
- **FR-003**: Frontend CI MUST continue to build Mission Control bundles from source and MAY upload the built `dist/` tree as an artifact.
- **FR-004**: Docker production builds MUST continue to build Mission Control bundles from source before copying them into the runtime image.
- **FR-005**: Contributor documentation and local scripts MUST distinguish between checked-in generated API types and untracked frontend build output.
- **FR-006**: Tests that verify the Vite manifest tooling MUST NOT depend on committed `dist/` files existing in the repository.

## Success Criteria

- **SC-001**: `git ls-files api_service/static/task_dashboard/dist` returns no tracked frontend bundle files.
- **SC-002**: `package.json` and GitHub Actions no longer reference a `dist/` git-diff check.
- **SC-003**: `npm run ci:test` still completes a clean Vite build and manifest verification path from source.
- **SC-004**: Documentation consistently states that `dist/` is built from source and is not committed.
