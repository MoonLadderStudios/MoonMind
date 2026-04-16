# Data Model: Remove Legacy Merge Automation Workflow

No new durable data entities, database tables, or serialized external models are introduced.

## Existing Runtime Concepts

- **MergeAutomation workflow type**: Existing Temporal workflow name `MoonMind.MergeAutomation`; after cleanup, exactly one class implements it in active runtime code.
- **Readiness evidence**: Existing normalized evidence produced by helper functions and consumed by the active workflow.
- **Resolver child request**: Existing `MoonMind.Run` child workflow request built for `pr-resolver` with `publishMode=none`.
- **Activity catalog entry**: Existing internal activity registration list; this story removes the dead `merge_automation.create_resolver_run` entry and keeps `merge_automation.evaluate_readiness`.

## State Transitions

- Active merge automation status transitions remain unchanged.
- No migration or persistence cutover is required because MoonMind is pre-release and active workers already register the workflow from `merge_automation.py`.
