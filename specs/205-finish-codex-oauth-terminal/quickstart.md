# Quickstart: Finish Codex OAuth Terminal Flow

## Focused Test Commands

Prepare JS dependencies if needed, then run focused frontend tests:

```bash
./tools/test_unit.sh --ui-args frontend/src/components/settings/ProviderProfilesManager.test.tsx
```

Run focused Python OAuth/provider tests:

```bash
./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_volume_verifiers.py tests/unit/api_service/api/routers/test_oauth_sessions.py
```

Run final unit suite before completion:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run hermetic integration CI suite when Docker is available:

```bash
./tools/test_integration.sh
```

## Test-First Scenarios

1. Add a ProviderProfilesManager test that renders a Codex OAuth profile and expects an Auth action.
2. Add a ProviderProfilesManager test that clicking Auth calls `POST /api/v1/oauth-sessions`, opens or records the terminal session, and displays the active status.
3. Add UI tests for cancel, retry, finalize success, finalize failure, and Provider Profile query invalidation.
4. Add provider registry tests expecting Codex bootstrap command `("codex", "login", "--device-auth")` and Codex session transport `moonmind_pty_ws`.
5. Add API/service tests proving newly created Codex OAuth sessions start workflow payloads with interactive terminal transport.
6. Add volume verifier tests proving Codex verification rejects malformed auth material even when expected files exist and returns sanitized metadata.
7. Run focused tests to confirm red failures before implementation changes.
8. Implement changes and rerun focused tests.
9. Run full unit suite.
10. Run integration suite if Docker is available; otherwise record Docker unavailability as an exact blocker, not passing evidence.

## Manual Runtime Check

1. Start MoonMind locally.
2. Open Settings and locate or create a `codex_cli` OAuth-volume Provider Profile.
3. Click Auth.
4. Confirm a terminal opens for the session and displays the Codex device-code login flow.
5. Complete login in the terminal.
6. Finalize the session from Settings.
7. Confirm the Provider Profile remains enabled and uses `oauth_volume` / `oauth_home` with the expected volume reference and mount path.
8. Confirm no token values, private keys, raw auth JSON, or raw volume listings appear in UI notices, logs, or artifacts.
