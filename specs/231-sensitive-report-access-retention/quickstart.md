# Quickstart: Apply Report Access and Lifecycle Policy

## Focused Unit Validation

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifact_authorization.py
```

## Focused Integration Validation

```bash
pytest tests/integration/temporal/test_temporal_artifact_lifecycle.py -m integration_ci -q --tb=short
```

Use `./tools/test_integration.sh` for the full hermetic `integration_ci` suite when Docker Compose is available.

## Traceability Validation

```bash
rg -n "MM-495|DESIGN-REQ-011|DESIGN-REQ-017|DESIGN-REQ-018" specs/231-sensitive-report-access-retention
```

## Expected Results

- Restricted report metadata uses preview/default-read behavior without raw download access.
- Report metadata validation rejects unsupported, unsafe, or oversized values.
- `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, and `report.export` default to `long` retention.
- `report.structured` and `report.evidence` default to `standard` unless explicitly overridden.
- Pin then unpin restores report-derived retention.
- Deleting a report artifact leaves unrelated runtime observability artifacts intact.
