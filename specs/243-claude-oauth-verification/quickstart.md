# Quickstart: Claude OAuth Verification and Profile Registration

## Focused TDD Flow

1. Add failing verifier tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_volume_verifiers.py
```

Expected red cases before implementation:

- `claude_code` checks `credentials.json` directly under the mounted Claude home.
- `claude_code` accepts qualifying `settings.json`.
- `claude_code` rejects non-qualifying `settings.json`.
- Verification results do not expose raw settings values, file contents, or full artifact paths.

2. Add failing route-boundary tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py
```

Expected red cases before implementation if route coverage is missing:

- Successful Claude finalization verifies before registering or updating `claude_anthropic`.
- Finalization stores OAuth-volume profile refs only.
- Provider Profile Manager sync is called for `claude_code`.
- Unauthorized finalize attempts do not verify or mutate profiles.

3. Implement the smallest production changes needed:

- Update Claude credential artifact detection in `moonmind/workflows/temporal/runtime/providers/volume_verifiers.py`.
- Update finalization route only if the Claude route-boundary tests expose a behavior gap.

4. Run focused validation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/auth/test_volume_verifiers.py \
  tests/unit/api_service/api/routers/test_oauth_sessions.py
```

5. Run final unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Hermetic integration command if API/artifact lifecycle behavior changes:

```bash
./tools/test_integration.sh
```
