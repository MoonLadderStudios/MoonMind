---
description: Ensure workflow summaries include a link to created PRs
---

# PR Summary Formatting Rule

When an agentic workflow completes, if that workflow created a Pull Request (PR) during its execution, the final summary or walkthrough artifact must explicitly include a direct link to that PR.

## Expected Format
Include a concluding statement similar to:
> "Workflow completed successfully and created PR: https://github.com/<owner>/<repo>/pull/<number>"

This ensures the user has immediate access to the PR directly from the workflow's summary output.

## JSON Artifact Format

When a workflow produces a `reports/run_summary.json` artifact, the `publish.prUrl` field must be populated with the PR link.

**Example:**
```json
"publish": {
  "mode": "pr",
  "status": "published",
  "prUrl": "https://github.com/<owner>/<repo>/pull/<number>"
}
```
