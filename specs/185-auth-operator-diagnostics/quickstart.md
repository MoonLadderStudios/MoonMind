# Quickstart: Auth Operator Diagnostics

## Focused Validation

1. Run OAuth session API tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api_service/api/routers/test_oauth_sessions.py
```

2. Run managed session activity/controller tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py
```

3. If Docker is available, run required hermetic integration coverage:

```bash
./tools/test_integration.sh
```

## End-To-End Story Check

1. Create or fetch an OAuth session that references a registered Provider Profile.
2. Confirm the OAuth session response includes status, timestamps, redacted failure reason, and a compact profile summary.
3. Launch a managed Codex session with an OAuth-backed profile.
4. Confirm launch/session metadata includes selected profile ref, credential source, volume ref, auth mount target, Codex home path, readiness, and component ownership.
5. Confirm responses and metadata do not contain credential contents, token values, raw auth-volume listings, runtime-home contents, environment dumps, or terminal scrollback.

## Final Verification

Run the full unit suite before final MoonSpec verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```
