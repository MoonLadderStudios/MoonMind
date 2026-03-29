# Specification: Fully implement Phase 6 from docs/tmp/009-LiveLogsPlan.md

## High-Level Objective
Retire the deprecated session-based (`tmate` / `web_ro`) observability path for managed runs across the stack. Ensure historical runs degrade gracefully via artifact-backed displays while the legacy observability models are stripped out entirely.

## Source Document Requirements
- **DOC-REQ-001**: **Citation**: `Phase 6 - Tasks`. **Requirement**: Add compatibility handling for historical runs that rely on legacy session/transcript data gracefully in the UI when new artifacts are missing.
- **DOC-REQ-002**: **Citation**: `Phase 6 - Tasks`. **Requirement**: Explicitly deprecate legacy terminal-session observability records in code annotations and architecture documentation.
- **DOC-REQ-003**: **Citation**: `Phase 6 - Tasks`. **Requirement**: Eliminate legacy `web_ro`-driven viewer API and feature paths entirely in the context of managed run logging.
- **DOC-REQ-004**: **Citation**: `Phase 6 - Tasks`. **Requirement**: Remove any launcher logic or agent runtime paths that invoked `tmate` purely for live log streaming visibility.
- **DOC-REQ-005**: **Citation**: `Phase 6 - Tasks`. **Requirement**: Remove obsolete Data Transfer Objects (DTOs), frontend state handlers, and API endpoints mapped to terminal web sessions.
- **DOC-REQ-006**: **Citation**: `Phase 6 - Tasks`. **Requirement**: Update documentation covering managed agent logs to exclude terminal embeds, while preserving `xterm.js` references exclusively for interactive OAuth terminal flows.
- **DOC-REQ-007**: **Citation**: `Phase 6 - Tasks`. **Requirement**: Provide operator migration notes documenting the cut-off of legacy sessions.
- **DOC-REQ-008**: **Citation**: `Phase 6 - Tasks`. **Requirement**: Add regression tests that validate backward compatibility mapping for historical runs lacking separated output.

## Functional Requirements
1. DTOs referring to `TaskRunLiveSession` must be removed or marked `@deprecated` aggressively if API backwards-compatibility is strictly required temporarily (SATISFIES DOC-REQ-002, DOC-REQ-005).
2. The agent launcher explicitly must not start `tmate` processes as a log streaming mechanism (SATISFIES DOC-REQ-004).
3. The MoonMind API routing components handling `web_ro` terminal WebSocket requests must reject or be removed for managed runs (SATISFIES DOC-REQ-003).
4. The frontend UI must present a generic read-only archive view when encountering historical models without Phase 1+ standard artifacts (SATISFIES DOC-REQ-001).
5. Add rigorous Python and UI tests to prove legacy data still resolves to a viewer without crashing (SATISFIES DOC-REQ-008). 
6. System architecture docs must be corrected (SATISFIES DOC-REQ-006, DOC-REQ-007).

## User Scenarios & Testing
*Scenario 1: Viewing a legacy run*
Given an operator visits a task detail page for a run executed prior to Phase 1,
When the backend returns an old-style legacy payload lacking `stdout_artifact_ref`,
Then the UI seamlessly displays the historical transcript via the fallback UI layer without relying on the retired `xterm.js` component,
And the operator encounters no `tmate` WebSockets errors.

*Scenario 2: Agent launch*
Given a new managed agent is launched via Temporal,
When the environment spins up the subprocess,
Then the `tmate` socket infrastructure is strictly absent because it is no longer required for web logging.

## Success Criteria
- 0 references to `tmate` or `xterm.js` for observability paths.
- All historical runs render successfully under degraded artifacts viewing contexts.
- Codebase size reduced from deleted legacy API models and session routing paths.

## Assumptions
- The standard stdout/stderr streams implemented in Phase 1 serve as the universally acceptable replacement.
