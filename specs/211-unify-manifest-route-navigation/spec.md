# Feature Specification: Unify Manifest Route And Navigation

**Feature Branch**: `211-unify-manifest-route-navigation`  
**Created**: 2026-04-19  
**Input**: Jira Orchestrate for MM-418. Source story: STORY-001. Source summary: "Unify manifest route and navigation." Source Jira issue: unknown. Original brief reference: not provided.

## User Story 1 - Launch And Monitor Manifests From One Page (Priority: P1)

As a Mission Control operator, I can open one Manifests destination, start either a registry-backed or inline manifest run, and see recent manifest runs on the same page without using a separate Manifest Submit tab.

**Independent Test**: Visit `/tasks/manifests`, verify the page contains both the run form and recent runs table, submit registry and inline manifest runs through the existing manifest APIs, and verify the page refreshes recent runs in place.

### Acceptance Scenarios

1. **Given** an operator views Mission Control navigation, **When** manifest destinations are listed, **Then** only `Manifests` appears and no separate `Manifest Submit` top-level item is present.
2. **Given** an operator requests the legacy `/tasks/manifests/new` route, **When** the route is handled, **Then** the operator is redirected to `/tasks/manifests`.
3. **Given** an operator opens `/tasks/manifests`, **When** the page renders, **Then** the manifest run form and recent manifest runs table appear together.
4. **Given** an inline YAML manifest is submitted, **When** the backend accepts the upsert and run request, **Then** the page reports the started run and refreshes recent runs without navigating away.
5. **Given** a registry manifest is submitted, **When** the backend accepts the run request, **Then** no manifest body is re-uploaded and the page refreshes recent runs without navigating away.

## Requirements

- **FR-001**: `/tasks/manifests` MUST be the only top-level Mission Control navigation destination for manifest operations.
- **FR-002**: The legacy `/tasks/manifests/new` route MUST redirect to `/tasks/manifests`.
- **FR-003**: `/tasks/manifests` MUST include a manifest run form and recent manifest runs in one vertical page flow.
- **FR-004**: The run form MUST support registry manifest execution and inline YAML submission through the existing `/api/manifests` endpoints.
- **FR-005**: Successful manifest submission MUST keep the operator on `/tasks/manifests` and refresh recent run data in place.
- **FR-006**: Advanced manifest run options MUST be collapsed by default.
- **FR-007**: The implementation MUST NOT introduce raw secret entry or change manifest execution backend semantics.
- **FR-008**: Dashboard 404 guidance MUST list only current canonical dashboard routes.

## Success Criteria

- **SC-001**: Backend route tests prove `/tasks/manifests/new` redirects to `/tasks/manifests` and navigation exposes a single manifest destination.
- **SC-002**: Frontend tests prove the unified Manifests page renders the run form and recent runs together.
- **SC-003**: Frontend tests prove inline and registry submissions use the existing APIs and refresh in place.
- **SC-004**: Focused unit validation passes for the modified router and frontend entrypoint.

## Scope Boundaries

- In scope: dashboard route handling, Mission Control navigation, the Manifests React entrypoint, focused unit tests, and traceability artifacts.
- Out of scope: redesigning manifest detail pages, changing manifest execution workflows, adding saved manifest registry browsing, or changing persistence.
