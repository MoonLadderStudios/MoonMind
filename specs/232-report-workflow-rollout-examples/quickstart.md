# Quickstart: Report Workflow Rollout and Examples

Run targeted unit tests while implementing:

```bash
pytest tests/unit/workflows/temporal/test_report_workflow_rollout.py -q
```

Run the broader related unit tests before final verification:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py
```

Expected behavior:

- `unit_test`, `coverage`, `security_pentest`, and `benchmark` mappings all include `report.primary`.
- Evidence and runtime diagnostics remain separate from curated report classes.
- Generic `output.primary`-only artifact sets classify as fallback, not canonical reports.
- Report-producing validation rejects missing `report.primary`.
- Projection summaries contain only compact refs and bounded metadata.

Traceability check:

```bash
rg -n "MM-464|DESIGN-REQ-003|DESIGN-REQ-007|DESIGN-REQ-019|DESIGN-REQ-020|DESIGN-REQ-021|DESIGN-REQ-022|report_workflow" specs/232-report-workflow-rollout-examples docs/tmp/jira-orchestration-inputs/MM-464-moonspec-orchestration-input.md moonmind/workflows/temporal tests/unit/workflows/temporal
```
