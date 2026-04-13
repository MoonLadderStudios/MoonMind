# Research: Jira Failure Handling

## Decision: Normalize Jira browser errors at the MoonMind router boundary

**Rationale**: The Create page consumes MoonMind-owned Jira browser endpoints, so every browser-visible failure should be shaped before it leaves the API service. Known Jira browser failures already carry stable code, status, and action metadata; the router can expose that safely while replacing secret-like messages with a generic failure. Unexpected exceptions should be logged server-side and returned as a safe structured error.

**Alternatives considered**:

- Let raw FastAPI exception handling return generic 500 responses. Rejected because it loses the Jira-specific source/action context and can expose inconsistent failure shapes.
- Push all error interpretation to the frontend. Rejected because the browser should not understand provider internals or inspect raw Jira failures.

## Decision: Preserve empty Jira browser responses as successful renderable states

**Rationale**: Empty projects, boards, columns, or issues are not failures. They should remain successful responses with empty collections or empty normalized models so the browser can show explicit empty-state copy and keep manual task creation available.

**Alternatives considered**:

- Convert empty Jira results into API errors. Rejected because an empty board or column is a valid Jira state and should not block manual task creation.
- Invent a separate empty-state endpoint. Rejected because existing list/detail contracts already carry enough structure to render empties.

## Decision: Render Jira frontend failures only inside the shared browser panel

**Rationale**: Jira is an optional instruction source. React Query errors for projects, boards, columns, issues, and issue detail should be converted into local browser-panel notices. Manual step fields, preset fields, runtime controls, and Create submission should remain independent.

**Alternatives considered**:

- Surface Jira failures as global Create page alerts. Rejected because global alerts imply the main form is impaired.
- Disable the Create button after Jira browser failure. Rejected because it violates the additive Jira contract and blocks manual authoring.

## Decision: Keep task submission contract unchanged

**Rationale**: Phase 8 is failure handling only. Jira import may have already copied text into existing fields, but Jira availability must not add submission fields, change objective precedence, or alter the Temporal-backed create path.

**Alternatives considered**:

- Submit Jira failure/provenance metadata with the task. Rejected because there is no downstream consumer in this phase and earlier Jira phases intentionally kept provenance local.
- Add a Jira-specific task source state. Rejected because Jira remains an instruction source, not a task model.

## Decision: Validate with focused backend and frontend unit coverage

**Rationale**: The risk is concentrated in error response shape, secret safety, local UI rendering, and manual submission after Jira failure. Focused router/service tests and Create page tests can exercise these behaviors without external Jira credentials.

**Alternatives considered**:

- Provider verification tests against live Jira. Rejected for this phase because failure-shaping behavior is local to MoonMind and should be hermetic.
- Manual-only browser verification. Rejected because failure isolation is regression-prone and must be automated.
