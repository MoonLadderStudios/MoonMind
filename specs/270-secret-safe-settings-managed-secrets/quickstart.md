# Quickstart: Secret-Safe Settings and Managed Secrets Workflows

1. Create a managed secret through Settings or `POST /api/v1/secrets`.
2. Confirm list and create responses include `secretRef: "db://<slug>"` and omit plaintext/ciphertext.
3. Copy the SecretRef from Managed Secrets and bind it to a SecretRef setting such as `integrations.github.token_ref`.
4. Validate the secret with `GET /api/v1/secrets/{slug}/validate` and confirm redacted diagnostics.
5. Disable the secret and reload the Settings catalog/effective value for the bound setting; confirm an explicit inactive/broken diagnostic appears.

Validation commands:

```bash
pytest tests/unit/api/test_secrets_api.py tests/unit/services/test_secrets.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py -q
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/components/secrets/SecretManager.test.tsx
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_secrets_api.py tests/unit/services/test_secrets.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py --ui-args frontend/src/components/secrets/SecretManager.test.tsx
```
