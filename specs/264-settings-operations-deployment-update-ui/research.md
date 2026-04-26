# Research: Settings Operations Deployment Update UI

## Classification

Decision: Treat `MM-522` as a single-story runtime UI feature.
Evidence: The preserved Jira brief names one actor, one Settings Operations card, one source design slice, and one acceptance set.
Rationale: The story is independently testable through the Settings UI with mocked deployment endpoints.
Alternatives considered: Running breakdown was rejected because the brief already selects one bounded story.
Test implications: UI integration tests cover the story.

## Existing Backend Surface

Decision: Reuse existing deployment operation endpoints.
Evidence: `api_service/api/routers/deployment_operations.py` exposes `GET /api/v1/operations/deployment/stacks/{stack}`, `GET /api/v1/operations/deployment/image-targets`, and `POST /api/v1/operations/deployment/update`; `tests/unit/api/routers/test_deployment_operations.py` covers the typed backend shape.
Rationale: MM-518 through MM-521 already built the backend and executable contract foundations.
Alternatives considered: Adding a new endpoint was rejected because it would duplicate the existing typed operation contract.
Test implications: Frontend tests can mock the existing endpoints; backend route tests remain existing evidence.

## Operations UI Gap

Decision: Extend `OperationsSettingsSection` rather than adding a new top-level page.
Evidence: `frontend/src/entrypoints/settings.tsx` already routes `section=operations` to `OperationsSettingsSection`; current component only renders worker pause controls.
Rationale: Source section 6.1 requires Settings -> Operations placement.
Alternatives considered: A top-level deployment page was rejected by the Jira acceptance criteria.
Test implications: Test rendering the component and assert no top-level deployment navigation is introduced.

## Target Selection And Confirmation

Decision: Prefer recent release tags or digest references over mutable tags by default, warn for `latest`, and use `window.confirm` for the first confirmation implementation.
Evidence: Image target response exposes `recentTags`, `allowedReferences`, and `digestPinningRecommended`.
Rationale: This satisfies the source UX without adding a new modal framework.
Alternatives considered: Blocking until backend returns resolved digest for all tags was rejected because UI can warn and backend remains authoritative.
Test implications: UI tests assert default selection, mutable warning, confirmation text, and POST payload.

## Recent Actions

Decision: Render currently available last update run id and defensively render richer optional recent action fields if the API later includes them.
Evidence: Current `DeploymentStackStateResponse` includes `lastUpdateRunId`; the source design requires richer recent action presentation when available.
Rationale: The UI can avoid overclaiming unavailable data while remaining forward-compatible with optional fields in response JSON.
Alternatives considered: Adding persistent recent-action storage was rejected as outside this UI story and contrary to the existing no-new-storage plan.
Test implications: UI tests include optional recent action fields in mocked responses.
