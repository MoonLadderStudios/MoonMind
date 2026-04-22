# Quickstart: Report Bundle Workflow Publishing

## Focused Unit Tests

Run targeted report bundle tests during implementation:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py tests/unit/workflows/temporal/test_artifacts_activities.py tests/unit/workflows/temporal/test_activity_runtime.py
```

Expected coverage:
- Report bundle result serializes as `report_bundle_v = 1` with compact refs.
- Embedded bodies, logs, screenshots, transcripts, raw URLs, evidence blobs, and large findings are rejected.
- Final report bundles expose exactly one canonical final report marker.
- Step metadata is attached as bounded metadata.
- Evidence artifacts remain separately addressable.
- `artifact.publish_report_bundle` is registered and routed through the artifacts task queue.

## Full Unit Verification

Before final verification:

```bash
./tools/test_unit.sh
```

## Integration Verification

When Docker is available:

```bash
./tools/test_integration.sh
```

The required integration suite is compose-backed and limited to `integration_ci`.

## Traceability

Confirm MM-461 and source requirement IDs remain visible:

```bash
rg -n "MM-461|DESIGN-REQ-006|DESIGN-REQ-008|DESIGN-REQ-010|DESIGN-REQ-014|DESIGN-REQ-017|DESIGN-REQ-018|report_bundle_v" specs/227-report-bundle-workflow-publishing docs/tmp/jira-orchestration-inputs/MM-461-moonspec-orchestration-input.md moonmind/workflows/temporal tests/unit/workflows/temporal
```
