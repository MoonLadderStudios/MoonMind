# Quickstart: Server-Side Validation and Cross-Setting Policy Enforcement

Traceability: MM-656.

## Prerequisites

- Python dependencies installed for the repo.
- Local test database fixtures available through existing pytest fixtures.
- No external Jira, GitHub, or provider credentials are required for the planned unit or hermetic integration tests.

## Test-First Workflow

1. Add red-first unit tests in `tests/unit/services/test_settings_catalog.py` for:
   - accepted and rejected booleans, strings, numbers, enums, lists, objects, and SecretRefs;
   - numeric, string, list, object, and size constraints;
   - SecretRef syntax/backend policy and provider profile reference validation;
   - cross-setting rules from `docs/Security/SettingsSystem.md` section 18.2;
   - validation boundary metadata for descriptor generation, write request, pre-persistence, effective preview, launch or operation execution, and readiness diagnostics;
   - fail-fast no-mutation and no-sensitive-fallback behavior.

2. Add red-first API/unit tests in `tests/unit/api_service/api/routers/test_settings_api.py` for structured `SettingsError` responses:
   - `key`, `scope`, `details.code`, `details.boundary`, and `details.blocks` are populated;
   - plaintext secrets and unsafe submitted values are not echoed;
   - rejected writes leave current effective values unchanged.

3. Add red-first hermetic integration tests in `tests/integration/api/test_settings_overrides_contract.py` and/or `tests/integration/api/test_settings_effective_values_contract.py` for:
   - write-time rejection through `PATCH /api/v1/settings/{scope}`;
   - effective preview diagnostics through `GET /api/v1/settings/effective`;
   - readiness diagnostics through `GET /api/v1/settings/diagnostics`;
   - one invalid cross-setting combination per documented rule.

## Focused Commands

Run focused unit tests during implementation:

```bash
pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py tests/unit/specs/test_mm656_traceability.py -q
```

Run focused hermetic integration tests during implementation:

```bash
pytest tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci' -q
```

Before final verification, run the repository unit wrapper:

```bash
./tools/test_unit.sh
```

When Docker is available for hermetic integration verification, run:

```bash
./tools/test_integration.sh
```

## End-to-End Story Check

The MM-656 story is complete when:
- valid supported settings are accepted;
- invalid values and invalid combinations are rejected with structured errors;
- effective preview and readiness diagnostics report the same rule failures;
- rejected writes do not persist partial changes;
- missing SecretRefs, disabled provider profiles, locked settings, unsupported scopes, and workspace policy violations fail explicitly;
- MM-656 remains preserved in spec, plan, tasks, implementation notes, verification output, commit text, and pull request metadata.
