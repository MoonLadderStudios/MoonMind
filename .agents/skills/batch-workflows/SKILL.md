---
name: batch-workflows
description: Resolve Jira board-column or GitHub repository issue targets and enqueue one child MoonMind workflow per target using a selected child preset, inherited runtime, and a shared publish policy.
metadata:
  required-capabilities:
    - git
    - jira
    - gh
---

# Batch Workflows Skill

## Purpose

Resolve a set of issue-like targets, then queue one child MoonMind workflow per
target running a selected child preset (for example `jira-implement` or
`github-issue-implement`). Every child inherits the parent runtime
(`runtimeInheritance="caller"`) and a single shared publish policy. The parent
records a summary artifact that links every queued child workflow.

This replaces one-off prompts like "Queue a Jira Implement preset for every issue
in the In Progress column of the THOR board" with a single batch run.

## Inputs (preset inputs / skill args)

- `source_kind` (string, required): `jira_board_column` or `github_repo_issues`.
- Jira board-column source: `jira_board_id`, `jira_column`, optional
  `jira_label_filter`, `jira_issue_type_filter`, `jira_assignee_filter`, and
  `repository` (target repo for implementation children; defaults to the parent
  workflow repository when available).
- GitHub repo issues source: `github_repository`, `github_issue_state`
  (default `open`), optional `github_label_filter`, `github_assignee_filter`,
  `github_milestone_filter`, `github_search_query`.
- `target_preset_slug` / `target_preset_scope`
  (default `global`) / `target_preset_scope_ref`: the child preset to run per
  target. Known issue presets (`jira-implement`, `github-issue-implement`) are
  auto-bound; other presets must be mapped explicitly.
- `constraints` (string, optional): shared input copied to every child.
- `publish_mode` (string, required): `none`, `branch`, or `pr`.
- `max_workflows` (number, optional): hard cap on queued children. Default `25`.
- `sort` (string, optional): target ordering.

## Workflow

1. **Resolve targets** into the canonical resolved-target shape and write them to
   `artifacts/batch-workflows-targets.json`, then preview the list before queueing.
   - `jira_board_column`: use the deterministic trusted Jira tool surface to list
     the issues in the selected column of the selected board, apply the optional
     label/issue-type/assignee filters, sort, and cap at `max_workflows`. Each
     target is:

     ```json
     {
       "provider": "jira",
       "ref": "THOR-123",
       "jiraIssue": {"key": "THOR-123", "summary": "...", "description": "...",
                      "url": "...", "status": "In Progress", "assignee": "..."},
       "repository": "MoonLadderStudios/MoonMind"
     }
     ```

   - `github_repo_issues`: use the trusted GitHub tool surface (`gh issue list`)
     on the selected repository with the chosen state and optional
     label/assignee/milestone/search filters, sort, and cap at `max_workflows`.
     Each target is:

     ```json
     {
       "provider": "github",
       "ref": "MoonLadderStudios/MoonMind#123",
       "githubIssue": {"repository": "MoonLadderStudios/MoonMind", "number": 123,
                        "title": "...", "body": "...", "url": "...",
                        "state": "open", "labels": ["..."]},
       "repository": "MoonLadderStudios/MoonMind"
     }
     ```

   Never use raw Jira/GitHub credentials, web scraping, or guessed issue content to
   build the target list.

2. **Queue child workflows** by running the helper:

   ```bash
   python3 .agents/skills/batch-workflows/bin/batch_workflows.py \
     --targets artifacts/batch-workflows-targets.json \
     --target-preset-slug <slug> \
     --target-preset-scope <global|personal> \
     --target-preset-scope-ref <scope-ref-or-empty> \
     --publish-mode <none|branch|pr> \
     --constraints-file <optional path to shared constraints> \
     --max-workflows <cap>
   ```

   For each resolved target the helper:
   - Auto-binds the issue target into the child preset's issue input
     (`jira_issue` + `jira_issue_key` for `jira-implement`; `github_issue` +
     `github_issue_ref` for `github-issue-implement`) and copies the shared
     `constraints` into every child.
   - Stamps `runtimeInheritance="caller"` plus a fallback copy of the parent's
     effective runtime (mode/model/effort/provider profile) so children reuse the
     caller runtime even on deployments that do not honour the inheritance
     contract.
   - Applies the chosen `publish.mode` once to every child.
   - Assigns a stable idempotency key per `(batch scope, target ref)` so rerunning
     the same batch does not create duplicate child workflows.
   - Submits via the internal Temporal execution API (`POST /api/executions`);
     `MOONMIND_URL` must point at the MoonMind API from the managed session.

3. **Record the summary**: the helper writes `artifacts/batch-workflows-result.json`
   linking every queued child workflow id together with the resolved targets,
   skips, and errors, and prints a short `queued/skipped/errors` count summary.

## Safety constraints

- Require `MOONMIND_URL` to reach the MoonMind API; the legacy direct-DB queue is
  not supported.
- Never re-select provider/model/effort in the batch form — children inherit the
  caller runtime.
- Cap the resolved list at `max_workflows`.
- Targets whose selected preset is not auto-bindable and has no explicit mapping
  are skipped with a clear `unsupported_preset` reason rather than queued blindly.
