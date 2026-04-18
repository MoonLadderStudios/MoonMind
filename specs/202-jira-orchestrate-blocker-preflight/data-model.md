# Data Model: Jira Orchestrate Blocker Preflight

## Target Jira Issue

Represents the Jira issue selected as input to Jira Orchestrate.

Fields:
- `issue_key`: Required Jira issue key, such as `MM-398`.
- `status`: Current Jira status when available from trusted Jira data.
- `links`: Trusted Jira issue relationships available for blocker evaluation.

Validation rules:
- `issue_key` must be present and must be the same issue key provided to Jira Orchestrate.
- Link data must come from the trusted Jira tool surface.
- Missing link data is not treated as evidence of an unresolved blocker unless Jira reports a blocker relationship whose details cannot be resolved.

## Blocking Jira Issue

Represents a Jira issue that blocks the target issue.

Fields:
- `issue_key`: Blocking issue key when available.
- `status`: Blocking issue status when available.
- `source`: The trusted Jira relationship or fetched linked-issue data that identified the blocker.

Validation rules:
- A blocking issue with status `Done` is satisfied.
- A blocking issue with any non-Done status is unresolved.
- A blocking issue with missing or unavailable status is unresolved.
- Non-blocker Jira links must not be treated as blockers.

## Blocker Preflight Outcome

Represents the pre-implementation decision for the target issue.

Fields:
- `target_issue_key`: Target Jira issue key.
- `decision`: `continue` or `blocked`.
- `blocking_issues`: List of blocking issue summaries with issue key and status when available.
- `summary`: Operator-readable result.

Validation rules:
- `decision` is `blocked` when at least one blocking issue is non-Done or status is unavailable.
- `decision` is `continue` when no blocker relationships are present or all detected blockers are Done.
- Blocked outcomes must include the target issue key and all available blocker issue keys and statuses.

## State Transitions

```text
start
  -> fetch target issue through trusted Jira tool surface
  -> inspect blocker relationships
  -> fetch linked blocker statuses when needed
  -> continue when no unresolved blockers exist
  -> blocked when unresolved blocker exists or blocker status is unavailable
```

Blocked outcomes stop before brief loading, classification, MoonSpec implementation, pull request creation, and Code Review transition.
