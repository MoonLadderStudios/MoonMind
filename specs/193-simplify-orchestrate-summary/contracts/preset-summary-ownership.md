# Contract: Preset Summary Ownership

## Canonical Finish Summary

`MoonMind.Run` workflow finalization owns the generic end-of-run summary contract.

Required behavior:

- A finish summary artifact is produced through workflow finalization.
- The finish summary covers terminal success, failure, cancellation, and no-change outcomes.
- Presets may provide structured facts for the finalizer or operator surfaces, but they do not own generic completion narration.

## Jira Orchestrate Preset

`jira-orchestrate` must retain operational gates:

- Move Jira issue to In Progress.
- Load the Jira preset brief.
- Run MoonSpec specify, plan, tasks, align, implement, and verify.
- Create a pull request and write `artifacts/jira-orchestrate-pr.json`.
- Move Jira issue to Code Review only after a PR URL exists.

`jira-orchestrate` must not include:

- A final Jira-specific narrative report step whose only purpose is generic completion reporting.

## MoonSpec Orchestrate Preset

`moonspec-orchestrate` must retain operational gates:

- Classify input and resume point.
- Create or select spec artifacts.
- Run plan, tasks, align, implement, and verify.

`moonspec-orchestrate` must not include:

- A final orchestration report step whose only purpose is generic completion reporting or publish narration.

## Structured Output Rule

Domain-specific facts remain valid when exposed as:

- Step results.
- Handoff artifacts.
- Verification reports.
- Publish context.
- Finish summary structured fields.

They must not require a final narrative report step to exist.
