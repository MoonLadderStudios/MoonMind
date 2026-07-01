---
name: queue-moonmind-workflows
description: Queue additional MoonMind workflows from inside an existing workflow by submitting explicit `/api/executions` requests, applying stable idempotency, verifying every returned `workflowId` with the execution API, and writing a durable queue summary. Use when a workflow needs to fan out follow-up MoonMind executions, create child implementation workflows, replace local task-list or manifest-only handoffs, or prove that downstream workflows were actually queued.
---

# Queue MoonMind Workflows

## Overview

Queue child MoonMind workflows through the durable execution API and verify
their visibility before reporting success. This skill is for workflow fan-out:
creating more MoonMind executions from a running MoonMind workflow, not for
planning future work in an agent-local task list.

## Non-Negotiables

- Do not claim workflows were queued unless each successful item has a
  `workflowId` returned by `POST /api/executions` and verified by
  `GET /api/executions/{workflowId}`.
- Do not satisfy queueing requests by writing only a markdown file, a local JSON
  manifest, an in-session task list, or a proposal.
- Use `MOONMIND_URL` and the execution API. Do not write directly to the
  database, call Temporal CLI directly for creation, or infer success from
  agent stdout.
- Use stable idempotency. Prefer a parent workflow/run scope plus the child ref;
  fail when idempotency cannot be established unless the operator explicitly
  asks for non-idempotent submission.
- Keep child request bodies compact. Large source briefs, story files, logs, or
  generated context must be artifact refs or issue refs, not embedded blobs.

## Manifest

Write a manifest containing one complete `/api/executions` request per child:

```json
{
  "batchScope": "mm:parent-workflow-or-run",
  "workflows": [
    {
      "ref": "github:MoonLadderStudios/MoonMind#722",
      "request": {
        "type": "task",
        "priority": 0,
        "maxAttempts": 3,
        "payload": {
          "repository": "MoonLadderStudios/MoonMind",
          "runtimeInheritance": "caller",
          "requiredCapabilities": ["git", "gh"],
          "task": {
            "title": "Implement GitHub issue #722",
            "instructions": "Implement GitHub issue MoonLadderStudios/MoonMind#722.",
            "skill": {"name": "github-issue-implement"},
            "inputs": {
              "github_issue_ref": "MoonLadderStudios/MoonMind#722"
            },
            "publish": {"mode": "pr"}
          }
        }
      }
    }
  ]
}
```

The helper supports both execution API shapes:

- task-shaped wrapper: `{"type": "task", "payload": {...}}`
- direct execution create body: `{"workflowType": "MoonMind.UserWorkflow", ...}`

For task-shaped requests, idempotency is stored at `request.payload.idempotencyKey`.
For direct create requests, idempotency is stored at `request.idempotencyKey`.

## Workflow

1. Resolve the child targets and write the manifest to a managed artifact path,
   usually `artifacts/queue-moonmind-workflows-manifest.json`.
2. Run the helper:

   ```bash
   python3 .agents/skills/queue-moonmind-workflows/scripts/queue_moonmind_workflows.py \
     --manifest artifacts/queue-moonmind-workflows-manifest.json \
     --max-workflows 25
   ```

3. Read the helper output. A successful queue has:
   - `submitted > 0`
   - `verified == submitted`
   - `errors == []`
   - one `workflowId` per queued item
4. Report queued workflow IDs and the summary artifact path. If any item failed
   submission or verification, report the run as failed/partial and include the
   specific failing `ref` values.

## Helper Behavior

`scripts/queue_moonmind_workflows.py`:

- requires `MOONMIND_URL`;
- forwards supported API auth from `MOONMIND_AUTH_HEADER`,
  `MOONMIND_API_TOKEN`, `MOONMIND_AUTH_TOKEN`, `MOONMIND_BEARER_TOKEN`, or
  `MOONMIND_API_KEY` when present, and also forwards
  `MOONMIND_WORKER_TOKEN` or `MOONMIND_WORKER_TOKEN_FILE` for deployments that
  still accept the legacy worker header;
- forwards parent workflow/agent headers from `MOONMIND_TASK_WORKFLOW_ID`,
  `MOONMIND_WORKFLOW_ID`, `TEMPORAL_WORKFLOW_ID`, `MOONMIND_AGENT_RUN_ID`,
  `MOONMIND_RUN_ID`, or `AGENT_RUN_ID`;
- derives idempotency keys from `batchScope`, child `ref`, and the request body;
- submits each child with `POST /api/executions`;
- verifies each returned `workflowId` with bounded retries against
  `GET /api/executions/{workflowId}`;
- writes `queue-moonmind-workflows-result.json` under the managed session
  artifact spool when available, otherwise under `--artifacts-dir`;
- exits non-zero when submission or verification errors occur.

Use `--dry-run` to validate and summarize the manifest without submitting. Use
`--allow-empty` only when a no-op is an acceptable outcome.

## Result Standard

Treat these as failures:

- `MOONMIND_URL` is missing.
- The manifest has zero workflows and `--allow-empty` was not set.
- The manifest exceeds `--max-workflows`; cap skips are reported as partial
  failures instead of silent success.
- Any child request lacks a stable idempotency key and the helper cannot derive
  one from parent scope.
- A `POST /api/executions` response lacks `workflowId`.
- A returned `workflowId` cannot be fetched through `GET /api/executions/{id}`
  within the retry window.
- The helper exits non-zero.

When queueing from an already-running workflow, prefer
`runtimeInheritance: "caller"` in the child payload and include a fallback
runtime only when the parent task context supplies one. Do not silently switch
model, effort, provider profile, repository, publish mode, or credentials.
