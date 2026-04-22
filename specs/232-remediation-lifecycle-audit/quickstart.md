# Quickstart: Remediation Lifecycle Audit

## Scope

Validate MM-456 as one runtime story: remediation runs expose lifecycle phase, required artifacts, summary block, target-side linkage, compact audit events, cancellation/failure behavior, and Continue-As-New preservation.

## Test-First Flow

1. Add failing unit tests for bounded remediation phase values and lifecycle summary serialization.
2. Add failing unit tests for required remediation artifact metadata and redaction rules.
3. Add failing unit tests for remediation summary block fields and bounded degraded/fallback outcomes.
4. Add failing service-boundary tests for target-side linkage summary metadata.
5. Add failing tests for compact remediation audit event fields and metadata boundedness.
6. Add failing tests for cancellation/failure finalization and Continue-As-New preservation.
7. Implement the minimum remediation runtime changes needed to pass those tests.
8. Run targeted unit tests, then full unit verification.

## Commands

Targeted unit iteration:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

Router/read-model unit tests when target-side summaries or execution serialization change:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/api/routers/test_task_runs.py
```

Full unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Hermetic integration verification if artifact lifecycle or API routes change:

```bash
./tools/test_integration.sh
```

## End-To-End Validation

1. Create or fixture a remediation execution linked to one target workflow/run.
2. Move the remediation lifecycle through evidence collection, diagnosis, approval/action, verification, and terminal outcome.
3. Verify allowed `remediationPhase` values and top-level run state separation.
4. Verify all required `remediation.*` artifact types are linked to the remediation execution with bounded safe metadata.
5. Verify `reports/run_summary.json` contains the compact remediation block.
6. Verify target-side detail metadata exposes inbound remediation summary fields.
7. Verify compact audit events identify actor, principal, remediation workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
8. Verify cancellation/failure and Continue-As-New cases preserve the required evidence and state refs.
