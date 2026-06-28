# Story Breakdown: MM-977 Chat Session View

- Source: MM-977: Build Omnigent-inspired chat session view for MoonMind managed-agent sessions
- Source path: null (trusted Jira issue description / workflow instruction text)
- Source document class: declarative-text
- Extracted at: 2026-06-28T11:11:52Z
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

The design calls for a MoonMind-native chat session projection over existing managed-agent observability, history, SSE logs, controls, and artifacts. It borrows Omnigent's event-to-block-to-render pattern for typed events, block reduction, and chat presentation while explicitly preserving MoonMind AgentRun, RunObservabilityEvent, Temporal control, and artifact-first durability boundaries. The user-facing outcome is a dashboard Chat Session View with bubbles, streaming assistant output, tool and approval cards, boundaries, status, follow-up controls, and durable fallback, plus feature-flagged session-oriented API facades.

## Coverage Points

- DESIGN-REQ-001 (requirement) Chat session projection over MoonMind observability: Build a MoonMind Chat Session View as a projection over observability events and live logs. Source: Goals / Proposed architecture. Owners: STORY-001, STORY-002.
- DESIGN-REQ-002 (integration) MoonMind-normalized event adapter: Convert RunObservabilityEvent rows to ChatSessionEvent values without exposing provider-native payloads. Source: ChatSessionEvent. Owners: STORY-001.
- DESIGN-REQ-003 (state-model) Display-oriented chat blocks with context: Represent user, assistant, tool, approval, status, boundary, and error blocks with session/run context. Source: ChatBlock. Owners: STORY-001.
- DESIGN-REQ-004 (requirement) Reducer groups assistant text without duplication: Accumulate streaming deltas and dedupe final assistant messages. Source: Reducer responsibilities. Owners: STORY-001.
- DESIGN-REQ-005 (requirement) Reducer pairs and dedupes tools: Pair tool calls/results by call id and dedupe by call/result identity. Source: Reducer responsibilities. Owners: STORY-001.
- DESIGN-REQ-006 (state-model) Reducer preserves turns and lifecycle boundaries: Track response/turn identity, close blocks on completion or failure, and render failures. Source: Reducer responsibilities. Owners: STORY-001.
- DESIGN-REQ-007 (requirement) Historical replay and live append converge: Replay from history and append from SSE should produce the same final block sequence. Source: Reducer correctness. Owners: STORY-001, STORY-002.
- DESIGN-REQ-008 (requirement) Dashboard chat rendering: Render user bubbles, assistant bubbles, tool cards, tool output, approvals, status rows, and boundaries. Source: Chat Session View. Owners: STORY-002.
- DESIGN-REQ-009 (observability) Raw Live Logs remain available: Keep Live Logs as the chronological debug projection and provide an escape hatch from chat view. Source: Goals / Risks. Owners: STORY-002.
- DESIGN-REQ-010 (artifact) Artifact-backed durable reconstruction: Ended runs and refreshed pages reconstruct from observability/events and artifacts without requiring a live stream. Source: Durability. Owners: STORY-002, STORY-005.
- DESIGN-REQ-011 (security) Session composer capability gating: Show composer only for session-capable runtimes and allowed intervention capabilities. Source: ChatSessionComposer. Owners: STORY-003.
- DESIGN-REQ-012 (integration) Control actions through MoonMind API and Temporal: Follow-up, clear, interrupt, and approval actions go through MoonMind APIs, not terminal injection. Source: Composer and controls. Owners: STORY-003.
- DESIGN-REQ-013 (requirement) Optimistic message reconciliation: Support stable optimistic user bubbles and reconcile them when confirmed by durable events. Source: Omnigent ChatPage concepts / Reducer. Owners: STORY-001, STORY-003.
- DESIGN-REQ-014 (integration) Feature-flagged session API facades: Add /api/sessions aliases behind MOONMIND_SESSION_API_COMPAT_ENABLED without removing /api/agent-runs endpoints. Source: API naming alignment. Owners: STORY-004.
- DESIGN-REQ-015 (artifact) Bounded session snapshot alias: GET /api/sessions/{sessionId} returns bounded status, identity, capabilities, and artifact refs. Source: Session snapshot. Owners: STORY-004.
- DESIGN-REQ-016 (integration) Session events alias: POST /api/sessions/{sessionId}/events maps message, interrupt, clear_session, and unsupported types correctly. Source: Session event posting. Owners: STORY-004.
- DESIGN-REQ-017 (integration) Session stream alias: GET /api/sessions/{sessionId}/stream exposes existing live information, including sequence/replay affordances and optional formats. Source: Session stream. Owners: STORY-004.
- DESIGN-REQ-018 (artifact) Session items history alias: GET /api/sessions/{sessionId}/items exposes durable item-oriented history derived from observability and artifact refs. Source: Session items. Owners: STORY-004.
- DESIGN-REQ-019 (integration) Approval resolution alias: Resolve approval prompts through a dedicated session elicitation endpoint or compatible facade. Source: Elicitation / approval resolution. Owners: STORY-003, STORY-004.
- DESIGN-REQ-020 (artifact) Session resources as artifact projection: Expose read-oriented session resource aliases over authorized MoonMind artifacts without duplicating blob storage. Source: Session resources. Owners: STORY-005.
- DESIGN-REQ-021 (security) Security and authorization boundaries: Redact/drop secret-like metadata, enforce artifact authorization, and suppress one-shot runtime capabilities. Source: Security. Owners: STORY-002, STORY-003, STORY-004, STORY-005.
- DESIGN-REQ-022 (non-goal) Do not port Omnigent runtime model: Borrow UI/reducer concepts only; do not port runner tunnel, host lifecycle, conversations store, WebSockets, forking, or provider-native browser payloads. Source: Non-goals. Owners: STORY-001, STORY-004, STORY-005.
- DESIGN-REQ-023 (constraint) Omnigent reference study: Use Omnigent's Apache-2.0 code as concrete reference for concepts while preserving MoonMind naming and architecture. Source: Relevant Omnigent code. Owners: STORY-001.
- DESIGN-REQ-024 (requirement) Tests cover frontend, API, and integration behavior: Add reducer, adapter, UI, composer, backend alias, capability, and integration coverage. Source: Test plan. Owners: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005.

## Ordered Stories

### STORY-001: Create MoonMind chat event adapter and reducer

- Short name: chat-projection-reducer
- Source reference: MM-977 trusted Jira issue description; path null; claimIds []
- Sections: Proposed architecture, ChatSessionEvent, ChatBlock, Reducer responsibilities, Reducer correctness
- Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-013, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024
- Dependencies: none
- Independent test: Run frontend unit tests for fromObservability and reducer fixtures that replay historical events and then append equivalent live events, asserting identical ChatBlock sequences.

Description:
As a MoonMind operator, I need managed-session observability events reduced into stable chat blocks so history and live streams can power one coherent chat projection.

Acceptance criteria:
- RunObservabilityEvent rows map to ChatSessionEvent values for user messages, response starts, assistant deltas, final assistant messages, tool calls/results, approvals, session clear/reset, runtime status, completion, and failure.
- Unknown or incomplete observability rows become system/status blocks or are safely downgraded without crashing.
- Assistant deltas accumulate into a single active assistant text block and a final assistant message does not duplicate already streamed text.
- Tool calls and results pair by callId when possible and duplicate call/result rows are ignored using stable response/turn-aware keys.
- Response completion and failure close open assistant/tool sections and failures emit useful error blocks.
- Reducer state supports replay from history and live append from SSE without requiring one transport to be authoritative.
- Optimistic user message keys can be preserved and reconciled when durable user_message events arrive.
- Implementation uses MoonMind-specific names and types and does not import Omnigent runtime/server assumptions.

Requirements:
- Add a frontend chatSession projection layer for events, blocks, reducer, and observability mapping.
- Every ChatBlock carries agentRunId context and optional sessionId, sessionEpoch, responseId, itemId, turnIndex, sequenceStart, and sequenceEnd context where available.
- Reducer fixture coverage must include text delta accumulation, final-message dedupe, tool pairing, approval events, boundaries, unknown events, optimistic reconciliation, and history-plus-live convergence.

Assumptions:
- Existing RunObservabilityEvent rows contain enough kind/payload metadata to support an initial best-effort mapping.

### STORY-002: Render dashboard Chat Session View with raw timeline fallback

- Short name: chat-session-view
- Source reference: MM-977 trusted Jira issue description; path null; claimIds []
- Sections: Problem, Goals, Chat Session View, Acceptance criteria, Durability
- Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-021, DESIGN-REQ-024
- Dependencies: STORY-001
- Independent test: Render ChatSessionView with mocked summary, history, and live SSE inputs and verify chat blocks, fallback messaging, raw timeline access, and refresh reconstruction behavior.

Description:
As a MoonMind operator, I need a chat-like session view beside the raw timeline so session-capable managed-agent runs are readable as turns, messages, tools, approvals, status, and boundaries while diagnostics remain accessible.

Acceptance criteria:
- The workflow detail/session surface can render user messages as user bubbles and assistant output as assistant bubbles.
- Tool calls render as distinct tool cards and tool outputs render as attached or expandable output cards when paired.
- Approval requests render as first-class cards or rows and resolved approvals update visibly.
- Session reset/clear events render as obvious boundary banners and runtime/session status renders as status rows or chips.
- The view fetches observability summary and events, connects to logs/stream for active sessions, and sends all historical and live rows through the same reducer path.
- When structured chat projection is unavailable or live stream drops, the UI falls back to durable history, merged logs, or the existing Live Logs panel without failing the page.
- Live Logs, diagnostics, and artifact views remain available as raw chronological/debug projections.

Requirements:
- Add ChatSessionView or equivalent dashboard integration using existing route plumbing where possible.
- Expose a clear raw timeline escape hatch from the chat surface.
- Do not expose provider-native raw payloads directly in rendered chat blocks.

Assumptions:
- The dashboard already has reusable fetch/SSE plumbing from the Live Logs panel.

Needs clarification:
- [NEEDS CLARIFICATION] Whether the initial UX should be a new tab beside Live Logs or a mode toggle inside the Live Logs area.

### STORY-003: Add capability-gated session composer and controls

- Short name: session-composer-controls
- Source reference: MM-977 trusted Jira issue description; path null; claimIds []
- Sections: ChatSessionComposer, Composer and controls, Security, Acceptance criteria
- Coverage IDs: DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-019, DESIGN-REQ-021, DESIGN-REQ-024
- Dependencies: STORY-001, STORY-002
- Independent test: Render the composer against capability combinations and mock control submissions, asserting hidden/read-only behavior for one-shot or terminal runs and correct MoonMind API payloads for supported sessions.

Description:
As a MoonMind operator, I need follow-up, clear, interrupt, and approval actions in the same session surface only when the managed runtime explicitly supports them.

Acceptance criteria:
- The composer is shown only when a session id is present, the run is not terminal, and explicit intervention capabilities allow follow-up messages.
- One-shot managed runs and runtimes without session controllers do not advertise or render interactive message/follow-up capability.
- Submitting a message calls MoonMind's control/API surface and Temporal update path, not raw terminal stdin or provider-native endpoints.
- The UI can insert an optimistic user bubble with a stable key and reconcile or clear it after durable confirmation/failure.
- Clear session and interrupt controls are gated by capabilities and unsupported actions fail loudly with clear user-visible errors.
- Approval resolution uses the existing control route initially or the dedicated elicitation facade when available, while preserving approval card state.

Requirements:
- Add ChatSessionComposer or equivalent controls component for session-capable managed agents.
- Use an event-shaped internal payload for messages where practical while translating to existing send_follow_up/control actions until API aliases are available.
- Capability and terminal-state tests must cover hidden, disabled, success, and unsupported-action paths.

Assumptions:
- Existing control route supports send_follow_up and clear_session for at least one session-capable runtime.

Needs clarification:
- [NEEDS CLARIFICATION] Whether optimistic user bubbles should appear before backend acknowledgement in the first implementation.

### STORY-004: Expose feature-flagged session API snapshot, items, stream, events, and elicitation aliases

- Short name: session-api-facades
- Source reference: MM-977 trusted Jira issue description; path null; claimIds []
- Sections: API naming alignment with Omnigent, Recommended MoonMind compatibility aliases, API compatibility
- Coverage IDs: DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-024
- Dependencies: none
- Independent test: Run backend API tests with the session compat feature flag enabled and disabled, asserting facade responses, action translation, authorization, unsupported-type errors, and unchanged /api/agent-runs behavior.

Description:
As a MoonMind API client or dashboard surface, I need session-oriented API facades that align with chat-session semantics while preserving AgentRun, artifacts, authorization, and existing endpoint stability.

Acceptance criteria:
- Session API aliases are disabled unless MOONMIND_SESSION_API_COMPAT_ENABLED is enabled or the configured feature flag is active.
- Existing /api/agent-runs/... summary, events, stream, and control endpoints continue to work unchanged.
- GET /api/sessions/{sessionId} returns a bounded snapshot with id, agentRunId, workflowId/status where available, sessionEpoch, intervention capabilities, and artifact refs.
- GET /api/sessions/{sessionId}/items returns a durable item-oriented history derived from observability events and artifact refs.
- GET /api/sessions/{sessionId}/stream returns the same live information as the existing logs stream and preserves MoonMind sequence/replay affordances; optional format handling fails clearly for unsupported formats.
- POST /api/sessions/{sessionId}/events supports at least message, interrupt, and clear_session where capabilities allow and rejects unsupported event types with clear errors.
- POST /api/sessions/{sessionId}/elicitations/{elicitationId}/resolve maps approval resolution through the existing trusted MoonMind control path or a dedicated backend handler.

Requirements:
- Implement thin backend facades over existing AgentRun/session artifact APIs rather than replacing canonical contracts.
- Session id to agent run mapping must be deterministic and authorization-checked.
- Unsupported event types, terminal runs, and one-shot runtimes must fail loudly or suppress capabilities rather than silently falling back.

Assumptions:
- A managed session store or existing observability metadata can resolve sessionId to agentRunId without introducing identity aliases.

Needs clarification:
- [NEEDS CLARIFICATION] Whether omnigent_compat stream format should ship in the first facade story or be deferred behind a clearly unsupported format error.

### STORY-005: Project session resources from authorized artifacts

- Short name: session-resource-projection
- Source reference: MM-977 trusted Jira issue description; path null; claimIds []
- Sections: Session resources, Durability, Security, Non-goals
- Coverage IDs: DESIGN-REQ-010, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-024
- Dependencies: STORY-004
- Independent test: Run backend and UI/resource projection tests that list session resources for authorized artifacts, deny unauthorized artifacts, and verify ended sessions still show resource evidence after refresh.

Description:
As a MoonMind operator, I need session-scoped resource views over artifacts so files and evidence associated with a chat session are discoverable without duplicating storage or bypassing artifact authorization.

Acceptance criteria:
- GET /api/sessions/{sessionId}/resources and initial file/content aliases return session-scoped projections over MoonMind ArtifactRefs rather than new blob storage.
- Artifact authorization is enforced for every resource listing and content/download path.
- Secret-like metadata is redacted or dropped from resource summaries and rendered cards.
- Ended sessions can still show resource/evidence references from durable artifacts without a live stream.
- The implementation does not add a full file/resource browser or upload flow in the first version unless explicitly scoped later.
- Chat Session View can link to or summarize resource/output-file evidence without hiding the existing artifact views.

Requirements:
- Represent session resources as read-oriented projections over existing ArtifactRefs.
- Keep artifact storage authoritative and avoid duplicating blobs or replacing diagnostics/artifact views.
- Tests must cover authorization boundaries, redaction, and terminal/refresh behavior.

Assumptions:
- Existing artifact metadata is sufficient to identify session-scoped resources or can be joined through run/session artifact refs.

Needs clarification:
- [NEEDS CLARIFICATION] Whether session resources should remain read-only in the first version or support uploads later.

## Coverage Matrix

- DESIGN-REQ-001: STORY-001, STORY-002
- DESIGN-REQ-002: STORY-001
- DESIGN-REQ-003: STORY-001
- DESIGN-REQ-004: STORY-001
- DESIGN-REQ-005: STORY-001
- DESIGN-REQ-006: STORY-001
- DESIGN-REQ-007: STORY-001, STORY-002
- DESIGN-REQ-008: STORY-002
- DESIGN-REQ-009: STORY-002
- DESIGN-REQ-010: STORY-002, STORY-005
- DESIGN-REQ-011: STORY-003
- DESIGN-REQ-012: STORY-003
- DESIGN-REQ-013: STORY-001, STORY-003
- DESIGN-REQ-014: STORY-004
- DESIGN-REQ-015: STORY-004
- DESIGN-REQ-016: STORY-004
- DESIGN-REQ-017: STORY-004
- DESIGN-REQ-018: STORY-004
- DESIGN-REQ-019: STORY-003, STORY-004
- DESIGN-REQ-020: STORY-005
- DESIGN-REQ-021: STORY-002, STORY-003, STORY-004, STORY-005
- DESIGN-REQ-022: STORY-001, STORY-004, STORY-005
- DESIGN-REQ-023: STORY-001
- DESIGN-REQ-024: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005

## Out Of Scope

- Porting Omnigent's runner tunnel, host management, full conversations store, session forking, WebSocket transport, or provider-native browser protocol.
- Replacing AgentRun, RunObservabilityEvent, Temporal execution lifecycle, ArtifactRef, existing /api/agent-runs endpoints, Live Logs, Diagnostics, or Artifacts.
- Making one-shot managed runs appear interactive or bypassing MoonMind API/Temporal for control actions.
- Adding a full file/resource browser or upload flow in the first version.

## Recommended First Story

STORY-001: Create MoonMind chat event adapter and reducer. This is the lowest dependency contract/story that unlocks both UI rendering and deterministic tests.

## Verification Notes

- No spec.md files were created by this breakdown.
- No specs/ directories were created by this breakdown.
- TDD remains the default strategy for downstream /speckit.plan, /speckit.tasks, and /speckit.implement.
- After implementation, run /speckit.verify against the preserved original design text.
