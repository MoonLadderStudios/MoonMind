# Quickstart: Surface Canonical Reports in Mission Control

## Focused Frontend Validation

```bash
./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

Expected coverage:

- report panel appears for latest `report.primary`
- report panel appears before the generic Artifacts section
- related `report.summary`, `report.structured`, and `report.evidence` content is shown and individually openable
- executions without `report.primary` retain the normal artifact list without fabricated report status
- report open targets honor `default_read_ref`

## Focused API Contract Validation

```bash
./tools/test_unit.sh tests/contract/test_temporal_artifact_api.py
```

Expected coverage:

- execution artifact list accepts `link_type=report.primary&latest_only=true`
- serialized artifact metadata includes links and default read refs needed by Mission Control

## Final Unit Verification

```bash
./tools/test_unit.sh
```

## Hermetic Integration Strategy

```bash
./tools/test_integration.sh
```

Run only if backend artifact behavior changes beyond the existing read-only query/serialization contract or when validating the broader required integration suite in an environment with Docker/compose available.

## Traceability Check

```bash
rg -n "MM-494|DESIGN-REQ-005|DESIGN-REQ-014|DESIGN-REQ-015|DESIGN-REQ-016" specs/230-mission-control-report-presentation docs/tmp/jira-orchestration-inputs/MM-494-moonspec-orchestration-input.md
```
