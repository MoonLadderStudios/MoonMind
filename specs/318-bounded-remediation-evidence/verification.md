# Verification: Bounded Remediation Evidence Context

## Implementation Step Evidence

- Jira issue: MM-618
- Feature directory: `specs/318-bounded-remediation-evidence`
- Implemented scope: bounded remediation context enrichment for target task-run evidence refs, compact selected-step summaries, evidence availability/degraded metadata, and live-follow state normalization.

## Red-First Evidence

- Command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py -q`
- Initial result before production changes: failed as expected.
- Expected failures:
  - `test_remediation_context_builder_enriches_task_run_evidence_and_live_follow` showed selected steps lacked `status`, `summary`, and `artifactRefs`, and task-run evidence lacked typed refs.
  - `test_remediation_context_builder_records_historical_degraded_evidence` showed task-run evidence lacked `mergedLogsRef`.

## Passing Unit Evidence

- Command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py -q`
- Result: passed, 35 tests.

- Command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/api/routers/test_task_runs.py tests/unit/api/routers/test_executions.py -q`
- Result: passed for the focused Python unit targets and the bundled frontend unit run invoked by the test runner.

- Command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Result: passed, including full Python unit suite (`4521 passed`, `1 xpassed`, `16 subtests passed`) and focused frontend task-detail tests (`87 passed`).

## Integration Evidence

- Command: `./tools/test_integration.sh`
- Result: blocked by managed runtime Docker access.
- Exact blocker: Docker Compose attempted to build `repo-pytest`, then Docker returned `403 Forbidden` with `Request forbidden by administrative rules`.

## Remaining Verify Step

- Final `/moonspec-verify` is intentionally not run in this implementation step because the current managed step boundary authorizes `moonspec-implement` only.
