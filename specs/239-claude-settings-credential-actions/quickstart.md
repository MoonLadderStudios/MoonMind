# Quickstart: Claude Settings Credential Actions

## Focused UI Validation

1. Prepare frontend dependencies if needed:

   ```bash
   npm ci --no-fund --no-audit
   ```

2. Run the focused Settings component tests:

   ```bash
   npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx
   ```

3. Validate the story behavior in the test output:

   - `claude_anthropic` shows `Connect with Claude OAuth`.
   - `claude_anthropic` shows `Use Anthropic API key`.
   - `Connect with Claude OAuth` calls `/api/v1/oauth-sessions` with `runtime_id=claude_code`.
   - `Use Anthropic API key` opens the API-key enrollment drawer and does not call `/api/v1/oauth-sessions`.
   - `Validate OAuth` and `Disconnect OAuth` appear only with trusted metadata.
   - `codex_default` OAuth behavior remains unchanged.

## Final Unit Verification

Run the focused backend provider-profile route tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py
```

Validate the backend test output covers:

- Anthropic API keys are stored through Managed Secrets.
- Profile metadata reports `claude_credential_methods`.
- Runtime materialization uses `ANTHROPIC_API_KEY`.
- Raw submitted API keys are not returned in API responses.

Run the required unit test wrapper:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

If the full wrapper is blocked by environment prerequisites, record the exact blocker and preserve focused UI test evidence.
