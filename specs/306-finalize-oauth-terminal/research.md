# Research: Finalize OAuth from Provider Terminal

## FR-001 / SCN-001 / SC-001 - Safe Terminal Session Projection

Decision: partial; add terminal-page polling and rendering for safe OAuth session projection.
Evidence: `api_service/api/schemas_oauth_sessions.py` exposes `OAuthSessionResponse` with status, expiry, failure reason, transport refs, and `profile_summary`; `api_service/api/routers/oauth_sessions.py` returns `_oauth_session_response()` from `GET /oauth-sessions/{session_id}`. `frontend/src/entrypoints/oauth-terminal.tsx` currently stores a string connection status and does not render selected profile label, runtime, provider, expiry, failure summary, or profile summary.
Rationale: Backend response shape is close, but the terminal page does not yet use it as the operator-visible completion surface required by the spec.
Alternatives considered: Keep Settings as the only status page. Rejected because the source design explicitly says terminal finalization is not Settings-only.
Test implications: frontend unit tests for projection states; API unit tests if response fields need extension or sanitization.

## FR-002 / SCN-002 - Terminal Attachment Gating

Decision: implemented_unverified; add explicit UI verification before changing behavior.
Evidence: `TERMINAL_ATTACHABLE_STATUSES` in `frontend/src/entrypoints/oauth-terminal.tsx` includes `bridge_ready`, `awaiting_user`, and `verifying`; `api_service/api/routers/oauth_sessions.py` validates attach status, expiry, and terminal refs before issuing an attach token. Existing `oauth-terminal.test.tsx` covers attach and websocket behavior, but not explicit non-attachable projection behavior.
Rationale: The behavior appears present at the API and UI attach boundary, but the new completion UI could regress it.
Alternatives considered: Rework attach transport. Rejected because the story is finalization, not bridge implementation.
Test implications: frontend unit plus existing API attach tests.

## FR-003 / SCN-003 - Eligible Finalize Action

Decision: missing; add terminal-page finalize action gated by eligible statuses.
Evidence: `frontend/src/entrypoints/oauth-terminal.tsx` has Copy/Paste/Send controls only. Settings has `canFinalizeOAuthStatus()` and a Finalize button in `ProviderProfilesManager.tsx`.
Rationale: The primary user story cannot be completed from the terminal page today.
Alternatives considered: Redirect operators back to Settings. Rejected by source requirement that returning to Settings is optional.
Test implications: frontend unit tests for button visibility across statuses.

## FR-004 / SCN-004 / SC-002 - Shared Finalization Operation

Decision: partial; terminal page must call the same finalization endpoint Settings uses.
Evidence: Settings calls `POST /api/v1/oauth-sessions/{session_id}/finalize`; API route exists. Terminal page never calls it.
Rationale: The endpoint is the right integration surface, but one required caller is missing.
Alternatives considered: Add a terminal-specific finalize endpoint. Rejected because source design requires the same finalization operation.
Test implications: frontend unit tests assert terminal caller uses the same endpoint; API unit tests verify response semantics are caller-neutral.

## FR-005 / SCN-005 - Verifying and Registering Profile State Sequence

Decision: partial; API finalization must expose the same state sequence as the workflow-owned path.
Evidence: `moonmind/workflows/temporal/workflows/oauth_session.py` transitions through `verifying`, then `registering_profile`, then `succeeded` on workflow-native finalize. `api_service/api/routers/oauth_sessions.py` verifies volume credentials, then sets status directly to `succeeded` before profile registration, and signals `api_finalize_succeeded`.
Rationale: Settings API finalization was optimized to do work in the API process, but the design now requires callers to observe the same projected states.
Alternatives considered: Move all finalization back into the workflow signal path. Rejected for planning because the existing API-owned verification path has tests and can satisfy the contract with explicit state updates and boundary coverage.
Test implications: API unit tests for `verifying` and `registering_profile`; workflow boundary test to keep `api_finalize_succeeded` compatible.

## FR-006 / DESIGN-REQ-004 - Safe Registered Profile Summary

Decision: partial; return/render a safe profile summary after successful finalization.
Evidence: `ProviderProfileSummary` exists in `schemas_oauth_sessions.py`, and `GET /oauth-sessions/{session_id}` can hydrate profile summary. `finalize_oauth_session()` returns only `{"status": "succeeded"}`. Terminal page has no success summary.
Rationale: The summary model exists but is not returned from the terminal's natural completion action.
Alternatives considered: Require an extra GET after finalize. Acceptable as an implementation detail, but the terminal page must still show the safe summary and tests should verify it.
Test implications: API unit tests for response or follow-up GET; frontend unit tests for rendered summary.

## FR-007 / SC-005 - Settings Query Refresh After Terminal Success

Decision: partial; add terminal-success cache invalidation or cross-surface refresh.
Evidence: `ProviderProfilesManager.tsx` invalidates `PROVIDER_PROFILE_QUERY_KEY` after Settings finalize and after polling observes `succeeded`. Terminal page does not use TanStack Query and cannot currently invalidate Settings-side data.
Rationale: The design requires Settings views that are open to refresh after terminal finalization. Within one browser context, query invalidation is sufficient; across tabs, the implementation may use storage, broadcast, or a best-effort refetch strategy.
Alternatives considered: Rely only on Settings polling. Rejected because Settings may not have the session in local component state when finalization happens exclusively in terminal.
Test implications: frontend unit test for terminal success invalidation/broadcast and existing Settings poll behavior.

## FR-008 / SC-003 - Duplicate and Concurrent Finalization Safety

Decision: partial; make finalize idempotent for in-progress and succeeded states.
Evidence: API accepts `AWAITING_USER` and `VERIFYING`, rejects `SUCCEEDED`, and reruns verify/profile update when called during `VERIFYING`. Existing tests cover success and failure but not duplicate calls.
Rationale: The design requires duplicate clicks or concurrent Settings/terminal requests to converge without duplicate Provider Profiles or mutation of a different profile.
Alternatives considered: Disable buttons only in frontend. Rejected because concurrency must be enforced at the API boundary.
Test implications: API unit tests for `verifying`, `registering_profile`, and `succeeded`; frontend unit test for duplicate-click disabling.

## FR-009 / SC-004 - Safe Failure for Invalid Sessions

Decision: partial; expand explicit failure matrix.
Evidence: Unauthorized finalization returns 404 before verification in `test_finalize_oauth_session_rejects_other_users_claude_session_before_verify`. Cancelled and expired sessions fail by invalid status. Superseded session handling is not explicit in the current model.
Rationale: The story requires safe failure without Provider Profile mutation for cancelled, expired, superseded, and unauthorized sessions.
Alternatives considered: Treat all invalid states as generic 400. Rejected because tests should prove no mutation and sanitized outcomes.
Test implications: API unit tests for cancelled, expired, superseded/active-session conflict policy, and unauthorized cases.

## FR-010 - Immutable Session Identity and Credential Refs

Decision: implemented_unverified; add regression tests.
Evidence: Terminal page has no form fields that can change identity values, and API finalization reads `profile_id`, `volume_ref`, `volume_mount_path`, runtime, provider metadata, and account label from `ManagedAgentOAuthSession`. Tests do not explicitly prove a terminal request cannot override these values.
Rationale: The current shape is safe by construction, but the new terminal action should keep that guarantee.
Alternatives considered: Add request body fields for terminal finalize. Rejected because finalization should use the existing session row.
Test implications: frontend test that terminal finalize sends no mutable identity body; API test that supplied body/query noise cannot alter session-owned identity.

## FR-011 - Cancel, Retry, and Reconnect Actions

Decision: partial; add terminal-page allowed actions.
Evidence: Settings exposes Cancel OAuth, Finalize, and Retry based on local session status. API has cancel and reconnect endpoints. Terminal page currently has no Cancel, Retry, or Reconnect controls.
Rationale: The terminal page must be a completion surface with recovery actions, not only terminal I/O.
Alternatives considered: Keep recovery in Settings only. Rejected by source design.
Test implications: frontend unit tests for allowed actions by status; API unit tests can reuse existing cancel/reconnect coverage with any terminal-specific state assumptions.

## FR-012 / SC-006 - Secret Hygiene

Decision: partial; strengthen assertions around terminal-visible finalization.
Evidence: API tests cover selected secret-safe cases, such as no token in profile repr and workflow redaction. The terminal page currently renders raw `failure_reason` in attach errors and will need safe projection rendering.
Rationale: Finalization introduces new browser-visible success/failure and profile-summary surfaces that must not leak credential material.
Alternatives considered: Trust backend sanitization only. Rejected because UI rendering and response fields both need evidence.
Test implications: API unit tests for sanitized response fields; frontend tests using secret-like failure text to assert redaction or bounded display according to backend contract.

## Test Tooling

Decision: use existing required test runners with focused commands during implementation.
Evidence: `README.md` and repo instructions require `./tools/test_unit.sh` for final unit verification. `package.json` exposes `npm run ui:test`; repo instructions prefer `./tools/test_unit.sh --ui-args` for Vitest targets. Integration CI is `./tools/test_integration.sh`.
Rationale: The feature spans API and frontend. Focused tests should run first, then the full unit runner before final verification.
Alternatives considered: Only frontend tests. Rejected because API idempotency/state sequencing is a key risk.
Test implications: Python API unit, frontend Vitest, and hermetic integration command documented in quickstart.

## Constitution and Source Constraints

Decision: keep runtime work out of canonical docs and preserve source mapping.
Evidence: Constitution Principles IX, XI, XII, and XIII require resilient idempotent contracts, spec-driven artifacts, canonical-doc separation, and no stale compatibility aliases. `docs/ManagedAgents/OAuthTerminal.md` is desired-state source, not a place for migration notes.
Rationale: The plan must not dilute the selected story or add unrelated terminal bridge/session launch work.
Alternatives considered: Implement the entire OAuthTerminal design. Rejected because the active spec is one finalization story.
Test implications: final verification checks traceability and out-of-scope boundaries.
