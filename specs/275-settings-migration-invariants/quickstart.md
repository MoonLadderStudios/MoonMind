# Quickstart: Settings Migration Invariants

## Focused Validation

Run backend unit and API boundary coverage:

```bash
./tools/test_unit.sh tests/unit/services/test_settings_catalog.py tests/unit/api_service/api/routers/test_settings_api.py
```

Expected result:
- rename migration preserves effective values,
- removed/deprecated keys reject new writes and surface diagnostics,
- type/schema mismatches fail until explicitly migrated,
- catalog invariants remain descriptor-driven and secret-safe.

## Full Unit Validation

```bash
./tools/test_unit.sh
```

## Hermetic Integration Validation

```bash
./tools/test_integration.sh
```

Run when Docker is available. If Docker is unavailable in the managed runtime, record the exact blocker and rely on focused unit/API evidence for this backend-only story.

## Traceability Check

```bash
rg -n "MM-546|DESIGN-REQ-020|DESIGN-REQ-021|DESIGN-REQ-024|DESIGN-REQ-027|DESIGN-REQ-028" specs/275-settings-migration-invariants
```

Expected result: the Jira key and all source design IDs remain present across MoonSpec artifacts.
