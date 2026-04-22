# Quickstart: Report Artifact Contract

## Targeted Validation

Run the focused artifact service tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py
```

Expected coverage:

- Supported `report.*` link types are accepted.
- Unsupported `report.*` link types are rejected.
- Report metadata allowlist and bounded value rules are enforced.
- Secret-like report metadata is rejected before storage.
- Generic output links still work with generic metadata.
- Latest `report.primary` lookup uses existing execution linkage.

## Full Unit Validation

Run the full unit suite before final handoff:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Optional Hermetic Integration

When Docker is available, run:

```bash
./tools/test_integration.sh
```

This story does not require provider credentials.
