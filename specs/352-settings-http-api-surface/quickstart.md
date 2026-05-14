# Quickstart: Settings HTTP API Surface

## Prerequisites

- Python dependencies installed for the repository.
- Local test database fixtures available through existing pytest fixtures.
- No external provider credentials are required for required unit or hermetic integration tests.

## Unit Test Strategy

Run the full required unit suite before finalizing:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Focused iteration for MM-657:

```bash
pytest tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py -q
```

Unit coverage should be written before implementation for:
- `POST /api/v1/settings/validate` route shape, permissions, structured errors, and no-commit behavior.
- `POST /api/v1/settings/preview` route shape, permissions, effective-value diffs, dependency warnings, reload requirements, structured errors, redaction, and no-commit behavior.
- Shared error envelope coverage for the MM-657 documented error matrix.

## Integration Test Strategy

Run the full required hermetic integration suite before finalizing:

```bash
./tools/test_integration.sh
```

Focused iteration for MM-657:

```bash
pytest tests/integration/api/test_settings_http_api_surface_contract.py tests/integration/api/test_settings_overrides_contract.py tests/integration/api/test_settings_effective_values_contract.py -m 'integration_ci' -q
```

Integration coverage should prove:
1. Catalog responses can represent `providers-secrets`, `user-workspace`, and `operations`.
2. Effective list and single-key reads preserve source explanations and diagnostics.
3. User/workspace updates persist atomically and return refreshed effective values.
4. Reset returns inherited effective values and leaves secrets, provider profiles, OAuth volumes, defaults, and audit rows intact.
5. Validate and preview requests do not commit overrides or audit mutations.
6. Preview returns diffs, dependency warnings, and reload requirements.
7. Audit reads filter by key/scope and redact sensitive values.
8. Structured errors include `error`, `message`, `key`, `scope`, and contextual `details`.

## End-To-End Story Validation

1. Read `GET /api/v1/settings/catalog?section=user-workspace&scope=workspace`.
2. Read `GET /api/v1/settings/effective?scope=workspace`.
3. Read `GET /api/v1/settings/effective/workflow.default_publish_mode?scope=workspace`.
4. Submit `POST /api/v1/settings/validate` with a valid proposed change and confirm no persisted override.
5. Submit `POST /api/v1/settings/preview` with the same change and confirm proposed diffs and reload metadata.
6. Submit `PATCH /api/v1/settings/workspace` with matching `expected_versions` and confirm a refreshed effective value.
7. Submit the same patch with stale `expected_versions` and confirm `version_conflict` with no mutation.
8. Submit `DELETE /api/v1/settings/workspace/workflow.default_publish_mode` and confirm the inherited effective value.
9. Read `GET /api/v1/settings/audit?scope=workspace` and confirm redaction policy is honored.
10. Run final MoonSpec verification against `specs/352-settings-http-api-surface/spec.md`, preserving `MM-657` traceability.
