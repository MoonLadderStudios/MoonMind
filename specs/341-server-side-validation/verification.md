# Verification: Server-Side Validation and Cross-Setting Policy Enforcement

Traceability: MM-656.

## Commands

- `pytest tests/unit/specs/test_mm656_traceability.py -q`: PASS, 1 passed.
- `pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/specs/test_mm656_traceability.py -q`: PASS, 98 passed.
- `pytest tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci' -q`: PASS, 5 passed.
- `./tools/test_unit.sh`: PASS, 4889 Python tests passed with 1 xpassed and 16 subtests passed; frontend Vitest passed with 20 files and 338 tests passed, 228 skipped.
- `./tools/test_integration.sh`: BLOCKED in the managed container. Docker Compose started, then the daemon returned `403 Forbidden` with "Request forbidden by administrative rules."

## Story Check

- Valid supported settings are accepted by the focused service and API tests.
- Invalid values, invalid referenced resources, unsafe payloads, and policy-denied combinations are rejected with `SettingsError` details containing key, scope, code, boundary, rule, and blocks.
- Effective preview and readiness diagnostics expose the same validation code vocabulary for policy-denied values.
- Rejected writes preserve existing effective values and do not persist partial changes.
- Secret plaintext and rejected raw submitted secret values are not echoed in API responses, diagnostics, or validation issue JSON.
