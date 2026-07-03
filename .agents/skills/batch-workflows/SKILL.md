---
name: batch-workflows
description: Resolve Jira issues by project/status and enqueue one child MoonMind workflow per target using a selected run capability, inherited runtime, and a shared advanced publish policy.
metadata:
  publish:
    mode: auto
    owner: agent
    requiresEvidence: true
    verifyRemoteHead: exact
  required-capabilities:
    - git
    - jira
    - gh
---

# Batch Workflows Skill

## Purpose

Resolve a Jira status cohort, then queue one child MoonMind workflow per target
running a selected child capability such as `skill:jira-verify` or
`preset:jira-implement`. Every child inherits the parent runtime
(`runtimeInheritance="caller"`) and a single shared publish policy. The parent
records a summary artifact that links every queued child workflow.

This replaces one-off prompts like "Queue Jira Verify for every MM issue in In
Progress" with a single batch run.

## Inputs (preset inputs / skill args)

- `jira_project_key` (string, required): Jira project key, for example `MM`.
- `jira_status` (string, required): Jira status name, for example `In Progress`.
- `run_ref` (string, required): child capability to run per target. Default
  `skill:jira-verify`. Supported values are `skill:jira-verify`,
  `preset:jira-implement`, and the helper also supports
  `preset:github-issue-implement` for non-default GitHub target files.
- `max_workflows` (number, optional): hard cap on queued children. Default `25`.
- `constraints` (string, optional): shared input copied to every child.
- `additional_jql` (string, optional): advanced JQL AND-clause appended to the
  project/status query.
- `repository` (string, optional): repository override when workflow context
  cannot infer it.
- `publish_mode` (string, optional): advanced child publish override, `none`,
  `branch`, `pr`, or `pr_with_merge_automation`. Default `none`.

## Workflow

1. **Resolve Jira targets** into the canonical resolved-target shape and write
   them to `artifacts/batch-workflows-targets.json`, then preview the list before
   queueing. Use the trusted Jira tool surface to search:

   ```text
   project = "<jira_project_key>" AND status = "<jira_status>"
   ```

   Append `additional_jql` only when provided. Each target is:

   ```json
   {
     "provider": "jira",
     "ref": "MM-123",
     "jiraIssue": {"key": "MM-123", "summary": "...", "description": "...",
                    "url": "...", "status": "In Progress", "assignee": "..."},
     "repository": "MoonLadderStudios/MoonMind"
   }
   ```

   Never use raw Jira credentials, web scraping, or guessed issue content to
   build the target list.

2. **Queue child workflows** by running the helper:

   ```bash
   python3 .agents/skills/batch-workflows/bin/batch_workflows.py \
     --targets artifacts/batch-workflows-targets.json \
     --run-ref <skill:jira-verify|preset:jira-implement> \
     --publish-mode <none|branch|pr|pr_with_merge_automation> \
     --constraints-file <optional path to shared constraints> \
     --max-workflows <cap>
   ```

   For each resolved target the helper:
   - Auto-binds Jira issues into `skill:jira-verify` inputs (`jira_issue`,
     `jira_issue_key`, `repository`, `verification_mode`, `update_status`, and
     `constraints`).
   - Auto-binds Jira issues into `preset:jira-implement` inputs
     (`jira_issue`, `jira_issue_key`, and `constraints`).
   - Auto-binds GitHub issue targets into `preset:github-issue-implement` inputs
     when a non-default GitHub target file is provided.
   - Stamps `runtimeInheritance="caller"` plus a fallback copy of the parent's
     effective runtime (mode/model/effort/provider profile) so children reuse the
     caller runtime even on deployments that do not honour the inheritance
     contract.
   - Applies the chosen `publish.mode` once to every child.
   - Assigns a stable idempotency key per `(batch scope, provider, target kind,
     target slug, target ref)` so rerunning the same batch does not create
     duplicate child workflows.
   - Submits via the internal Temporal execution API (`POST /api/executions`);
     `MOONMIND_URL` must point at the MoonMind API from the managed session.

3. **Record the summary**: the helper writes `artifacts/batch-workflows-result.json`
   linking every queued child workflow id together with the resolved targets,
   skips, and errors, and prints a short `queued/skipped/errors` count summary.

## Safety constraints

- Require `MOONMIND_URL` to reach the MoonMind API; the legacy direct-DB queue is
  not supported.
- Never re-select provider/model/effort in the batch form; children inherit the
  caller runtime.
- Cap the resolved list at `max_workflows`.
- Targets whose selected run capability is not auto-bindable are skipped with a
  clear `unsupported_target` reason rather than queued blindly.
