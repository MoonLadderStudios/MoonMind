# Story Breakdown: MM-976

Source: MM-976: Implement chat-like managed-agent observability and controls in MoonMind dashboard

Source path: null (trusted Jira issue/workflow instructions; no resolved canonical document path)

Source document class: declarative-text

Story extraction date: 2026-06-28T06:31:21Z

## Design Summary

MM-976 asks MoonMind to turn existing Live Logs and managed-session controls into a clearer chat-like operator timeline using SSE, artifact-backed observability history, RunObservabilityEvent, RuntimeLogStreamer, and HTTP/Temporal control actions. It excludes WebSocket, full Omnigent session-server parity, provider-native browser contracts, terminal scrollback durability, and interactive controls for one-shot runtimes.

## Coverage Points

- DESIGN-REQ-001 (integration): Reuse existing SSE/EventSource live transport; do not introduce WebSocket.
- DESIGN-REQ-002 (durability): Fetch structured history first and preserve artifact/merged-log fallback for refresh, ended runs, and stream failure.
- DESIGN-REQ-003 (public-contract): Define MoonMind-owned chat/session event vocabulary for turns, assistant output, tools, approvals, sessions, and status.
- DESIGN-REQ-004 (public-contract): Normalize chat-like events into RunObservabilityEvent with run, sequence, stream, kind, text, identity, and compact metadata.
- DESIGN-REQ-005 (state-model): Maintain a monotonic run-global event sequence.
- DESIGN-REQ-006 (security): Keep browser payloads normalized, compact, and free of secrets, full transcripts, large blobs, and raw provider-native event bodies.
- DESIGN-REQ-007 (integration): Add a thin ManagedSessionObservabilityBridge that delegates to RuntimeLogStreamer.emit_observability_event.
- DESIGN-REQ-008 (integration): Map runtime-native observations in managed adapters, initially codex_cli managed session.
- DESIGN-REQ-009 (observability): Codex managed sessions emit lifecycle, user, assistant/stdout, tool where visible, reset, completion, and failure events.
- DESIGN-REQ-010 (artifact): Persist stdout/stderr, diagnostics, and structured observability.events.jsonl for durable reconstruction.
- DESIGN-REQ-011 (resilience): Live stream/spool publishing failure must not fail runtime execution or artifact publication.
- DESIGN-REQ-012 (ui): Project standardized events into distinct Live Logs timeline rows, chips, cards, and reset banners.
- DESIGN-REQ-013 (compatibility): Existing stdout/stderr, diagnostics panels, merged-log fallback, and historical run display continue to work.
- DESIGN-REQ-014 (api): observability-summary exposes sessionSnapshot and interventionCapabilities derived from runtime/session records.
- DESIGN-REQ-015 (control): Follow-up, clear/reset, interrupt, and cancel controls are gated by capabilities, session identity, run terminal state, and active turn state.
- DESIGN-REQ-016 (integration): Control actions use HTTP POST and MoonMind/Temporal runtime adapters, not WebSocket or arbitrary terminal input.
- DESIGN-REQ-017 (constraint): One-shot managed agents may show logs/markers but must not appear follow-up capable.
- DESIGN-REQ-018 (observability): Successful control actions create visible timeline evidence and failed controls surface operator-readable errors.
- DESIGN-REQ-019 (non-goal): Use Omnigent as a reference without porting its full session-server, tunnel, resource, terminal, fork, list, or sub-agent product architecture.
- DESIGN-REQ-020 (test): Cover backend bridge, runtime Codex emission, API history/SSE/control behavior, frontend rendering, fallback, and guardrails.

## Story Candidates

### STORY-001: Standardize managed-session observability events

Short name: session-event-contract

Source reference: MM-976 trusted Jira/workflow instructions; path null; claim IDs none; coverage IDs DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007.

As a MoonMind operator, I need managed-session activity represented by stable MoonMind observability events so dashboard and artifact consumers can interpret turns, messages, tools, approvals, status, and session boundaries without provider-native contracts.

Independent test: Unit tests instantiate the bridge with a fake RuntimeLogStreamer, emit each event family, and assert RunObservabilityEvent-compatible payloads with stable kind, stream, run ID, sequence participation, text, optional session identity, and compact sanitized metadata.

Acceptance criteria:
- Shared constants or typed literals define the standardized event vocabulary.
- ManagedSessionObservabilityBridge exposes stable helpers and delegates to RuntimeLogStreamer.emit_observability_event.
- Events validate with run ID, stream, kind, text, and optional session identity fields.
- Provider-native names are metadata only and never required by browser consumers.
- Secret-like or oversized metadata is rejected or redacted according to existing policy.

Requirements:
- Define event kinds for turn, assistant, tool, approval/intervention, session lifecycle, runtime/model status, and system annotation.
- Place source loading/emission at runtime/activity boundaries, not workflow payloads.
- Keep provider-specific translation outside the bridge.
- Support missing optional session fields without validation failure.

Dependencies: None

Assumptions:
- RuntimeLogStreamer remains responsible for persistence/live publication and sequence allocation.

### STORY-002: Emit Codex managed-session timeline events

Short name: codex-session-events

Source reference: MM-976 trusted Jira/workflow instructions; path null; claim IDs none; coverage IDs DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-020.

As an operator watching a Codex managed session, I need turn lifecycle, user submission, assistant output, tool/status markers where reliable, completion/failure, and reset boundaries emitted as normalized observability events while existing stdout/stderr artifacts remain intact.

Independent test: Runtime tests drive Codex managed-session launch, follow-up, assistant output/stdout, clear/reset, success, and failure paths and assert expected normalized events persist in sequence without breaking stdout/stderr/diagnostics artifacts.

Acceptance criteria:
- Codex emits session_started or session_resumed when session identity is available.
- Submitted turns emit user_message_submitted and turn_started when the boundary is observable.
- Assistant output emits assistant_message_delta/assistant_message where available or remains visible through stdout/stderr rows.
- Tool, approval, runtime_status, and model_status events are emitted only when reliable markers exist.
- Clear/reset emits session_cleared and session_reset_boundary.
- Spool/live publication failures do not fail runtime execution or suppress artifacts.

Requirements:
- Wire the bridge into the Codex managed-session adapter.
- Translate Codex-native observations to stable event kinds at the adapter boundary.
- Retain stdout/stderr and diagnostics artifact production.
- Persist events into the structured history/event journal.

Dependencies: STORY-001

Assumptions:
- Codex exposes enough session/turn boundaries for the initial lifecycle markers.

Needs clarification:
- Which Codex-visible markers are reliable enough for first-version tool_call_* classification?

### STORY-003: Serve durable chat timeline history over existing observability APIs

Short name: durable-timeline-history

Source reference: MM-976 trusted Jira/workflow instructions; path null; claim IDs none; coverage IDs DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-013, DESIGN-REQ-020.

As an operator refreshing or reopening a run, I need the same chat-like timeline reconstructed from durable observability history and artifacts, with SSE used only as live convenience for active runs.

Independent test: API tests create structured events, merged logs, terminal and active states, then assert observability-summary, observability/events, and /logs/stream expose ordered normalized events while terminal runs avoid live streaming and fallback data remains available.

Acceptance criteria:
- observability/events returns structured chat/session events in run-global sequence order.
- /logs/stream emits the new event kinds over existing SSE without schema errors.
- Terminal runs do not advertise or open live streams.
- Merged/stdout/stderr fallback still works when structured history is unavailable.
- Page refresh reconstructs visible timeline rows without a live browser having been connected.

Requirements:
- Keep EventSource/SSE as the live read transport.
- Keep live stream non-authoritative relative to artifacts/history.
- Expose durable stdout, stderr, diagnostics, and observability events.
- Preserve legacy API behavior for historical viewers.

Dependencies: STORY-001

Assumptions:
- Existing observability APIs can carry additional event kinds without a new endpoint.

### STORY-004: Render Live Logs as a chat-like managed-agent timeline

Short name: chat-timeline-ui

Source reference: MM-976 trusted Jira/workflow instructions; path null; claim IDs none; coverage IDs DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-020.

As an operator viewing Live Logs, I need user messages, assistant output, tool calls, approvals, turn boundaries, failures, and session resets visually distinct while preserving the current logs, diagnostics, and fallback experience.

Independent test: Frontend tests feed representative normalized events into classifyTimelineRow and the renderer and assert distinct treatments for user, assistant, tool, approval, reset boundary, failure, stdout/stderr, SSE error fallback, and merged-log fallback.

Acceptance criteria:
- User-message events render as user-turn headers or bubbles.
- Assistant events render as grouped assistant output rows or bubbles.
- Tool events render as chips or collapsible/nested tool sections.
- Approval/intervention events render as highlighted operator-attention rows.
- Session reset boundaries are obvious and turn completion/failure markers are visible.
- Existing stdout/stderr/diagnostics and legacy merged-log fallback paths continue to render.

Requirements:
- Extend timeline row classification for standardized event kinds.
- Implement row treatments as a projection over existing timeline data.
- Keep SSE lifecycle/fallback behavior unchanged.
- Avoid UI copy implying one-shot runtimes can receive follow-ups.

Dependencies: STORY-001, STORY-003

Assumptions:
- TimelineRow can be extended without replacing Live Logs.

Needs clarification:
- Should the chat-like projection be the default when event kinds are present or a mode toggle?

### STORY-005: Gate managed-session controls by runtime capabilities

Short name: capability-gated-controls

Source reference: MM-976 trusted Jira/workflow instructions; path null; claim IDs none; coverage IDs DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-020.

As an operator, I need follow-up, clear/reset, interrupt, and cancel controls to appear and execute only when the managed runtime session explicitly supports them, so one-shot runs are never presented as interactive sessions.

Independent test: Backend and frontend tests exercise session-capable, one-shot, active-turn, terminal, and unsupported-action runs; controls render only from capabilities, posts route through the artifact-session endpoint, unsupported actions are rejected, and outcomes produce visible evidence/errors.

Acceptance criteria:
- observability-summary includes bounded sessionSnapshot and interventionCapabilities from managed-session/runtime records.
- Frontend no longer derives follow-up visibility or session IDs from Codex runtime-name guesses.
- send_follow_up appears only with session ID, sendFollowUp true, and non-terminal run state.
- clear_session, interrupt_turn, and cancel are independently capability-gated.
- Control actions route through MoonMind/Temporal runtime adapters, not raw terminal input or WebSocket.
- Unsupported actions and false capabilities produce operator-readable errors.

Requirements:
- Extend managed-session schema/API summary with capability metadata.
- Validate capabilities in the control endpoint as well as the frontend.
- Preserve Codex send_follow_up and clear_session through the generalized path.
- Emit durable control/reset timeline evidence for successful actions and readable errors for failures.

Dependencies: STORY-001, STORY-003

Assumptions:
- Runtime adapter/session records can authoritatively describe intervention capabilities.

Needs clarification:
- Should interrupt_turn ship immediately or only once a concrete runtime adapter supports it?

### STORY-006: Enforce observability and control safety guardrails

Short name: chat-observability-guardrails

Source reference: MM-976 trusted Jira/workflow instructions; path null; claim IDs none; coverage IDs DESIGN-REQ-006, DESIGN-REQ-011, DESIGN-REQ-017, DESIGN-REQ-019, DESIGN-REQ-020.

As a MoonMind maintainer, I need explicit guardrails and regression tests so the chat-like experience does not leak provider-native payloads or secrets, depend on non-durable state, or accidentally port out-of-scope Omnigent product architecture.

Independent test: Guardrail tests pass secret-like metadata, oversized provider-native payloads, terminal-scrollback-only state, unsupported one-shot controls, and disabled live publication; expected results are redaction/rejection, artifact preservation, hidden controls, and no WebSocket/session-server dependency.

Acceptance criteria:
- Browser-facing payloads remain MoonMind-normalized and do not require provider-native bodies.
- Secrets, raw env, OAuth tokens, API keys, and full unredacted transcripts are not emitted.
- No behavior depends on terminal scrollback, runtime home directories, or container-local thread databases as durable truth.
- No WebSocket, Omnigent session server, runner tunnel, terminal attachment, resource browser, forks, sub-agent trees, or session-list scope is introduced.
- Control actions are capability-gated and never write arbitrary terminal input without a safe runtime-specific adapter.

Requirements:
- Encode security and non-goal guardrails in contract tests or nearby documentation.
- Add regression coverage for metadata sanitization and provider-native payload exclusion.
- Prove live stream failure is non-fatal and artifacts persist.
- Prove one-shot runtimes do not advertise follow-up capabilities.

Dependencies: STORY-001, STORY-005

Assumptions:
- Omnigent remains a reference only; MoonMind's artifact-first model remains authoritative.

## Coverage Matrix

- DESIGN-REQ-001: STORY-003, STORY-004
- DESIGN-REQ-002: STORY-003, STORY-004
- DESIGN-REQ-003: STORY-001
- DESIGN-REQ-004: STORY-001
- DESIGN-REQ-005: STORY-001
- DESIGN-REQ-006: STORY-001, STORY-006
- DESIGN-REQ-007: STORY-001
- DESIGN-REQ-008: STORY-002
- DESIGN-REQ-009: STORY-002
- DESIGN-REQ-010: STORY-002, STORY-003
- DESIGN-REQ-011: STORY-002, STORY-003, STORY-006
- DESIGN-REQ-012: STORY-004
- DESIGN-REQ-013: STORY-003, STORY-004
- DESIGN-REQ-014: STORY-005
- DESIGN-REQ-015: STORY-005
- DESIGN-REQ-016: STORY-005
- DESIGN-REQ-017: STORY-005, STORY-006
- DESIGN-REQ-018: STORY-005
- DESIGN-REQ-019: STORY-006
- DESIGN-REQ-020: STORY-002, STORY-003, STORY-004, STORY-005, STORY-006

## Dependencies

- STORY-001: None
- STORY-002: STORY-001
- STORY-003: STORY-001
- STORY-004: STORY-001, STORY-003
- STORY-005: STORY-001, STORY-003
- STORY-006: STORY-001, STORY-005

## Out Of Scope

- WebSocket transport for managed-agent chat.
- Omnigent session server, runner tunnel, host liveness, resource browser, terminal attachment model, session lists, forks, sub-agent trees, or resource APIs.
- Making one-shot CLI agents fully interactive.
- Replacing Live Logs with a chat-only UI or treating live stream as durable truth.
- Provider-native browser payload contracts or terminal scrollback/container-local databases as durable state.

Rationale: these items are explicitly excluded by MM-976 so the first version extends MoonMind's existing artifact-first Live Logs, SSE, RunObservabilityEvent, and managed-session control surfaces without porting Omnigent's product architecture.

## Reference Notes

- Omnigent was shallow-cloned to /tmp/omnigent-mm-976 as an Apache-2.0 reference. The breakdown preserves MoonMind's stated boundary: borrow operator-experience concepts, not Omnigent's full session-server architecture.

## Coverage Gate

PASS - every major design point is owned by at least one story.
