# Quickstart: Launch Codex Auth Materialization

## Focused Unit Verification

```bash
./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py -k 'oauth_profile_auth_target'
./tools/test_unit.sh tests/unit/services/temporal/runtime/test_codex_session_runtime.py -k 'auth_volume_equal_to_codex_home or seeds_auth_volume'
```

## Broader Unit Verification

```bash
./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_codex_session_runtime.py
```

## Integration Verification

When Docker is available:

```bash
./tools/test_integration.sh
```

Provider OAuth verification with real credentials is not required for this story.
