# Quickstart: Settings Authorization Audit Diagnostics

## Targeted Unit/API Tests

```bash
pytest tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py -q
```

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Verification

No compose-backed integration is required for the first implementation pass because the settings service/API tests use hermetic SQLite and ASGI clients. If settings behavior is later wired through Mission Control browser flows, run:

```bash
./tools/test_integration.sh
```

## Story Validation

1. Confirm backend requests without required settings permissions are denied.
2. Confirm users with specific settings permissions can perform only matching actions.
3. Patch a normal setting and a SecretRef setting.
4. Read `/api/v1/settings/audit` and verify metadata is present, redaction status is explicit, and secret-like values are absent.
5. Read `/api/v1/settings/diagnostics` and verify source, read-only, validation, restart, recent-change, and readiness diagnostics are actionable and sanitized.
6. Confirm `MM-543` remains preserved in spec, tasks, verification notes, commit text, and pull request metadata.
