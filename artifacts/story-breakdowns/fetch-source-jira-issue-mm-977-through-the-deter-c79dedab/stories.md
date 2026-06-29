# Story Breakdown: MM-977 MoonMind Chat Breakdown 2

Source: MM-977 trusted Jira issue text (no readable source document path)

Source document class: declarative-text

Extracted at: 2026-06-29T06:38:43+00:00

## Design Summary

The source describes a MoonMind-native chat session projection over existing managed-agent observability, history, artifacts, and SSE streams. It asks MoonMind to adapt Omnigent-style typed events, block reduction, and chat rendering while keeping AgentRun, RunObservabilityEvent, ArtifactRef, Temporal control paths, and Live Logs canonical. The work spans a frontend adapter/reducer/view/composer, feature-flagged session API facades, capability-gated controls, durable fallback behavior, security guardrails, and targeted frontend/backend/integration tests.

## Coverage Points

- `DESIGN-REQ-001` (requirement): Chat projection scope - Build a MoonMind-native chat session projection for session-capable managed agents.
- `DESIGN-REQ-002` (constraint): Borrow concepts only - Adapt Omnigent UI/reducer concepts without porting its runtime, tunnel, host lifecycle, or resource model.
- `DESIGN-REQ-003` (constraint): MoonMind events canonical - Derive chat events from MoonMind RunObservabilityEvent and artifacts, not provider-native browser payloads.
- `DESIGN-REQ-004` (state-model): Typed chat events - Define a typed ChatSessionEvent union for status, response lifecycle, messages, tools, approvals, boundaries, and failures.
- `DESIGN-REQ-005` (state-model): Display blocks with context - Define display-oriented ChatBlock types that carry compact run/session/response/item/turn/sequence context.
- `DESIGN-REQ-006` (requirement): Text delta accumulation - Group assistant deltas into assistant text blocks.
- `DESIGN-REQ-007` (requirement): Final message dedupe - Avoid duplicate assistant output when streamed deltas and final assistant messages both arrive.
- `DESIGN-REQ-008` (requirement): Tool pairing and dedupe - Pair tool calls and results by call id and dedupe repeated calls/results by response or turn identity.
- `DESIGN-REQ-009` (requirement): Lifecycle boundaries - Preserve response, turn, session reset, completion, and failure boundaries and close open blocks at boundaries.
- `DESIGN-REQ-010` (durability): Replay/live equivalence - Historical replay and live SSE append must converge on the same block sequence.
- `DESIGN-REQ-011` (requirement): Chat visual surface - Render user bubbles, assistant bubbles, tool cards, output cards, approval cards, status rows, and boundary banners.
- `DESIGN-REQ-012` (observability): Raw logs retained - Keep Live Logs as the raw chronological debug projection and provide an escape hatch from chat view.
- `DESIGN-REQ-013` (integration): History and stream lifecycle - Fetch summary/history, connect to live stream when supported, and use the same adapter/reducer for historical and live rows.
- `DESIGN-REQ-014` (durability): Durable refresh reconstruction - Ended runs and refreshed pages reconstruct from observability/events and artifacts without requiring a live stream.
- `DESIGN-REQ-015` (durability): Live fallback behavior - Live stream failures fall back to historical/durable data or merged logs without crashing the run view.
- `DESIGN-REQ-016` (requirement): Capability-gated composer - Show composer only for session-capable runtimes with explicit follow-up/control capabilities.
- `DESIGN-REQ-017` (integration): Temporal control path - Submit follow-ups, clear, and interrupts through MoonMind API/Temporal control routes, not raw terminal input.
- `DESIGN-REQ-018` (constraint): One-shot suppression - One-shot managed agents must not advertise message or follow-up capability.
- `DESIGN-REQ-019` (migration): Session API feature flag - Expose session-oriented aliases only behind MOONMIND_SESSION_API_COMPAT_ENABLED.
- `DESIGN-REQ-020` (integration): Core session aliases - Implement bounded facades for snapshot, items, stream, and events over existing AgentRun/session/artifact APIs.
- `DESIGN-REQ-021` (constraint): Unsupported events fail loudly - Unsupported session event types fail with clear errors rather than hidden fallbacks.
- `DESIGN-REQ-022` (constraint): Existing API stability - Existing /api/agent-runs endpoints continue to work.
- `DESIGN-REQ-023` (security): Security and redaction - Do not expose provider-native raw payloads directly; redact or drop secret-like metadata.
- `DESIGN-REQ-024` (security): Authorization boundaries - Control actions and session resource aliases must preserve existing capability gates and artifact authorization.
- `DESIGN-REQ-025` (observability): Frontend behavior tests - Unit tests cover adapter, reducer, UI rendering, composer, optimistic reconciliation, and unknown events.
- `DESIGN-REQ-026` (observability): Backend and integration tests - API alias tests and integration tests cover session mapping, controls, live/history rendering, refresh, follow-up, clear, and capability gating.

## Ordered Story Candidates

### STORY-001: Create MoonMind chat event and block projection core
Short name: `chat-projection-core`
Why: As a dashboard operator, I need MoonMind observability rows converted into stable chat-session events and display blocks so managed-session history can be rendered as a coherent conversation without depending on provider-native protocols.
Source reference: MM-977: MoonMind Chat Breakdown 2; sections: Summary, Proposed architecture, Phase 1: Event-to-chat adapter, Phase 2: Chat block reducer; coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-023
Independent test: Run focused frontend unit tests that feed representative RunObservabilityEvent histories and live appends into the adapter/reducer and assert the resulting ChatBlock sequence.
Acceptance criteria:
- ChatSessionEvent types are derived from RunObservabilityEvent rows and preserve compact debug metadata.
- ChatBlock types cover user messages, assistant text, tool calls, tool results, approvals, status, boundaries, response lifecycle, and errors.
- Assistant deltas accumulate into a single active assistant block and final assistant messages do not duplicate streamed text.
- Tool calls/results are paired by callId and duplicate calls/results are ignored using stable response or turn identity.
- Response completion/failure and session reset/clear events close open blocks and create visible lifecycle blocks.
- Unknown or partially populated observability events degrade to status/error blocks without crashing.
Requirements:
- Add MoonMind-named chat session event and block modules.
- Add fromObservability mapping from known RunObservabilityEvent.kind values to ChatSessionEvent values.
- Add reducer state for active response, active text, turn index, pending tools, dedupe keys, pending user messages, and sequence tracking.
- Keep provider-native raw payloads out of browser-facing chat events except redacted compact metadata.
Dependencies: None
Assumptions:
- Existing observability rows expose enough kind/payload metadata to form an initial lossy but useful chat projection.

### STORY-002: Render Chat Session View from durable history and live stream
Short name: `chat-session-view`
Why: As a dashboard operator, I need a Chat Session View that reconstructs managed-agent sessions from durable observability history and updates from live SSE so I can inspect session-capable agents as chat without losing raw diagnostics.
Source reference: MM-977: MoonMind Chat Breakdown 2; sections: Problem, Goals, Phase 3: Chat Session View component, Durability; coverage: DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-023
Independent test: Render the component with mocked summary/history/stream data and assert chat blocks, fallback states, refresh reconstruction, stream error handling, and raw timeline access.
Acceptance criteria:
- The dashboard can render a Chat Session View for a session-capable managed agent.
- User messages, assistant text, tool cards, tool results, approval cards, status rows, and boundary banners render with stable keys.
- Historical events and live stream rows pass through the same adapter/reducer.
- Refresh uses observability/events and artifact-backed data to reconstruct ended or active sessions.
- Live stream errors do not blank the view and fall back to durable history or merged logs.
- Live Logs remains accessible as the raw chronological debug projection.
Requirements:
- Add or integrate ChatSessionView in workflow detail/session surfaces.
- Reuse existing observability summary, events, logs stream, route templates, and feature-flag plumbing where practical.
- Provide explicit fallback state when structured chat projection is unavailable.
- Keep Live Logs, Diagnostics, and Artifacts available beside or behind the chat view.
Dependencies: STORY-001
Assumptions:
- A tab or mode toggle is acceptable for the first version as long as Live Logs is still reachable.
Needs clarification:
- [NEEDS CLARIFICATION] Decide whether Chat Session View ships as a new tab beside Live Logs or as a mode toggle inside Live Logs.

### STORY-003: Add capability-gated chat composer and session controls
Short name: `session-composer-controls`
Why: As an operator, I need follow-up and session controls inside the chat surface only when the runtime explicitly supports them, so I can continue or clear managed sessions without unsafe terminal injection or false interactivity for one-shot runs.
Source reference: MM-977: MoonMind Chat Breakdown 2; sections: Phase 4: Composer and controls, Security, API compatibility; coverage: DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-024, DESIGN-REQ-026
Independent test: Run frontend composer tests and backend/control-route tests with snapshots that allow and deny capabilities, then assert the correct payloads and disabled/read-only states.
Acceptance criteria:
- Composer appears only when session id and intervention capabilities allow follow-up on a non-terminal session.
- Composer is hidden or read-only for terminal runs, one-shot runtimes, and sessions without sendFollowUp capability.
- Follow-up submission uses an event-shaped message payload that is translated to the existing MoonMind control route.
- Clear and interrupt controls are capability-gated and fail clearly when unsupported.
- Optimistic user bubbles reconcile with confirmed user-message events without duplication.
Requirements:
- Add ChatSessionComposer or equivalent controlled input surface.
- Route all actions through MoonMind API/Temporal control endpoints.
- Use explicit capability fields rather than inferring interactivity from runtime names alone.
- Preserve authorization and policy checks for every control action.
Dependencies: STORY-001, STORY-002
Assumptions:
- Existing control route can accept or be adapted to event-shaped message data for the initial send_follow_up path.
Needs clarification:
- [NEEDS CLARIFICATION] Confirm the initial interrupt action name and capability source if interrupt_turn is not yet implemented.

### STORY-004: Expose feature-flagged core session API facades
Short name: `session-api-facades`
Why: As an API client or dashboard surface, I need session-oriented snapshot, items, stream, and event aliases that delegate to existing MoonMind AgentRun/session/artifact APIs so chat UI can use session naming without destabilizing current endpoints.
Source reference: MM-977: MoonMind Chat Breakdown 2; sections: API naming alignment with Omnigent, Recommended MoonMind compatibility aliases, Phase 5: Session-oriented API aliases; coverage: DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-024, DESIGN-REQ-026
Independent test: Run backend API tests with the compat flag enabled and disabled, asserting route availability, bounded response shapes, stream delegation, event translation, authorization, and unsupported event errors.
Acceptance criteria:
- Session aliases are disabled unless MOONMIND_SESSION_API_COMPAT_ENABLED is true.
- GET /api/sessions/{sessionId} returns a bounded session snapshot built from existing session, observability, and artifact data.
- GET /api/sessions/{sessionId}/items returns a durable item-oriented projection of session history.
- GET /api/sessions/{sessionId}/stream delegates to existing live log/observability stream information and preserves sequence/replay affordances.
- POST /api/sessions/{sessionId}/events supports at least message and clear_session where capabilities allow.
- Unsupported event types fail with clear errors and existing /api/agent-runs endpoints continue to work.
Requirements:
- Add thin facades rather than replacing AgentRun endpoints.
- Map session id to the owning agent run through existing managed-session records.
- Keep response bodies bounded and artifact-ref based.
- Preserve authorization checks and capability gates from underlying routes.
Dependencies: None
Assumptions:
- Feature-flagged aliases are acceptable despite the general preference for one canonical internal path because they are external/API facades over existing canonical contracts, not alternate internal identities.
Needs clarification:
- [NEEDS CLARIFICATION] Confirm whether the first stream alias should support only MoonMind format or also omnigent_compat names.

### STORY-005: Add approval and resource session projections with guardrails
Short name: `approvals-resources-guardrails`
Why: As an operator, I need approval prompts and session resources to appear as first-class chat/session projections while preserving MoonMind artifact authorization and control policies.
Source reference: MM-977: MoonMind Chat Breakdown 2; sections: Elicitation / approval resolution, Session resources, Security; coverage: DESIGN-REQ-011, DESIGN-REQ-017, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-026
Independent test: Run UI and backend tests that render approval requests, resolve an elicitation through the authorized control path, list session resources through artifact-backed projections, and reject unauthorized access.
Acceptance criteria:
- Approval requests render as distinct approval cards or rows in the chat view.
- Approval resolution uses MoonMind API/Temporal control behavior and preserves authorization.
- Session resource aliases, if implemented in this story, return read-only artifact projections and do not duplicate blob storage.
- Resource access does not bypass existing artifact authorization.
- Secret-like provider metadata is redacted or omitted from approval/resource displays.
Requirements:
- Represent approval_requested and approval_resolved as chat events/blocks.
- Add or prepare /api/sessions/{id}/elicitations/{elicitationId}/resolve behind the session compat flag when backend scope includes approvals.
- Add read-only session resource projection routes only as facades over ArtifactRef data.
- Keep uploads and full resource browser behavior out of the first version unless explicitly chosen later.
Dependencies: STORY-001, STORY-004
Assumptions:
- Approval/resource aliases can be delivered after the core snapshot/events/stream aliases if the first API story is kept narrower.
Needs clarification:
- [NEEDS CLARIFICATION] Decide whether session resources are read-only artifact projections only in the first release.

### STORY-006: Validate chat session behavior end to end for a managed runtime
Short name: `end-to-end-session-validation`
Why: As a MoonMind maintainer, I need an end-to-end validation path proving a session-capable managed agent emits history and live events that render as chat, preserve raw logs, accept follow-up controls, and reconstruct after refresh.
Source reference: MM-977: MoonMind Chat Breakdown 2; sections: Integration tests, Definition of done, Risks and mitigations; coverage: DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-025, DESIGN-REQ-026
Independent test: Run the targeted integration/e2e test that launches or simulates a session-capable managed agent, emits user/assistant/tool/approval/session events, refreshes the page, submits a follow-up, clears the session, and asserts Live Logs remains intact.
Acceptance criteria:
- A session-capable managed runtime produces user message, assistant delta, tool call, tool result, completion, and boundary events that render as grouped chat blocks.
- Historical replay and live append produce equivalent final blocks in the e2e scenario.
- Refreshing the page reconstructs the chat from durable observability/events and artifacts.
- Submitting a follow-up creates a user bubble and routes through MoonMind control APIs.
- Clearing the session creates a visible boundary banner.
- One-shot runtimes do not show follow-up controls.
- Live Logs continues to render for the same run.
Requirements:
- Add targeted frontend unit coverage for the changed modules.
- Add backend API alias tests when aliases are included.
- Add one integration or e2e validation that covers the operator-visible workflow across history, live stream, controls, and fallback.
- Use required MoonMind test runners and markers appropriate to the touched frontend/backend/integration surfaces.
Dependencies: STORY-001, STORY-002, STORY-003, STORY-004
Assumptions:
- A simulated managed-session event source is acceptable if launching a real runtime would require provider credentials in CI.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-001
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-001
- `DESIGN-REQ-007` -> STORY-001
- `DESIGN-REQ-008` -> STORY-001
- `DESIGN-REQ-009` -> STORY-001
- `DESIGN-REQ-010` -> STORY-001, STORY-006
- `DESIGN-REQ-011` -> STORY-002, STORY-005
- `DESIGN-REQ-012` -> STORY-002, STORY-006
- `DESIGN-REQ-013` -> STORY-002, STORY-006
- `DESIGN-REQ-014` -> STORY-002, STORY-006
- `DESIGN-REQ-015` -> STORY-002, STORY-006
- `DESIGN-REQ-016` -> STORY-003, STORY-006
- `DESIGN-REQ-017` -> STORY-003, STORY-005
- `DESIGN-REQ-018` -> STORY-003, STORY-006
- `DESIGN-REQ-019` -> STORY-004
- `DESIGN-REQ-020` -> STORY-004
- `DESIGN-REQ-021` -> STORY-004
- `DESIGN-REQ-022` -> STORY-004
- `DESIGN-REQ-023` -> STORY-001, STORY-002, STORY-005
- `DESIGN-REQ-024` -> STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-025` -> STORY-006
- `DESIGN-REQ-026` -> STORY-003, STORY-004, STORY-005, STORY-006

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: None
- `STORY-005` depends on: STORY-001, STORY-004
- `STORY-006` depends on: STORY-001, STORY-002, STORY-003, STORY-004

## Out Of Scope

- Porting Omnigent runner tunnel, session server, host lifecycle, full conversation store, session forking, or full resource browser.
- Replacing MoonMind AgentRun, Temporal lifecycle, artifacts, or observability contracts.
- Adding WebSocket transport.
- Making one-shot runtimes interactive.
- Exposing provider-native raw payloads directly to the browser.

## Coverage Gate

PASS - every major design point is owned by at least one story.
