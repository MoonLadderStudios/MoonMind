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

- `unit_test`, `coverage`, `security_pentest`, and `benchmark` mappings all include `report.primary` and expected report classes. Covers FR-001, FR-002, SC-001, DESIGN-REQ-003, and DESIGN-REQ-019.
- Evidence, runtime stdout/stderr, and runtime diagnostics remain separate from curated report classes. Covers FR-004 and DESIGN-REQ-007.
- Generic `output.primary`, `output.summary`, and `output.agent_result` classes remain valid generic outputs during rollout. Covers FR-005 and DESIGN-REQ-020.
- Generic `output.primary`-only artifact sets classify as fallback, not canonical reports. Covers FR-006 and SC-003.
- Report-producing validation rejects missing `report.primary` unless legacy fallback is explicit. Covers FR-003 and SC-002.
- Ordered rollout phases are available for metadata conventions, report links/UI surfacing, compact bundle contracts, and optional projections/filters/retention/pinning. Covers FR-007 and DESIGN-REQ-021.
- Projection summaries contain only compact refs and bounded metadata, rejecting inline report bodies, evidence payloads, logs, screenshots, transcripts, raw URLs, and unsupported storage identifiers. Covers FR-008, SC-004, and DESIGN-REQ-022.
- Traceability output preserves MM-464 across the spec artifacts, runtime code, and tests. Covers FR-009 and SC-005.

Traceability check:

```bash
rg -n "MM-464|DESIGN-REQ-003|DESIGN-REQ-007|DESIGN-REQ-019|DESIGN-REQ-020|DESIGN-REQ-021|DESIGN-REQ-022|report_workflow" specs/232-report-workflow-rollout-examples docs/tmp/jira-orchestration-inputs/MM-464-moonspec-orchestration-input.md moonmind/workflows/temporal tests/unit/workflows/temporal
```
