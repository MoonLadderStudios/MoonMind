# Story Breakdown: MM-977 MoonMind Chat Breakdown 2

- Source: MM-977 trusted Jira issue description
- Source path: null
- Source document class: declarative-text
- Extracted at: 2026-06-29T08:02:27.643229Z
- Output mode: jira

## Design Summary

MM-977 asks MoonMind to add a native Chat Session View for session-capable managed agents by adapting Omnigent typed-stream, event vocabulary, and block reducer concepts onto MoonMind observability, history, artifacts, SSE logs, and Temporal-backed controls. The target renders user bubbles, assistant streaming, tool cards, outputs, approvals, status, and reset boundaries while preserving Live Logs, artifact-backed reconstruction, existing AgentRun APIs, and strict capability/security boundaries.

## Coverage Points

- DESIGN-REQ-001 (requirement): MoonMind-native chat projection - Render chat as a projection over MoonMind observability/history/SSE, not provider-native protocols.
- DESIGN-REQ-002 (constraint): Omnigent concepts without runtime port - Use Omnigent UI/reducer ideas without porting runner tunnel, host lifecycle, resource model, or conversation store.
- DESIGN-REQ-003 (integration): ChatSessionEvent adapter - Convert RunObservabilityEvent rows into typed chat-session events with graceful missing-metadata handling.
- DESIGN-REQ-004 (state-model): Display block model with context - Represent chat display blocks with run/session/response/item/turn/sequence context.
- DESIGN-REQ-005 (state-model): Reducer streaming semantics - Accumulate deltas, dedupe final text, pair tools, preserve boundaries, reconcile optimistic messages, and close lifecycle state.
- DESIGN-REQ-006 (requirement): Replay/live equivalence - Historical replay plus live append must match full ordered replay.
- DESIGN-REQ-007 (requirement): Chat Session View UI - Render user bubbles, assistant bubbles, tool cards, approvals, boundaries, and status rows.
- DESIGN-REQ-008 (constraint): Raw Live Logs preserved - Keep Live Logs as the raw chronological observability/debug projection.
- DESIGN-REQ-009 (artifact): Artifact-backed reconstruction - Refresh and ended-run views reconstruct from durable observability/events and artifacts.
- DESIGN-REQ-010 (observability): Live stream fallback - Stream errors fall back to durable history and do not fail the view.
- DESIGN-REQ-011 (security): Composer capability gating - Show follow-up controls only for explicit session capabilities.
- DESIGN-REQ-012 (integration): Temporal-backed controls - Follow-ups, clears, interrupts, and approvals go through MoonMind API/Temporal controls.
- DESIGN-REQ-013 (integration): Session API aliases - Add feature-flagged /api/sessions facades for snapshot, items, stream, events, elicitations, and resources.
- DESIGN-REQ-014 (constraint): AgentRun APIs remain stable - Do not rename or remove existing /api/agent-runs endpoints.
- DESIGN-REQ-015 (requirement): Unsupported events fail clearly - Unsupported session event types return clear errors.
- DESIGN-REQ-016 (constraint): One-shot runtimes not interactive - Do not advertise message/follow-up capability for one-shot runtimes.
- DESIGN-REQ-017 (security): Security and redaction - Do not expose raw provider payloads; redact or drop secret-like metadata; gate controls.
- DESIGN-REQ-018 (security): Artifact authorization for resources - Session resources remain authorized artifact projections without duplicate blob storage.
- DESIGN-REQ-019 (requirement): Clear/reset boundary display - Clear/reset events render obvious boundary banners.
- DESIGN-REQ-020 (observability): Unknown event fallback - Unknown events degrade to system/status rows without crashing.
- DESIGN-REQ-021 (requirement): Frontend unit tests - Cover adapter, reducer, view, composer, streaming, dedupe, tools, approvals, boundaries, optimistic reconciliation.
- DESIGN-REQ-022 (requirement): Backend API tests - Cover aliases, authorization, event mapping, unsupported errors, one-shot suppression, and resources.
- DESIGN-REQ-023 (requirement): Integration tests - Cover real or hermetic managed session chat, refresh, follow-up, clear, and Live Logs behavior.
- DESIGN-REQ-024 (constraint): Omnigent reference study - Clone/study Omnigent Apache-2.0 reference code during implementation without making it canonical runtime design.

## Story Candidates

### STORY-001: Define MoonMind chat event and block projection

- Short name: `chat-projection-core`
- Source reference: MM-977: MoonMind Chat Breakdown 2; sections: Summary, Relevant Omnigent code, Proposed architecture
- Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-020, DESIGN-REQ-024
- Dependencies: None

As a MoonMind operator, I need managed-session observability events projected into typed chat events and display blocks so session history can be rendered as chat without depending on provider-native protocols.

Independent test:
Unit-test the adapter and block type helpers with representative RunObservabilityEvent rows, unknown event kinds, missing optional metadata, and Omnigent-inspired lifecycle/tool/status concepts.

Acceptance criteria:
- MoonMind-named chatSession modules define ChatSessionEvent and ChatBlock unions derived from RunObservabilityEvent semantics.
- Adapter mapping covers user messages, assistant deltas/finals, tool calls/results, approvals, response lifecycle, session status, and reset/clear events.
- Every block carries agentRunId, sessionId, sessionEpoch, responseId, itemId, turnIndex, and sequence bounds when available.
- Unknown observability events produce system/status blocks without throwing.
- Omnigent reference concepts are documented or tested without importing its runtime/server model.

Requirements:
- Use MoonMind terminology rather than Omnigent-specific names.
- Preserve useful metadata for debugging while excluding provider-native raw payloads from display data.
- Keep mappings centralized for future response.* or session.* event names.

Assumptions:
- Existing observability rows contain enough kind/detail/sequence metadata for an initial projection.

### STORY-002: Reduce chat events into replay-safe session blocks

- Short name: `chat-block-reducer`
- Source reference: MM-977: MoonMind Chat Breakdown 2; sections: reduceChatSessionEvents, Reducer correctness, Acceptance criteria
- Coverage IDs: DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021
- Dependencies: STORY-001

As a MoonMind operator, I need historical and live session events reduced into one stable chat transcript so streamed assistant output, tool activity, approvals, and turn boundaries do not duplicate or drift after refresh.

Independent test:
Run reducer unit tests that replay historical events, append equivalent live events, and compare final blocks for text accumulation, final-message dedupe, tool/result pairing, approval lifecycle, clear/reset boundaries, and failure closure.

Acceptance criteria:
- Assistant deltas update one active assistant block and final assistant messages do not duplicate streamed text.
- Tool calls and results pair by callId when possible and dedupe by call id plus response/turn identity.
- Response completion closes active assistant/tool sections; response failure emits a useful error block.
- Session clear/reset emits visible boundary blocks and resets state that must not cross epochs.
- Historical replay followed by live append matches full ordered replay.

Requirements:
- Track active response id, active assistant block key, pending tools, seen calls/results, pending user messages, turn index, and last sequence.
- Support optimistic user message reconciliation.
- Handle missing or out-of-order optional metadata without crashing.

Assumptions:
- A deterministic sequence field is available or append order fallback is explicitly tested.

### STORY-003: Render Chat Session View with durable fallback

- Short name: `chat-session-view`
- Source reference: MM-977: MoonMind Chat Breakdown 2; sections: Add ChatSessionView, Goals, Durability, Acceptance criteria
- Coverage IDs: DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-023
- Dependencies: STORY-001, STORY-002

As a MoonMind operator, I need a dashboard session surface that renders managed-agent work as a chat-like transcript while preserving Live Logs and artifact-backed recovery after refresh or stream failure.

Independent test:
Render ChatSessionView with mocked summary/history/SSE inputs and assert bubbles, tool cards, approval cards, status rows, boundaries, raw timeline access, refresh reconstruction, and stream fallback.

Acceptance criteria:
- Dashboard displays Chat Session View for a session-capable managed agent using summary and events history.
- Active runs pass live stream rows through the same adapter and reducer as historical rows.
- Live Logs remains reachable as raw chronological/debug projection.
- Structured-event absence or live stream errors fall back to durable history, merged logs, or explicit fallback status.
- Ended runs render after refresh without EventSource.

Requirements:
- Reuse existing route plumbing and feature flags where possible.
- Render compact chat blocks for user, assistant, tool, approval, status, boundary, and error states.
- Keep diagnostics/artifacts context available.

Assumptions:
- Workflow detail UI can host the chat view without a larger navigation redesign.

Needs clarification:
- [NEEDS CLARIFICATION] Decide whether Chat Session View is a new tab beside Live Logs or a mode toggle inside Live Logs.

### STORY-004: Gate chat composer and session controls by capability

- Short name: `session-composer-controls`
- Source reference: MM-977: MoonMind Chat Breakdown 2; sections: ChatSessionComposer, Architecture constraints, Security, API compatibility
- Coverage IDs: DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-021
- Dependencies: STORY-001, STORY-002, STORY-003

As a MoonMind operator, I need follow-up, clear, interrupt, and approval controls only when the managed runtime explicitly supports them, and every action must flow through MoonMind API and Temporal control paths.

Independent test:
Unit-test ChatSessionComposer with capability permutations and mocked control submissions, including optimistic bubbles, backend acknowledgement, failure recovery, terminal/one-shot suppression, and no raw terminal injection.

Acceptance criteria:
- Composer appears only with sessionId, sendFollowUp capability, and a non-terminal/read-only state.
- Message submission uses an event-shaped payload mapped to send_follow_up through MoonMind APIs.
- Clear, interrupt, and approval actions appear only when advertised and submit through control endpoints.
- One-shot or terminal runtimes do not render interactive session affordances.
- Failed submissions show an error and do not leave duplicate optimistic bubbles after reconciliation.

Requirements:
- Capability checks fail closed.
- Control payloads do not expose provider-native raw payloads.
- Composer reconciles pending user messages with confirmed observability events.

Assumptions:
- Existing control endpoint supports send_follow_up and clear_session for at least one session-capable runtime.

Needs clarification:
- [NEEDS CLARIFICATION] Confirm whether approval resolution moves immediately to a dedicated elicitation route or remains behind existing controls for the first UI version.

### STORY-005: Add feature-flagged session API aliases

- Short name: `session-api-aliases`
- Source reference: MM-977: MoonMind Chat Breakdown 2; sections: API naming alignment, Recommended aliases, API compatibility, Security
- Coverage IDs: DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-022
- Dependencies: None

As an API client or dashboard developer, I need session-oriented MoonMind facades for snapshots, items, streams, events, approvals, and resources that align conceptually with Omnigent while preserving AgentRun, RunObservabilityEvent, ArtifactRef, and existing endpoints as canonical.

Independent test:
Run backend API tests with the session API feature flag enabled and disabled, asserting route availability, snapshot shape, item projection, stream delegation, control mapping, unsupported errors, one-shot suppression, and artifact authorization.

Acceptance criteria:
- MOONMIND_SESSION_API_COMPAT_ENABLED gates new /api/sessions routes without changing /api/agent-runs routes.
- GET /api/sessions/{sessionId} returns a bounded snapshot with identity, status, epoch, pending inputs, capabilities, and artifact refs when available.
- Items and stream aliases delegate to observability/events and logs/stream while preserving sequence/replay semantics.
- POST /api/sessions/{sessionId}/events supports message, interrupt where implemented, and clear_session where capabilities allow; unsupported types return clear errors.
- Elicitation and resource aliases preserve existing control and artifact authorization.

Requirements:
- Do not rename AgentRun, RunObservabilityEvent, ManagedSessionStore, ArtifactRef, or existing routes.
- Fail closed when session ID cannot map to an authorized agent run.
- Do not duplicate blob storage for session resources.

Assumptions:
- A reliable sessionId-to-agentRunId lookup exists or can be exposed without adding alias identity fields.

Needs clarification:
- [NEEDS CLARIFICATION] Decide whether /api/sessions/{id}/stream?format=omnigent_compat ships in the first alias story or remains format=moonmind only.

### STORY-006: Verify end-to-end managed session chat behavior

- Short name: `session-chat-integration`
- Source reference: MM-977: MoonMind Chat Breakdown 2; sections: Integration tests, Definition of done, Risks, Security
- Coverage IDs: DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-012, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-023
- Dependencies: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005

As a MoonMind maintainer, I need integration coverage proving the chat session surface, reducer, controls, durable reconstruction, raw logs, and API aliases work together for a real session-capable managed agent without weakening security or artifact-first semantics.

Independent test:
Launch a session-capable managed agent in the integration harness, emit a user message, assistant deltas, tool call, tool result, completion, follow-up, and clear event, then assert Live Logs, Chat Session View, API aliases, refresh reconstruction, and security gates.

Acceptance criteria:
- A managed-session run produces grouped chat blocks for user, assistant deltas, tool call/result, approval where applicable, completion, and status.
- Refreshing reconstructs the same meaningful transcript from observability/events and artifacts.
- Submitting a follow-up creates a user bubble and reaches the runtime through MoonMind API/Temporal controls.
- Clearing the session renders a boundary banner and preserves diagnostics/artifact evidence.
- Live Logs still renders raw chronological observability, and one-shot runtimes remain non-interactive.

Requirements:
- Cover raw-provider-payload exclusion or redaction at browser/API boundaries.
- Prefer hermetic integration infrastructure and avoid live provider credentials unless marked provider verification.
- Prove session resource checks still enforce artifact authorization.

Assumptions:
- A hermetic or mocked session integration path can produce representative observability without external credentials.

## Coverage Matrix

- DESIGN-REQ-001: STORY-001
- DESIGN-REQ-002: STORY-001
- DESIGN-REQ-003: STORY-001
- DESIGN-REQ-004: STORY-001
- DESIGN-REQ-005: STORY-002
- DESIGN-REQ-006: STORY-002
- DESIGN-REQ-007: STORY-003
- DESIGN-REQ-008: STORY-003, STORY-006
- DESIGN-REQ-009: STORY-003, STORY-006
- DESIGN-REQ-010: STORY-003, STORY-006
- DESIGN-REQ-011: STORY-004
- DESIGN-REQ-012: STORY-004, STORY-006
- DESIGN-REQ-013: STORY-005
- DESIGN-REQ-014: STORY-005
- DESIGN-REQ-015: STORY-005
- DESIGN-REQ-016: STORY-004, STORY-005, STORY-006
- DESIGN-REQ-017: STORY-004, STORY-005, STORY-006
- DESIGN-REQ-018: STORY-005, STORY-006
- DESIGN-REQ-019: STORY-002, STORY-003
- DESIGN-REQ-020: STORY-001, STORY-002, STORY-003
- DESIGN-REQ-021: STORY-002, STORY-003, STORY-004
- DESIGN-REQ-022: STORY-005
- DESIGN-REQ-023: STORY-003, STORY-006
- DESIGN-REQ-024: STORY-001

## Dependencies

- STORY-001: None
- STORY-002: STORY-001
- STORY-003: STORY-001, STORY-002
- STORY-004: STORY-001, STORY-002, STORY-003
- STORY-005: None
- STORY-006: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005

## Out of Scope

- Porting Omnigent runner tunnel, host management, full conversation store, session forking, or resource browser.
- Replacing MoonMind AgentRun / Temporal lifecycle, RunObservabilityEvent, artifacts, or existing /api/agent-runs endpoints.
- Adding WebSocket transport or exposing provider-native raw payloads directly to the browser.
- Making one-shot managed agents appear interactive.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Recommended First Story

STORY-001: Define MoonMind chat event and block projection. This unlocks reducer, UI, and composer work while keeping the first slice independently testable.

## Original Source Design

```text
MM-977: MoonMind Chat Breakdown 2

Build Omnigent-inspired chat session view for MoonMind managed-agent sessions

Type
Story / Feature

Summary
Implement a MoonMind-native Chat Session View for session-capable managed agents by adapting the useful presentation/reducer ideas from Omnigent's chat UI, while preserving MoonMind's artifact-first runtime model. Be sure to git clone https://github.com/omnigent-ai/omnigent as a concrete reference. It is open-source Apache 2.0 license, so it is fine to use as an explicit reference.

This issue follows earlier chat-like managed-agent observability and controls work. The prior issue focuses on emitting normalized MoonMind observability events and generic follow-up controls. This issue focuses on making the operator-facing UI feel much closer to Omnigent's chat session experience: user bubbles, assistant streaming, tool-call cards, tool-output cards, approval cards, turn boundaries, session status, and durable fallback.

This should not port Omnigent's full session server, runner tunnel, resource model, or host lifecycle. It should adapt the UI/reducer concepts that are useful on top of MoonMind's existing APIs.

Background
Omnigent's chat UI is built around GET /v1/sessions/{id}/stream -> parse SSE into typed StreamEvent values -> reduce StreamEvent values into display blocks -> render blocks as chat bubbles, tool cards, approvals, status chips, etc. Omnigent's ap-web/src/lib/sse.ts parses raw text/event-stream bytes from /v1/sessions/{id}/stream into typed StreamEvent values. events.ts defines response lifecycle, text deltas, reasoning, tool calls, tool results, elicitation requests, output files, compaction, interrupts, todos, usage, session status, child sessions, changed files, and presence. blockStream.ts reduces those events into UI blocks with reducer state for accumulated text, reasoning, pending tools, dedupe sets, agent name, turn number, and active response id.

MoonMind already has comparable lower-level substrate: GET /api/agent-runs/{id}/observability-summary, GET /api/agent-runs/{id}/observability/events, GET /api/agent-runs/{id}/logs/stream, and POST /api/agent-runs/{agentRunId}/artifact-sessions/{sessionId}/control. The current Live Logs panel fetches history, falls back to merged logs, opens an EventSource, appends parsed events, and updates session snapshot state. Therefore the desired implementation is: MoonMind observability/events + logs/stream -> MoonMind ChatStreamEvent adapter -> MoonMind ChatBlock reducer inspired by Omnigent BlockStream -> Chat Session View in dashboard.

Problem
MoonMind's current Live Logs timeline is useful for observability but does not feel like a first-class chat session UI. Operators should see user messages as user bubbles, assistant output as assistant bubbles, streaming assistant deltas without visual duplication, tool calls as tool cards, tool results as expandable output cards, approvals as first-class cards, turn/session boundaries as clear visual markers, follow-up controls in the same session surface, and artifact-backed reconstruction after refresh. The architecture must preserve that artifacts and bounded metadata remain durable truth, live streams are convenience not truth, UI consumes MoonMind-normalized events, control actions go through MoonMind API/Temporal rather than raw terminal input, and one-shot managed agents do not pretend to be interactive sessions.

Goals
Build a Chat Session View as a projection over MoonMind observability/event streams. Adapt Omnigent's event-to-block-to-render design pattern without porting the runtime model. Add a reducer that groups MoonMind observability events into chat-like display blocks. Support user bubbles, assistant bubbles, tool cards, approvals, turn boundaries, and session status rows. Keep Live Logs available as the raw chronological observability/debug projection. Add or prepare MoonMind session-oriented API aliases/naming closer to Omnigent's /v1/sessions contract. Keep compatibility with artifact-backed history and SSE fallback.

Non-goals
Do not port Omnigent's runner tunnel, host management, full /v1/conversations store, session forking, file/resource browser, WebSocket transport, or provider-native raw browser payloads. Do not replace MoonMind AgentRun / Temporal lifecycle or replace MoonMind artifacts with Omnigent session resources. Do not make one-shot runtimes interactive.

Proposed architecture
Add a MoonMind Chat Session projection layer: frontend/src/lib/chatSession/events.ts, blocks.ts, reducer.ts, render.tsx, frontend/src/components/ChatSessionView.tsx, and frontend/src/components/ChatSessionComposer.tsx. Define ChatSessionEvent derived from RunObservabilityEvent covering session status, response lifecycle, user messages, assistant deltas/final messages, tool calls/results, approvals, session clears, and failures. Define ChatBlock covering user message, assistant text, tool call, tool result, approval, session status, session boundary, error, response start, and response end with context for agentRunId, sessionId, sessionEpoch, responseId, itemId, turnIndex, and sequence bounds.

Implement reduceChatSessionEvents to group text deltas, avoid duplicate final assistant messages, pair tool calls/results by callId, preserve response/turn boundaries, maintain stable optimistic user keys, close open assistant/tool sections on response completion/failure, support replay from historical events and live append from SSE, and avoid depending on event arrival from a single transport. Add ChatSessionView that fetches observability-summary and observability/events, converts events, reduces blocks, connects to /logs/stream when active, passes live SSE rows through the same reducer, falls back to Live Logs / merged logs when structured events are missing, and preserves existing Live Logs. Add ChatSessionComposer only when capabilities allow and post event-shaped message controls through MoonMind's control endpoint.

API naming alignment
Add experimental feature-flagged aliases with MOONMIND_SESSION_API_COMPAT_ENABLED=true: GET /api/sessions/{sessionId}, GET /api/sessions/{sessionId}/items, GET /api/sessions/{sessionId}/stream, POST /api/sessions/{sessionId}/events, POST /api/sessions/{sessionId}/elicitations/{elicitationId}/resolve, and GET /api/sessions/{sessionId}/resources. These are thin facades over existing MoonMind APIs. Existing /api/agent-runs endpoints remain stable. Unsupported event types fail loudly. One-shot runtimes do not advertise follow-up capability.

Acceptance criteria and tests
The dashboard renders a Chat Session View for a session-capable managed agent. User messages render as user bubbles; assistant output renders as assistant bubbles; streaming deltas update active assistant blocks without duplicating final output; tool calls/results render and pair; approvals render; session reset/clear events render as boundary banners; unknown events degrade to system/status rows. Historical replay and live SSE append produce the same final block sequence. Ended runs reconstruct from observability/events after refresh. Live stream errors fall back to historical/durable data. Provider-native raw payloads are not exposed directly; secret-like metadata is redacted or dropped; controls are capability-gated; session resources do not bypass artifact authorization. Frontend, backend API, and integration tests cover adapter/reducer/view/composer behavior, aliases, authorization, one-shot suppression, follow-up, clear-session, and durable reconstruction.
```
