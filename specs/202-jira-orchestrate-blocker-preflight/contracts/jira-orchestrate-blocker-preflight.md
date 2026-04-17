# Contract: Jira Orchestrate Blocker Preflight

## Preset Step Contract

Jira Orchestrate must include a pre-implementation blocker step after the target issue is moved to In Progress and before brief loading, classification, MoonSpec specification, planning, task generation, implementation, pull request creation, or Code Review transition.

Required input:

```json
{
  "jira_issue_key": "MM-398"
}
```

Required behavior:

1. Fetch the target issue through the trusted Jira tool surface.
2. Inspect Jira issue relationships for blocker links where another issue blocks the target issue.
3. Fetch linked blocker issue status through the trusted Jira tool surface when the target response does not include enough status data.
4. Continue only when the target issue has no blocker links or every detected blocker has status `Done`.
5. Stop as blocked when any detected blocker status is not `Done`.
6. Stop as blocked when a blocker relationship exists but blocker status cannot be determined through trusted Jira data.

Required blocked result shape, expressed as operator-visible content:

```json
{
  "targetIssueKey": "MM-398",
  "decision": "blocked",
  "blockingIssues": [
    {
      "issueKey": "MM-397",
      "status": "In Progress"
    }
  ],
  "summary": "MM-398 is blocked by MM-397, which is In Progress."
}
```

Required continue result shape, expressed as operator-visible content:

```json
{
  "targetIssueKey": "MM-398",
  "decision": "continue",
  "blockingIssues": [],
  "summary": "MM-398 has no unresolved Jira blockers."
}
```

## Preset Expansion Expectations

The expanded `jira-orchestrate` template must:

- include the blocker preflight step before `Load Jira preset brief`;
- preserve the existing `Move Jira issue to In Progress` step as the first step;
- preserve all MoonSpec lifecycle steps after the blocker preflight;
- preserve the pull request step and its `artifacts/jira-orchestrate-pr.json` handoff;
- preserve the final Code Review step as the last step;
- include `MM-398` or the selected issue key in blocker-preflight instructions after template expansion.

## Failure Semantics

- Trusted Jira tool unavailable: stop as blocked with an operator-readable reason.
- Policy-denied Jira fetch: stop as blocked with an operator-readable reason.
- Blocker link present but status missing: stop as blocked.
- Non-blocker issue links present: ignore for blocker decisions.
- Pull request and Code Review stages must not run after a blocked preflight.
