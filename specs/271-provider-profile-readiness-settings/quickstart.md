# Quickstart: Provider Profile Management and Readiness in Settings

## Targeted Unit Tests

```bash
pytest tests/unit/api_service/api/routers/test_provider_profiles.py -q
pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q
npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx
```

## Final Unit Verification

```bash
./tools/test_unit.sh
```

## End-to-End Story Check

1. Start MoonMind locally.
2. Open Mission Control Settings -> Providers & Secrets.
3. Create a provider profile with runtime, provider, materialization mode, default model, SecretRef role binding, concurrency, cooldown, priority, tags, and enabled state.
4. Confirm the list displays launch-relevant profile metadata and readiness checks.
5. Disable the profile and confirm readiness becomes blocked.
6. Configure an OAuth-backed profile without required OAuth metadata and confirm readiness explains the missing fields.
7. Configure a profile with invalid or missing SecretRef data and confirm sanitized diagnostics appear without plaintext.
8. Configure `workflow.default_provider_profile_ref` to a missing or disabled profile and confirm effective settings return explicit launch-blocker diagnostics.
9. Confirm runtime strategy code still owns command construction, environment shaping, generated files, process launch, and capability checks.
