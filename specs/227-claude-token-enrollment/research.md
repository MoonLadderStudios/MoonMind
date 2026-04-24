# Research: Claude Token Enrollment Drawer

## FR-001 / DESIGN-REQ-005 Row-Local Manual Enrollment

Decision: Implement the manual enrollment entrypoint inside `ProviderProfilesManager.tsx` as a row-triggered drawer/modal rather than a route or terminal OAuth flow.
Evidence: `frontend/src/components/settings/ProviderProfilesManager.tsx` already classifies `claude_manual` actions and renders `Connect Claude`, `Replace token`, `Validate`, and `Disconnect`; those buttons currently only call `onNotice`. `docs/ManagedAgents/ClaudeAnthropicOAuth.md` sections 3 and 5.3 require manual token enrollment instead of volume-first OAuth.
Rationale: The MM-445 slice established the row-level action source. MM-446 should complete the operator flow without creating a standalone page.
Alternatives considered: Reuse `/api/v1/oauth-sessions`; rejected because the source design says that flow is volume/terminal-shaped and wrong for pasted token enrollment.
Test implications: Unit UI and integration-style UI tests.

## FR-002 / FR-007 / DESIGN-REQ-008 Lifecycle States

Decision: Model drawer state labels equivalent to `not_connected`, `awaiting_external_step`, `awaiting_token_paste`, `validating_token`, `saving_secret`, `updating_profile`, `ready`, and `failed`.
Evidence: The current component has OAuth session statuses but no Claude manual enrollment state model.
Rationale: Explicit states make the external-step/paste/validation/save/profile-update process observable without implying terminal OAuth.
Alternatives considered: One generic loading state; rejected because the Jira brief requires explicit lifecycle states.
Test implications: Unit UI tests must assert state progression.

## FR-004 / FR-005 / FR-006 / FR-012 Token Input Safety

Decision: Use a password-style token input, block empty submission, and clear the token field on success, cancellation, and close.
Evidence: No current token input exists. Constitution/security guardrails prohibit exposing raw credentials.
Rationale: The token should be transient UI state only.
Alternatives considered: Reusing the provider profile secret refs form; rejected because the story requires a focused enrollment drawer and secure paste field.
Test implications: Unit UI tests must assert empty-token blocking and token clearing.

## FR-008 / DESIGN-REQ-009 Failure Redaction

Decision: Redact submitted token values and token-like substrings before rendering validation failure text.
Evidence: Current OAuth failure text can render backend failure reason directly; no Claude-specific redaction exists.
Rationale: Validation errors may include provider messages or accidental token echoes; UI must be secret-safe.
Alternatives considered: Hide all failure detail; rejected because the brief asks for a redacted failure reason.
Test implications: Unit UI test with a failure body containing the submitted token and an `sk-ant-`-like string.

## FR-009 / FR-010 / DESIGN-REQ-009 Readiness Metadata

Decision: Extend trusted `command_behavior` rendering to show structured Claude readiness metadata when present: connected state, last validated timestamp, backing secret existence, launch readiness, and redacted failure reason.
Evidence: Existing tests and component render only `auth_status_label`.
Rationale: The source design specifically places credential health/readiness feedback in the provider row status.
Alternatives considered: Keep one freeform status string only; rejected because it cannot expose the required discrete readiness fields.
Test implications: Unit UI test for status details.

## FR-011 / SC-006 Codex OAuth Regression

Decision: Ensure Claude drawer actions do not call `/api/v1/oauth-sessions` and preserve existing Codex OAuth behavior and tests.
Evidence: Existing `ProviderProfilesManager.test.tsx` covers Codex OAuth start/finalize/retry. Claude buttons currently do not call OAuth.
Rationale: The story must not blur manual token enrollment with terminal OAuth.
Alternatives considered: Share OAuth mutation infrastructure; rejected because it would violate the source design.
Test implications: Existing Codex tests plus a Claude no-call assertion.

## SC-007 Traceability

Decision: Preserve MM-446 and DESIGN-REQ-005/008/009 across `spec.md`, `plan.md`, `tasks.md`, and verification.
Evidence: `spec.md` (Input) is the canonical orchestration input.
Rationale: Downstream artifacts and PR metadata need unambiguous Jira/source mapping.
Alternatives considered: Reference only the source design document; rejected because the user explicitly required preserving MM-446.
Test implications: Final verification.
