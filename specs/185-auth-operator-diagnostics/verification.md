# Verification: Auth Operator Diagnostics

Verdict: FULLY_IMPLEMENTED

## Scope

- Jira issue: MM-336
- Feature spec: `specs/185-auth-operator-diagnostics/spec.md`
- Preserved source brief: `docs/tmp/jira-orchestration-inputs/MM-336-moonspec-orchestration-input.md`
- Runtime mode: runtime

## Requirement Coverage

- FR-001 VERIFIED: OAuth session responses expose safe session status, timestamps, terminal transport refs, and redacted failure reason through `OAuthSessionResponse` and `_oauth_session_response`.
- FR-002 VERIFIED: OAuth session responses include a compact `profile_summary` when a matching Provider Profile exists, excluding secret refs, env templates, file templates, raw volume refs, and raw mount paths.
- FR-003 VERIFIED: Managed Codex launch activity metadata includes `authDiagnostics` with selected profile ref, runtime/provider IDs, credential source, materialization mode, volume ref, auth mount target, Codex home path, readiness, and owning component.
- FR-004 VERIFIED: Launch diagnostics tests assert token-like values and raw auth file paths do not appear in metadata.
- FR-005 VERIFIED: Launch failure handling reports `component=managed_session_controller` and a sanitized reason that redacts token-like values and auth paths.
- FR-006 VERIFIED: Managed-session controller record tests confirm durable evidence stays in artifact/log/summary/diagnostics refs and does not publish auth homes as artifact refs.
- FR-007 VERIFIED: OAuth/profile and managed-session diagnostics identify ownership through profile summaries and `component` metadata.

## Source Design Coverage

- DESIGN-REQ-004 VERIFIED through OAuth projection tests and managed-session evidence tests.
- DESIGN-REQ-016 VERIFIED through managed-session launch success/failure diagnostics tests.
- DESIGN-REQ-020 VERIFIED through controller record evidence assertions.
- DESIGN-REQ-021 VERIFIED through `component=managed_session_controller` diagnostics and profile summary ownership.
- DESIGN-REQ-022 VERIFIED through assertions that auth/runtime homes and OAuth terminal scrollback are not ordinary execution artifact refs.

## Test Evidence

- Red-first focused unit run failed before production changes on missing `profile_summary`, unsupported `volumeRef`/`volumeMountPath` profile fields, and missing `authDiagnostics`.
- Focused green: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py` passed with 123 tests.
- Full unit green: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3247 Python tests, 16 subtests, and 222 frontend tests.
- Integration runner: NOT RUN. `/var/run/docker.sock` is unavailable in this managed-agent environment, so `./tools/test_integration.sh` cannot start compose-backed integration tests.

## Residual Risk

- Docker-backed integration verification was blocked by the missing Docker socket, but the story's required boundary behavior is covered by focused API/activity/controller unit tests with mocked Docker/controller boundaries.
