# MM-320 Code Review Transition

## Pull Request

- Jira issue: MM-320
- Pull request: https://github.com/MoonLadderStudios/MoonMind/pull/1536
- MoonSpec feature: `specs/201-managed-github-secret-materialization`

## Jira Update

- Trusted tool surface: `MOONMIND_URL` `/mcp/tools`
- PR handoff artifact: `artifacts/jira-orchestrate-pr.json`
- PR reference action: `jira.add_comment`
- PR reference comment id: `10695`
- Previous status at transition time: `In Progress`
- Transition discovery action: `jira.get_transitions`
- Matched transition: `Code Review` -> `51`
- Transition action: `jira.transition_issue`
- Refetch action: `jira.get_issue`
- Confirmed status: `Code Review`
- Confirmed status id: `10039`

## Notes

- The transition was performed only after validating the local handoff artifact contained `jira_issue_key=MM-320` and a non-empty GitHub pull request URL for the current branch.
- The pull request URL is `https://github.com/MoonLadderStudios/MoonMind/pull/1536`.
