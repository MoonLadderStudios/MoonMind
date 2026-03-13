# Tasks Jira

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-13  
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskQueueSystem.md`

---

## 1. Purpose

Define a composable Jira-driven workflow for MoonMind using Jira Cloud as the origin source. 

The strategy isolates behaviors into distinct Activities (or discrete executions):
1. `jira-fetch` Activity: Ingests the Jira Cloud metadata.
2. A generic Temporal `MoonMind.Run` task or implementation loop to generate patches based on `issue.md`.
3. `jira-pr` Activity: Wraps the results in a Branch / Pull Request.

---

## 2. Jira Cloud Scope

* Endpoint: `/rest/api/3/issue/{ISSUE_KEY}`
* Format: Atlassian Document Format (ADF)
* Required Credentials: `ATLASSIAN_URL`, `ATLASSIAN_USERNAME`, `ATLASSIAN_API_KEY`

---

## 3. Artifact Contract

`jira-fetch` executes early in the workflow and must deposit a durable workspace artifact:

`<artifacts_root>/jira/ABC-123/issue.raw.json`
`<artifacts_root>/jira/ABC-123/issue.normalized.json`
`<artifacts_root>/jira/ABC-123/issue.md`
`<artifacts_root>/jira/ABC-123/context.json`

The context JSON contains normalized mappings (`issueKey`, `slug`, `prTitle`, `prBody`) that downstream Temporal Activities read natively from the workspace volume to safely checkout the repository, commit, and push exactly as requested by the original issue definitions without LLM hallucination.
