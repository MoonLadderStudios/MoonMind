# Research: Remove Legacy Merge Automation Workflow

## Active Workflow Registration

Decision: Treat `moonmind/workflows/temporal/workflows/merge_automation.py` as the only live `MoonMind.MergeAutomation` workflow implementation.

Rationale: `worker_entrypoint.py` imports and registers `MoonMindMergeAutomationWorkflow` from `merge_automation.py`. The duplicate class in `merge_gate.py` is not registered by the active worker and conflicts with code review clarity.

Alternatives considered: Preserve both classes and add comments. Rejected because MM-364 requires one unambiguous execution path and MoonMind pre-release policy prefers removing superseded internals.

## Helper Module Boundary

Decision: Keep `merge_gate.py` as a helper module for readiness classification, timeout handling, resolver request construction, idempotency keys, and compact continue-as-new payload builders.

Rationale: The active workflow imports these helpers today, and the Jira brief explicitly says to preserve helper functions still used by `merge_automation.py`.

Alternatives considered: Rename helpers into a new module. Rejected as hidden scope because the story is cleanup, not a module rename.

## Legacy Resolver Activity

Decision: Delete `merge_automation.create_resolver_run` from the activity catalog, runtime dispatch map, runtime handler, legacy workflow tests, and live workflow references.

Rationale: The active workflow launches resolver attempts as child `MoonMind.Run` workflows. Once the legacy workflow class is removed, the activity-based resolver launcher is unreachable live code.

Alternatives considered: Keep the activity for possible reuse. Rejected because the brief requires removing the legacy activity path if grep confirms it is no longer reachable.

## Test Strategy

Decision: Use focused unit tests for helper behavior and workflow-boundary tests for the active `merge_automation.py` workflow.

Rationale: The change is internal workflow cleanup. Existing tests already cover helper behavior and active workflow child launch/outcomes; this story adds explicit regression coverage that the legacy class and activity are gone.

Alternatives considered: Compose-backed integration tests. Rejected for this cleanup because no external service or persistence behavior changes, and required evidence is import/registration/workflow-boundary behavior.
