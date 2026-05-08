# Quickstart: Observable Remediation Repair and Prevention Lifecycle

## Prerequisites

- Python 3.12 environment with repository dependencies installed.
- No external provider credentials are required for planned unit or hermetic integration coverage.
- Docker socket is required only for the full `./tools/test_integration.sh` wrapper.

## Test-First Flow

1. Add failing unit tests in `tests/unit/workflows/temporal/test_remediation_context.py` for:
   - repair candidate decisions,
   - repair outcome classification,
   - recurrence-prevention outputs,
   - decision-log payload shape,
   - corrected-instruction retry provenance,
   - cancellation finalization,
   - rerun and Continue-As-New summary continuity.

2. Run focused unit tests:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
   ```

3. Implement the lifecycle decision/finalization behavior in the remediation service boundary:
   - `moonmind/workflows/temporal/remediation_context.py`
   - `moonmind/workflows/temporal/remediation_tools.py`
   - `moonmind/workflows/temporal/remediation_actions.py` only if action/guard evidence needs new compact fields

4. Add or extend hermetic integration coverage in `tests/integration/temporal/test_remediation_action_contracts.py` for:
   - published `remediation.decision_log`,
   - published `remediation.summary` with repair and prevention outputs,
   - no new target mutation after cancellation,
   - summary continuity for changed target run and Continue-As-New refs.

5. Run focused unit tests again:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
   ```

6. Run full unit verification:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
   ```

7. Run hermetic integration verification when Docker is available:

   ```bash
   ./tools/test_integration.sh
   ```

## End-To-End Story Validation

Use controlled target/remediation records and fake action executors to validate:

- A healthy-before-action target records `not_attempted` repair and still records prevention analysis.
- A safe action path records `attempted`, action request/result/verification refs, and `repaired` or `still_failed`.
- Unsafe, denied, approval-required, and budget-exhausted paths record bounded reasons and no unauthorized side effect.
- Prevention output records one of reviewable change, findings-only, no reviewable fix, or policy blocked.
- Cancellation finalization records no new target mutation, lock-release attempt, final audit/summary publication attempt, and any degraded evidence.
- Changed target runs and Continue-As-New payloads preserve target identity, pinned run, resulting run when applicable, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.

## Expected Artifacts

- `reports/remediation_context.json`
- `reports/remediation_plan.json`
- `logs/remediation_decision_log.ndjson` or JSON equivalent preserving the v1 entry contract
- `reports/remediation_action_request-<id>.json`
- `reports/remediation_action_result-<id>.json`
- `reports/remediation_verification-<id>.json`
- `reports/remediation_summary.json`

All artifacts must be redacted and contain refs rather than raw logs or artifact bodies.
