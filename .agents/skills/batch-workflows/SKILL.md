---
name: batch-workflows
description: Provide the shared fan-out engine used by provider-specific Jira and GitHub batch presets to enqueue child workflows with inherited runtime and stable evidence.
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

# Batch Workflows Fan-out Skill

## Purpose

Provide the internal fan-out engine used by the product-facing Batch Jira
Workflows and Batch GitHub Workflows presets. Each provider-specific preset owns
its fixed source model and curated destination list; this shared skill handles
queueing, runtime inheritance, publishing, idempotency, and evidence. Every child inherits the parent runtime
(`runtimeInheritance="caller"`) and a single shared publish policy. The parent
records a summary artifact that links every queued child workflow.

This parent batch skill does not publish repository changes itself. It records
child workflow queueing evidence in `artifacts/batch-workflows-result.json`;
each queued child workflow owns its configured publish outcome.

This replaces one-off prompts like "Queue Jira Verify for every MM issue in In
Progress" with a single batch run.

## Inputs (preset inputs / skill args)

- `jira_project_key` (string, required): Jira project key, for example `MM`.
- `jira_status` (string, required): Jira status name, for example `In Progress`.
- `run_ref` (string, required): child capability to run per target. Default
  `skill:jira-verify`. The Jira preset supports `skill:jira-verify`,
  `preset:jira-implement`, and `preset:jira-orchestrate`. The separate GitHub
  preset supports `preset:github-issue-implement` and
  `preset:github-issue-orchestrate` through the same helper.
- `max_workflows` (number, optional): hard cap on queued children. Default `25`.
- `constraints` (string, optional): shared input copied to every child.
- `run_verify` (boolean, optional): shared verification toggle copied to child
  implement presets. Default `true`.
- `additional_jql` (string, optional): advanced JQL AND-clause appended to the
  project/status query.
- `issue_range` (string, GitHub preset only): inclusive `START-END` search
  criteria. Numeric members are not targets unless GitHub returns an Issue.
- `repository` (string, optional): repository override when workflow context
  cannot infer it.
- `publish_mode` (string, optional): advanced child publish override, `none`,
  `branch`, `pr`, or `pr_with_merge_automation`. Default `none`.

## Workflow

1. **Resolve provider targets** into the canonical resolved-target shape and
   write them to `artifacts/batch-workflows-targets.json` before queueing.

   For Jira, use the trusted Jira tool surface to search and preview the list:

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

   For GitHub, the inclusive number range is search criteria, not a target list.
   GitHub issues and pull requests share numbers, and numbers may be absent. Use
   the helper's `--github-issue-range` plus `--github-repository` inputs so its
   trusted GraphQL `issue(number:)` lookup returns only real Issue objects.
   Pull requests and absent numbers are omitted normally and never become
   targets. The helper writes the resolved target artifact before queueing.

2. **Queue child workflows** by running the helper:

   ```bash
   python3 "$MOONMIND_ACTIVE_SKILLS_DIR/batch-workflows/bin/batch_workflows.py" \
     --targets artifacts/batch-workflows-targets.json \
     --run-ref <curated provider-specific run ref> \
     --publish-mode <none|branch|pr|pr_with_merge_automation> \
     --constraints-file <optional path to shared constraints> \
     --run-verify | --no-run-verify \
     --update-status | --no-update-status \
     --max-workflows <cap>
   ```

   For the GitHub preset, replace `--targets` with:

   ```bash
   --github-issue-range <START-END> \
   --github-repository <owner/repository>
   ```

   For each resolved target the helper:
   - Auto-binds Jira issues into `skill:jira-verify` inputs (`jira_issue`,
     `jira_issue_key`, `repository`, `verification_mode`, `update_status`, and
     `constraints`).
   - Auto-binds Jira issues into `preset:jira-implement` inputs
     (`jira_issue`, `jira_issue_key`, `constraints`, and `run_verify`).
   - Auto-binds the same Jira inputs into `preset:jira-orchestrate`.
   - Auto-binds GitHub issue targets into the Implement and Orchestrate presets,
     including `run_verify`.
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

   If provider-specific input validation fails before a trustworthy target list
   can be written, still invoke the same helper exactly once with
   `--preflight-error <actionable message>` and
   `--requested-count <requested target count>`. The helper records the
   current execution's authoritative failure in the managed artifact spool and
   does not read targets or queue children. Do not handcraft or reuse a
   repo-local result artifact.

3. **Record the summary**: the helper writes `artifacts/batch-workflows-result.json`
   linking every queued child workflow id together with the resolved targets,
   skips, and errors, and prints a short `queued/skipped/errors` count summary.

## Safety constraints

- Require `MOONMIND_URL` to reach the MoonMind API; the legacy direct-DB queue is
  not supported.
- Never re-select provider/model/effort in the batch form; children inherit the
  caller runtime.
- Cap the resolved target list at `max_workflows`; a GitHub range's numeric
  width is not the target count.
- Targets whose selected run capability is not auto-bindable are skipped with a
  clear `unsupported_target` reason rather than queued blindly.
