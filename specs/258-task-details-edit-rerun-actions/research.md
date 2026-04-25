# Research: Task Details Edit and Rerun Actions

The existing executions API already computes action capabilities in `_build_action_capabilities` and gates task editing/rerun by workflow type, feature flag, and original task input snapshot. The narrowest change is adding `canEditForRerun` to the same boundary instead of inferring terminal edit behavior in the UI.

The existing Task Details page renders task actions in a dedicated section and already uses capability fields from `execution.actions`. The UI should continue to treat backend capabilities as source of truth.
