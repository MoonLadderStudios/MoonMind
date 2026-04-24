# Research: Claude Browser Terminal Sign-In Ceremony

## Classification

Decision: Treat MM-479 as a single-story runtime feature request.
Evidence: `spec.md` (Input) defines one operator story: complete Claude OAuth in a MoonMind browser terminal by opening Claude's URL externally and pasting the returned token or code.
Rationale: The brief is independently testable through one OAuth terminal ceremony and references bounded source sections from `docs/ManagedAgents/ClaudeAnthropicOAuth.md`.
Alternatives considered: Treating the source document as a broad design was rejected because MM-479 selects one story from that design.
Test implications: Unit, route-level, and UI integration-style tests are required.

## Existing OAuth Terminal Page

Decision: Implemented and verified with MM-479-specific UI coverage.
Evidence: `frontend/src/entrypoints/oauth-terminal.tsx` polls `GET /api/v1/oauth-sessions/{session_id}`, waits until terminal identifiers are present and status is `bridge_ready`, `awaiting_user`, or `verifying`, calls `/terminal/attach`, then connects to the returned WebSocket URL.
Rationale: The shared UI matches the source flow and should remain runtime-neutral, but the added MM-479 UI test proves a Claude `runtime_id = claude_code` ceremony reaches attach in `awaiting_user`.
Alternatives considered: Adding a Claude-only terminal page was rejected because the source design says Claude uses the same browser-terminal infrastructure as Codex OAuth.
Test implications: Mission Control UI coverage exists for a Claude session payload that waits through non-ready states and attaches in `awaiting_user`.

## OAuth Attach Token Contract

Decision: Implemented and verified with a Claude awaiting-user route test.
Evidence: `api_service/api/routers/oauth_sessions.py` issues a random attach token, stores only `terminal_attach_token_sha256`, marks `terminal_attach_token_used` false, and allows attach only for terminal-ready statuses including `AWAITING_USER`.
Rationale: The route already supports the required security contract; the Claude-specific fixture makes MM-479 traceability explicit.
Alternatives considered: Creating a separate token model was rejected because existing session metadata is sufficient and already tested.
Test implications: Route coverage exists for `runtime_id = claude_code`, status `AWAITING_USER`, hash-only token metadata, and no raw token leakage in persisted metadata.

## PTY Input Forwarding And Secret Safety

Decision: Implemented and verified with a Claude authorization-code input test.
Evidence: `_handle_oauth_terminal_ws_message` delegates input frames to `TerminalBridgeConnection.handle_frame_for_pty`; `TerminalBridgeConnection.safe_metadata()` reports counts and resize data without raw input values.
Rationale: The behavior satisfies the ceremony if pasted auth codes are forwarded exactly to the PTY and not present in durable metadata.
Alternatives considered: Redacting the PTY input before forwarding was rejected because the Claude CLI must receive the exact returned code.
Test implications: Unit coverage sends a token-like Claude authorization code to the in-memory PTY and asserts the PTY receives it while safe metadata excludes it.

## Generic Terminal Rejection

Decision: Implemented and already verified.
Evidence: `tests/unit/services/temporal/runtime/test_terminal_bridge.py` rejects `docker_exec` and `task_terminal` frames.
Rationale: This preserves the OAuth terminal as credential enrollment/repair only.
Alternatives considered: Duplicating the same assertion in MM-479 tests was rejected as unnecessary; final validation will run the existing test.
Test implications: Keep existing focused terminal bridge tests in the validation command.
