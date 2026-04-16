# Research: Simplify Orchestrate Summary

## Summary Ownership

Decision: Treat `MoonMind.Run` finalization and `reports/run_summary.json` as the canonical owner of generic end-of-run summaries.
Rationale: `moonmind/workflows/temporal/workflows/run.py` already builds a finish summary during finalization with outcome, publish, proposal, dependency, operator summary, publish context, and last-step data. Relying on that path keeps failure and cancellation behavior consistent when late preset steps do not run.
Alternatives considered: Keep preset-authored narrative report steps and ask them to mention finalization. Rejected because it preserves split ownership and drift risk.

## Preset Report Step Removal

Decision: Remove only the final generic report steps from `jira-orchestrate` and `moonspec-orchestrate`.
Rationale: Earlier preset steps still perform required work such as Jira state changes, MoonSpec verification, PR creation, and publish handoff. Removing only report-only steps narrows scope and avoids changing workflow logic.
Alternatives considered: Replace report steps with structured output aggregation steps. Rejected because MM-366 asks for workflow finalization to own generic summaries and preserve domain facts through existing structured outputs or artifacts.

## Jira Orchestrate Handoff

Decision: Preserve the `artifacts/jira-orchestrate-pr.json` handoff and Code Review transition step.
Rationale: The PR URL handoff is required before Jira can move to Code Review and is not a generic completion narrative. It remains structured data for operator visibility and downstream reasoning.
Alternatives considered: Move PR handoff into the final removed report step. Rejected because the current PR creation step already owns the local handoff artifact.

## MoonSpec Orchestrate Handoff

Decision: Remove the final "Return orchestration report and defer publish actions" step while relying on verification output, publish behavior, and workflow finish summary for final outcome.
Rationale: The final step is a narrative report only and explicitly says publish behavior is handled elsewhere. Removing it avoids duplicate completion reporting without removing the MoonSpec implementation or verification stages.
Alternatives considered: Keep the step but rename it to structured handoff. Rejected because there is no current structured output contract in that step beyond prose reporting.

## Test Strategy

Decision: Add regression assertions at the task-template catalog boundary proving both seeded presets omit generic final report steps while preserving required structured handoff instructions.
Rationale: The seed catalog test exercises the real YAML loading and expansion behavior that operators use. It is the smallest reliable boundary for this change.
Alternatives considered: Temporal workflow integration tests. Rejected for this slice because no workflow finalization code changes are planned and the existing finalizer already writes summary artifacts.
