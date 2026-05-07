# Implementation Plan: Publish Repair Feedback

## Constitution Check

- Resilient by default: PASS. Adds one bounded recovery turn before failing.
- Spec-driven development: PASS. This artifact defines the behavior and tests.
- Thin scaffolding, thick contracts: PASS. Keeps publishing as a workflow-owned postcondition.
- Compatibility: PASS. New behavior is behind a Temporal patch marker; existing terminal behavior remains the fallback.

## Technical Approach

- Track the latest managed `AgentExecutionRequest` that can be reused for a corrective turn.
- Add a publish repair helper in `MoonMind.Run` that sends one extra `MoonMind.AgentRun` child with a precise repair instruction.
- Invoke repair before native PR creation when PR publishing has no PR URL and the recorded push result says no commits were available.
- Update Jira Orchestrate seed wording so managed agents prepare committed work and let MoonMind publishing create the PR.
- Cover repair message construction and Jira template/runtime planner behavior with focused unit tests.
