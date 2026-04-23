# Research: Claude OAuth Authorization and Redaction Guardrails

## FR-001 / DESIGN-REQ-016

Decision: partial because the repo already enforces owner-scoped access for create, cancel, finalize, and reconnect, but Claude-specific proof is incomplete across the full lifecycle and there is no separate named repair test.
Evidence: `api_service/api/routers/oauth_sessions.py` filters create/profile ownership, cancel/finalize/reconnect by `requested_by_user_id`; `tests/unit/api_service/api/routers/test_oauth_sessions.py::test_finalize_oauth_session_rejects_other_users_claude_session_before_verify` proves unauthorized Claude finalize is rejected before verification or mutation.
Rationale: The core ownership checks exist and likely cover most of the story already. The remaining gap is proof that the repair-like reconnect flow and the other lifecycle actions satisfy the same Claude guardrail standard.
Alternatives considered: Mark missing because repair is not a distinct route. Rejected because `POST /oauth-sessions/{id}/reconnect` clearly acts as the current repair/re-enrollment path.
Test implications: Unit tests for Claude reconnect ownership and any missing create/cancel/attach owner-scope proof; no integration needed unless route behavior changes materially.

## FR-002 / FR-003 / DESIGN-REQ-009 / DESIGN-REQ-017

Decision: partial because hashed one-time attach tokens and consume-on-connect logic already exist, but the repo lacks direct Claude-focused proof that a successfully consumed token cannot be replayed.
Evidence: `attach_oauth_terminal` stores `terminal_attach_token_sha256` and `terminal_attach_token_used=False`; `oauth_terminal_websocket` rejects missing, expired, used, or mismatched tokens and flips `terminal_attach_token_used=True` on connect; tests cover hash-only token issuance for Claude and expired-session rejection, but not successful consume-then-reuse denial.
Rationale: The implementation looks correct, so the likely need is verification rather than redesign. MM-482 still needs explicit replay-denial evidence at the real route/WebSocket boundary.
Alternatives considered: Mark implemented_verified from code inspection alone. Rejected because token-replay behavior is exactly the kind of boundary condition that should be proven by test.
Test implications: Unit tests that attach once, mark the token used, and verify a second connect or attach attempt is rejected.

## FR-004 / DESIGN-REQ-013

Decision: implemented_unverified because secret-redaction helpers are already used throughout OAuth session responses, provider-profile serialization, launch activities, and launcher errors, but MM-482 needs Claude guardrail evidence that ties those surfaces together.
Evidence: `moonmind/utils/logging.py` redacts bearer tokens, token assignments, auth-like paths, and nested payloads; `api_service/api/routers/oauth_sessions.py` redacts `failure_reason`; `api_service/api/routers/provider_profiles.py` and `provider_profile_service.py` redact secret-like runtime fields; `moonmind/workflows/temporal/activity_runtime.py` and `runtime/launcher.py` redact session and launch failures.
Rationale: The infrastructure is present and already exercised in generic or adjacent Claude tests. The remaining work is to prove the MM-482 operator-visible surfaces stay secret-free when Claude-specific outputs contain token-like content.
Alternatives considered: Mark partial and assume code changes are likely. Rejected because the current redaction implementation is already broad and the first step should be targeted proof.
Test implications: Unit tests covering Claude failure reasons, provider-profile payloads, verification results, terminal bridge metadata, and launcher failures with token/path-shaped content.

## FR-005 / DESIGN-REQ-004

Decision: implemented_unverified because provider-profile response redaction and Claude finalize/profile registration already ensure ref-only persistence, but the story still lacks a Claude-specific proof matrix for profile and verification surfaces.
Evidence: `tests/unit/api_service/api/routers/test_provider_profiles.py::test_provider_profile_response_redacts_secret_like_runtime_fields`; `test_provider_profile_manager_payload_redacts_secret_like_runtime_fields`; `tests/unit/api_service/api/routers/test_oauth_sessions.py::test_finalize_oauth_session_registers_claude_oauth_profile` asserts the Claude profile stores OAuth-home refs only and does not surface token-like values in its persisted shape.
Rationale: The code paths likely satisfy the requirement already. MM-482 needs direct traceability showing those protections apply to the Claude OAuth lifecycle rather than only generic profile serialization.
Alternatives considered: Mark implemented_verified now. Rejected because the existing generic redaction test is not Claude-specific and the story demands explicit guardrail evidence.
Test implications: Unit tests that read Claude profile or verification payloads after lifecycle actions and assert ref-only / metadata-only output.

## FR-006 / DESIGN-REQ-018

Decision: partial because auth-volume separation already exists in launch/materialization and provider validation flows, but the repo does not yet prove the auth volume is treated as credential-store-only across all operator-visible surfaces in scope.
Evidence: `moonmind/workflows/adapters/materializer.py`, `managed_agent_adapter.py`, `activity_runtime.py`, and `test_agent_runtime_activities.py::test_launch_session_materializes_claude_oauth_home_environment` keep `MANAGED_AUTH_VOLUME_PATH` and auth diagnostics separate from the workspace; `tests/unit/services/temporal/runtime/test_launcher.py` verifies the launcher cwd is distinct from the auth mount; `provider_profiles.py::validate_claude_oauth_profile` uses the mounted auth volume for verification.
Rationale: Launch-time separation is already strong. The remaining risk is that validation, diagnostics, or audit-adjacent surfaces could still imply that the auth volume is a workspace or artifact root.
Alternatives considered: Mark implemented_verified from launch tests alone. Rejected because MM-482 explicitly spans verification, launch, logs, artifacts, and profile rows.
Test implications: Unit tests for validation/diagnostic payloads and launch metadata; integration only if artifact publication or compose-backed seams change.

## FR-007 / SC-006 / DESIGN-REQ-018

Decision: partial because multiple real-boundary tests already exist, but the story still needs a consolidated MM-482 proof set mapped to every protection in scope.
Evidence: OAuth routes, provider-profile routes, terminal bridge, launch activities, and launcher tests all exist; current Claude-focused tests are distributed across MM-479, MM-480, and MM-481 boundary suites.
Rationale: The implementation is not greenfield. The planning need is to formalize the guardrail matrix so implementation only changes code if distributed boundary tests reveal a genuine gap.
Alternatives considered: Treat adjacent-story tests as final proof. Rejected because MM-482 is specifically the cross-cutting guardrail story and needs its own explicit traceability.
Test implications: Unit-first test plan touching routes, WebSocket bridge, provider-profile serialization, verification, and launch boundaries.

## FR-008

Decision: implemented_verified.
Evidence: `specs/245-claude-oauth-guardrails/spec.md` preserves MM-482 and the original Jira preset brief verbatim.
Rationale: Traceability is already satisfied at the specification stage and must be carried forward.
Alternatives considered: None.
Test implications: None beyond final verify.

## Test Strategy

Decision: Use focused unit tests as the primary TDD harness, with hermetic integration tests only when guardrail changes affect compose-backed API/artifact/worker seams that unit tests cannot prove safely.
Evidence: Existing MM-482-relevant coverage lives in route, WebSocket, provider-profile, launch-activity, and launcher unit suites. Repo guidance requires `./tools/test_unit.sh` for final unit validation and reserves `./tools/test_integration.sh` for hermetic integration seams.
Rationale: The missing evidence is overwhelmingly in Python boundary behavior rather than external-provider behavior. Unit tests provide the fastest red/green loop and align with the story’s need for precise boundary proof.
Alternatives considered: Start with integration tests. Rejected because the current gaps are best isolated and diagnosed at the unit boundary first.
Test implications: Focused `./tools/test_unit.sh` targets first, then full `./tools/test_unit.sh`; run `./tools/test_integration.sh` only if implementation changes artifact publication, worker topology, or other compose-backed seams.
