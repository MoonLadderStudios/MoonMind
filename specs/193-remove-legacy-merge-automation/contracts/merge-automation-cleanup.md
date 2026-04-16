# Contract: Merge Automation Runtime Cleanup

## Workflow Registration Contract

- Workflow name: `MoonMind.MergeAutomation`
- Active implementation: `moonmind.workflows.temporal.workflows.merge_automation.MoonMindMergeAutomationWorkflow`
- Legacy implementation in `merge_gate.py`: forbidden

Validation:

```bash
rg -n "class MoonMindMergeAutomationWorkflow" moonmind/workflows/temporal/workflows
```

Expected live result: exactly one match in `merge_automation.py`.

## Resolver Launch Contract

When the active workflow reaches ready PR state, it must launch:

- child workflow type: `MoonMind.Run`
- task skill id: `pr-resolver`
- task tool type: `skill`
- task tool version: `1.0`
- publish mode: `none`

The workflow must not call `merge_automation.create_resolver_run`.

## Activity Catalog Contract

Required live merge automation activity:

- `merge_automation.evaluate_readiness`

Removed legacy activity:

- `merge_automation.create_resolver_run`

Validation:

```bash
rg -n "merge_automation\\.create_resolver_run" moonmind docs/Tasks
```

Expected live result: no matches in production code or canonical task documentation. Tests may retain negative assertions that prove the legacy activity remains absent.
