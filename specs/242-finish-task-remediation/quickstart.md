# Quickstart: Finish Task Remediation Desired-State Implementation

1. Confirm the MM-483 source input is preserved:

   ```bash
   rg -n "MM-483|Canonical Jira Brief|DESIGN-REQ-|FR-001" specs/242-finish-task-remediation docs/tmp/jira-orchestration-inputs/MM-483-moonspec-orchestration-input.md
   ```

2. Run focused remediation action tests:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
   ```

3. If API read-model behavior changes, run router tests:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py
   ```

4. If Mission Control rendering changes, run focused UI tests:

   ```bash
   ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
   ```

5. Before final verification, run:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
   ```

6. Run `/moonspec-verify` and record the MM-483 verdict in `specs/242-finish-task-remediation/verification.md`.
