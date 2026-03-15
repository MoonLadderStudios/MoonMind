# Tasks Jira Integration

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-14  
Related: `docs/Tasks/TaskArchitecture.md`, `docs/Tasks/TaskQueueSystem.md`

---

## 1. Purpose

Define a composable Jira-driven workflow for MoonMind using Jira Cloud as the origin source. 

The strategy isolates behaviors into distinct Temporal Activities within the `AgentTaskWorkflow`:
1. `JiraFetchActivity`: Ingests the Jira Cloud metadata given an Issue Key.
2. `AgentLoopActivity`: The generic Agent implementation loop (e.g., OpenHands) processes the instructions to generate patches based on the fetched `issue.md`.
3. `PublishActivity` / `JiraTransitionActivity`: Wraps the results in a Branch / Pull Request, and transitions the Jira ticket state.

---

## 2. Jira Cloud Scope

* Endpoint: `/rest/api/3/issue/{ISSUE_KEY}`
* Format: Atlassian Document Format (ADF)
* Required Credentials: `ATLASSIAN_URL`, `ATLASSIAN_USERNAME`, `ATLASSIAN_API_KEY` mapped securely into the `temporal-worker-sandbox`.

---

## 3. Artifact Contract

`JiraFetchActivity` executes early in the `AgentTaskWorkflow` and must deposit durable workspace artifacts into the shared workflow volume:

`<artifacts_root>/jira/ABC-123/issue.raw.json`
`<artifacts_root>/jira/ABC-123/issue.normalized.json`
`<artifacts_root>/jira/ABC-123/issue.md`
`<artifacts_root>/jira/ABC-123/context.json`

The context JSON contains normalized mappings (`issueKey`, `slug`, `prTitle`, `prBody`) that downstream Temporal Activities read natively from the workspace volume. This allows the agent to safely checkout the repository, commit, and push exactly as requested by the original issue definitions without LLM hallucination of branch names or PR titles.

---

## 4. Workflow Execution Flow

1. **Triggering**: A webhook from Jira or a manual Mission Control submission triggers an `AgentTaskWorkflow` with a Jira payload.
2. **Context Assembly**: The `JiraFetchActivity` pulls the ADF description, translates it to Markdown, and writes the Artifact Contract.
3. **Workspace Prep**: `PrepareWorkspaceActivity` ensures the codebase is cloned into the Agent Sandbox.
4. **Execution**: The Agent reads `issue.md` from the workspace and applies the required code changes.
5. **Publishing**: The `PublishActivity` creates the PR using the title and description from `context.json`.
6. **Closing the Loop**: A final `JiraTransitionActivity` transitions the Jira ticket to "In Review" and comments the PR link.
