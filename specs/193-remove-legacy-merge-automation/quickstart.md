# Quickstart: Remove Legacy Merge Automation Workflow

## Focused Validation

1. Confirm only the active workflow class remains:

   ```bash
   rg -n "class MoonMindMergeAutomationWorkflow" moonmind/workflows/temporal/workflows
   ```

2. Confirm the legacy resolver-run activity path is gone from live code and canonical task documentation:

   ```bash
   rg -n "merge_automation\\.create_resolver_run" moonmind docs/Tasks
   ```

3. Run focused unit and workflow-boundary tests:

   ```bash
   ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py
   ```

4. Run final unit verification:

   ```bash
   ./tools/test_unit.sh
   ```

5. Confirm `MM-364` remains preserved in MoonSpec artifacts:

   ```bash
   rg -n "MM-364" specs/193-remove-legacy-merge-automation
   ```
