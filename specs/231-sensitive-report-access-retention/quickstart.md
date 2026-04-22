# Quickstart: Sensitive Report Access and Retention

## Focused Unit Validation

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifact_authorization.py
```

## Focused Integration Validation

```bash
pytest tests/integration/temporal/test_temporal_artifact_lifecycle.py -m integration_ci -q --tb=short
```

Use `./tools/test_integration.sh` for the full hermetic integration_ci suite when Docker Compose is available.

## Traceability Validation

```bash
rg -n "MM-463|DESIGN-REQ-015|DESIGN-REQ-016|DESIGN-REQ-022" specs/231-sensitive-report-access-retention docs/tmp/jira-orchestration-inputs/MM-463-moonspec-orchestration-input.md
```

## Expected Results

- Restricted report metadata uses preview/default-read behavior without raw download access.
- `report.primary` and `report.summary` default to `long` retention.
- `report.structured` and `report.evidence` default to `standard` unless explicitly overridden.
- Pin then unpin restores report-derived retention.
- Deleting a report artifact leaves unrelated runtime observability artifacts intact.
