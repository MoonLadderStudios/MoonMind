# Quickstart: Codex Auth Volume Profile Contract

## Prerequisites

- Work from repository root.
- Use managed-agent local test mode for unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1`.
- Docker is required only for compose-backed integration verification.
- No external provider credentials are required for required unit or hermetic integration validation.

## Focused Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py tests/unit/api/routers/test_oauth_sessions.py tests/unit/auth/test_oauth_session_activities.py tests/unit/schemas/test_agent_runtime_models.py
```

Expected red-first result: tests added for `MM-355` fail before production changes when profile shape validation, redaction, or registration/update behavior is missing.

Expected green result: focused tests pass after the story is implemented.

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: full required unit suite passes before final MoonSpec verification.

## Integration Verification

```bash
./tools/test_integration.sh
```

Coverage target: `tests/integration/temporal/test_oauth_session.py` must include or be updated with hermetic coverage for the OAuth verification to Provider Profile registration boundary.

Expected result: hermetic integration coverage passes in a Docker-enabled environment. If `/var/run/docker.sock` is unavailable in the managed-agent container, record that exact blocker in verification output.

## End-To-End Story Check

1. Confirm `spec.md` preserves `MM-355` and the original Jira preset brief.
2. Confirm `plan.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md` exist.
3. Confirm unit and integration test strategies remain separate.
4. Confirm Codex OAuth Provider Profile registration/update preserves refs and slot policy metadata.
5. Confirm operator-facing profile responses and workflow-facing snapshots exclude raw credential contents, token values, auth file payloads, raw auth-volume listings, and environment dumps.
6. Confirm non-Codex profiles are not forced into Codex task-scoped managed-session parity.
7. After implementation, run `/moonspec-verify` equivalent against `specs/189-codex-auth-profile/spec.md`.
