# Quickstart: Mission Control Report Presentation

## Focused Frontend Validation

```bash
npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx
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

## Optional Required Integration Suite

```bash
./tools/test_integration.sh
```

Run when backend artifact behavior changes beyond contract serialization or when local Docker/compose is available.

## Traceability Check

```bash
rg -n "MM-462|DESIGN-REQ-011|DESIGN-REQ-012|DESIGN-REQ-013|DESIGN-REQ-014|DESIGN-REQ-020|DESIGN-REQ-022" specs/228-mission-control-report-presentation docs/tmp/jira-orchestration-inputs/MM-462-moonspec-orchestration-input.md
```
