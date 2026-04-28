# Quickstart: Change Application, Reload, Restart, and Recovery Semantics

## Focused Backend Unit Tests

```bash
./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py
```

Expected coverage:
- descriptors include explicit apply modes,
- descriptor validation rejects incomplete application metadata,
- committed changes populate structured event fields,
- diagnostics expose activation state and sanitized restored-reference failures,
- backup-safe settings data excludes raw managed secret plaintext.

## Focused Frontend Tests

```bash
./tools/test_unit.sh --ui-args frontend/src/components/settings/GeneratedSettingsSection.test.tsx
```

Expected coverage:
- Settings UI renders apply mode and affected subsystem metadata,
- restart-required settings show pending activation state and completion guidance,
- broken restored-reference diagnostics are visible without plaintext secret values.

## Integration Verification

```bash
./tools/test_integration.sh
```

Use the full hermetic integration suite before final verification when implementation touches operations/runtime refresh behavior.

## Final Unit Verification

```bash
./tools/test_unit.sh
```

Run the full unit suite before `/speckit.verify` or final handoff.

## Manual Story Check

1. Open Settings and inspect user/workspace descriptors.
2. Confirm every editable setting shows how changes apply.
3. Save a workspace setting and confirm the change event/audit output includes key, scope, source, apply mode, actor, timestamp, and affected systems.
4. Inspect diagnostics for a restart/reload-relevant setting and confirm current value, pending value if applicable, activation state, affected subsystem, and completion guidance.
5. Configure a missing SecretRef or provider profile reference and confirm Settings surfaces a sanitized broken-reference diagnostic.
6. Confirm generated artifacts and verification output preserve `MM-544`.
