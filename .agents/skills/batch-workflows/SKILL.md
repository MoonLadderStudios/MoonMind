---
name: batch-workflows
description: Resolve a Jira project/status cohort and enqueue one curated Jira child workflow per issue with inherited runtime and stable evidence.
metadata:
  sideEffect:
    kind: enqueue_children
    owner: agent
    outcomeArtifact: artifacts/batch-workflows-result.json
    terminalContractId: batch_workflows_fanout.v1
    terminalSchemaVersion: moonmind.batch-workflows-result.v1
  required-capabilities:
    - git
    - jira
    - gh
---

# Batch Jira Workflows

## Purpose

Resolve Jira issues in one project/status cohort and queue one curated child
workflow per issue. Every child inherits the parent runtime
(`runtimeInheritance="caller"`) and a shared publish policy. The parent records
durable target and queue evidence; each child owns its configured publish
outcome.

This Skill is the Jira provider entrypoint. Use `batch-github-workflows` for a
GitHub issue-number range. Both entrypoints execute the same portable fan-out
engine from the resolved active Skill snapshot.

## Inputs

- `jira_project_key` (string, required): Jira project key, for example `MM`.
- `jira_status` (string, required): Jira status name, for example `In Progress`.
- `run_ref` (string, required): `skill:jira-verify`, `preset:jira-implement`, or
  `preset:jira-orchestrate`.
- `max_workflows` (number, optional): hard cap on queued children; default `25`.
- `constraints` (string, optional): shared child guidance.
- `run_verify` (boolean, optional): verification toggle for implement presets;
  default `true`.
- `additional_jql` (string, optional): JQL AND-clause appended to the fixed
  project/status query.
- `repository` (string, optional): repository override when workflow context
  cannot infer it.
- `publish_mode` (string, optional): `none`, `branch`, `pr`, or
  `pr_with_merge_automation`. The omitted default is derived from the
  selected run (`skill:jira-verify` -> `none`, implement presets -> `pr`)
  via the preset `defaultFrom` policy; the batch parent itself publishes
  nothing (`workflowPublish.mode: none`).

## Workflow

1. Use the trusted Jira tool surface to search this fixed cohort:

   ```text
   project = "<jira_project_key>" AND status = "<jira_status>"
   ```

   Append `additional_jql` only when provided. Write the result to
   `artifacts/batch-workflows-targets.json` in this canonical form:

   ```json
   {
     "provider": "jira",
     "ref": "MM-123",
     "jiraIssue": {"key": "MM-123", "summary": "...", "description": "...",
                    "url": "...", "status": "In Progress", "assignee": "..."},
     "repository": "MoonLadderStudios/MoonMind"
   }
   ```

   Never use raw Jira credentials, web scraping, or guessed issue content.

2. Invoke the Jira entrypoint exactly once:

   ```bash
   python3 "$MOONMIND_ACTIVE_SKILLS_DIR/batch-workflows/bin/batch_workflows.py" \
     --targets artifacts/batch-workflows-targets.json \
     --run-ref <curated Jira run ref> \
     --publish-mode <none|branch|pr|pr_with_merge_automation> \
     --constraints-file <optional constraints path> \
     --run-verify | --no-run-verify \
     --update-status | --no-update-status \
     --max-workflows <cap>
   ```

   The engine binds Jira issue data into the selected child, stamps
   `runtimeInheritance="caller"` plus the parent's effective runtime fallback,
   applies one publish policy, creates a stable per-target idempotency key, and
   submits through `POST /api/executions` at `MOONMIND_URL`.

   When MoonMind supplies `MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE`
   (preferred) or `MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN`, forward that scoped
   bearer on create and describe calls. A declared token file that is missing or
   empty is a hard failure; never fall back to the ambient value.

   If input validation fails before a trustworthy target list can be written,
   invoke the same entrypoint once with `--preflight-error <message>` and
   `--requested-count <count>`. Do not handcraft or reuse a result artifact.

3. Report the helper's `artifacts/batch-workflows-result.json` queued, skipped,
   and error counts honestly and link every queued child workflow.

## Security and execution constraints

- Require `MOONMIND_URL`; the legacy direct-DB queue is unsupported.
- Never re-select provider, model, or effort; children inherit the caller.
- Cap the resolved target list at `max_workflows`.
- Skip targets whose run capability cannot be auto-bound with an explicit
  `unsupported_target` reason.
