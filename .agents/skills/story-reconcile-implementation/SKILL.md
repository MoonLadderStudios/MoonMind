---
name: story-reconcile-implementation
description: Compare MoonSpec story breakdown output against the current repository implementation, preserve fully implemented stories as skipped, narrow partially implemented stories to remaining work, and rewrite the story breakdown handoff before Jira issue creation.
---

# Story Implementation Reconciliation

Use this skill after `moonspec-breakdown` and before Jira issue creation when a breakdown may include work that is already fully or partially implemented.

## Inputs

- Required: a MoonSpec story breakdown JSON path. Prefer `storyBreakdownPath` from the runtime inputs.
- Optional: `storyBreakdownMarkdownPath` for the human-readable reconciliation report.
- Optional: source design path or text from the breakdown source.
- Required: current repository checkout containing the implementation to inspect.

If no readable story breakdown JSON path is available, stop with a clear blocker. Do not create Jira issues or implementation tasks.

## Workflow

1. Read the story breakdown JSON.
2. For each story, inspect relevant source, tests, docs, migrations, contracts, and configuration in the repository.
3. Compare repository evidence against the story acceptance criteria, requirements, source design coverage, and explicit non-goals.
4. Classify every story with exactly one `implementationStatus`:
   - `fully_implemented`
   - `partially_implemented`
   - `not_implemented`
   - `unverifiable`
5. Rewrite the JSON breakdown in place at `storyBreakdownPath` with the same top-level structure and story order.
6. Write or update the markdown report at `storyBreakdownMarkdownPath` when provided.

## Classification Rules

- Use `fully_implemented` only when every in-scope acceptance criterion and requirement has concrete repository evidence and the behavior is testable.
- Use `partially_implemented` when at least one in-scope criterion is implemented and at least one remains missing or insufficiently validated.
- Use `not_implemented` when no meaningful in-scope implementation evidence exists.
- Use `unverifiable` when the requirement is too ambiguous, depends on missing external context, or cannot be checked from the repository state available in this run.
- Be conservative. If evidence is weak, do not mark a story fully implemented.

## Story Output Contract

Preserve the original story fields. Add reconciliation fields; do not delete source traceability.

For a fully implemented story:

```json
{
  "implementationStatus": "fully_implemented",
  "implementedEvidence": [
    {
      "requirement": "DESIGN-REQ-001",
      "status": "met",
      "evidence": "tests/unit/example_test.py covers the acceptance path."
    }
  ],
  "jiraCreation": {
    "action": "skip",
    "reason": "All acceptance criteria have repository evidence."
  }
}
```

For a partially implemented story:

```json
{
  "implementationStatus": "partially_implemented",
  "implementedEvidence": [
    {
      "requirement": "DESIGN-REQ-001",
      "status": "met",
      "evidence": "api_service/example.py implements the existing endpoint."
    }
  ],
  "remainingWork": {
    "summary": "Complete remaining behavior for the original story",
    "description": "Only the unmet behavior should be implemented.",
    "acceptanceCriteria": [
      "Unmet acceptance criterion."
    ],
    "requirements": [
      "Unmet requirement."
    ]
  },
  "jiraCreation": {
    "action": "create_remaining_work_issue",
    "reason": "Some criteria are already implemented; Jira should track only the remaining work."
  }
}
```

For a not implemented story:

```json
{
  "implementationStatus": "not_implemented",
  "jiraCreation": {
    "action": "create_issue",
    "reason": "No implementation evidence found."
  }
}
```

For an unverifiable story:

```json
{
  "implementationStatus": "unverifiable",
  "jiraCreation": {
    "action": "manual_review",
    "reason": "Repository evidence is insufficient to safely create implementation work."
  }
}
```

## Markdown Report

The markdown report must include:

- Source design title or path.
- Story ID, summary, and implementation status for every story.
- Evidence for implemented requirements.
- Remaining work for partially implemented stories.
- Skipped fully implemented stories.
- Unverifiable stories and the exact missing evidence or ambiguity.
- Confirmation that Jira issue creation should consume the reconciled JSON, not the original unreconciled story list.

## Key Rules

- Do not create Jira issues.
- Do not create downstream Jira Orchestrate tasks.
- Do not implement code.
- Do not create or modify `spec.md`.
- Do not delete original story scope, source references, coverage IDs, dependencies, assumptions, or non-goals.
- Fully implemented stories must use `jiraCreation.action = "skip"`.
- Partially implemented stories must preserve original scope and define `remainingWork`.
- Unverifiable stories must use `jiraCreation.action = "manual_review"` so deterministic Jira creation will not create work automatically.
