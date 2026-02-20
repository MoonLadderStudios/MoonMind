# Task Queue System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-02-19

## 1. Purpose

Define how MoonMind queue jobs are submitted and executed for task automation in runtime-neutral terms.

This document covers the canonical Task queue contract used across Codex, Gemini, and Claude worker runtimes, including shared API surface, stage behavior, and publish semantics.

## 2. Queue Model

### 2.1 Primary Job Type

Task submissions use:

- `type = "task"`
- `payload = CanonicalTaskPayload`

Legacy job types (currently `codex_exec`, `codex_skill`) remain supported during migration but are compatibility shims.

### 2.2 Runtime-Neutral Contract

The queue model is shared across runtimes:

- one canonical payload shape
- one claim lifecycle (`queued -> running -> terminal`)
- one wrapper stage plan (`prepare -> execute -> publish`)
- runtime-specific behavior isolated to the `execute` adapter path

### 2.3 Statuses

Queue status model:

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`
- `dead_letter`

## 3. API Surface

### 3.1 REST

- `POST /api/queue/jobs`
- `GET /api/queue/jobs`
- `GET /api/queue/jobs/{jobId}`
- `POST /api/queue/jobs/claim`
- `POST /api/queue/jobs/{jobId}/heartbeat`
- `POST /api/queue/jobs/{jobId}/complete`
- `POST /api/queue/jobs/{jobId}/fail`
- `POST /api/queue/jobs/{jobId}/events`
- `GET /api/queue/jobs/{jobId}/events`
- `GET /api/queue/jobs/{jobId}/events/stream`
- `POST /api/queue/jobs/{jobId}/artifacts/upload`
- `GET /api/queue/jobs/{jobId}/artifacts`
- `GET /api/queue/jobs/{jobId}/artifacts/{artifactId}/download`

### 3.2 Task Preset Catalog REST

Task preset APIs provide reusable template expansion for queue submissions:

- `GET /api/task-step-templates`
- `POST /api/task-step-templates`
- `GET /api/task-step-templates/{slug}`
- `GET /api/task-step-templates/{slug}/versions/{version}`
- `POST /api/task-step-templates/{slug}:expand`
- `POST /api/task-step-templates/save-from-task`
- `POST /api/task-step-templates/{slug}:favorite`
- `DELETE /api/task-step-templates/{slug}:favorite`

Disable flag (optional):

- `FEATURE_FLAGS__DISABLE_TASK_TEMPLATE_CATALOG=1` to disable (legacy fallback: `DISABLE_TASK_TEMPLATE_CATALOG=1`)

CLI helper usage is available via `moonmind.agents.cli.task_templates.TaskTemplateClient`.
Example:

```python
from moonmind.agents.cli.task_templates import TaskTemplateClient, merge_expanded_steps

client = TaskTemplateClient(base_url="http://localhost:8000", token="<api-token>")
items = client.list_templates(scope="global", search="pr")
expanded = client.expand_template(
    slug=items[0]["slug"],
    scope="global",
    version=items[0]["latestVersion"],
    inputs={"change_summary": "Fix flaky tests"},
)
steps = merge_expanded_steps(existing_steps=[], expanded_steps=expanded["steps"], mode="append")
```

### 3.3 MCP Tools

- `queue.enqueue`
- `queue.claim`
- `queue.heartbeat`
- `queue.complete`
- `queue.fail`
- `queue.get`
- `queue.list`
- `queue.upload_artifact`

MCP tools map to the same queue service methods used by REST.

## 4. Canonical Task Payload

Top-level policy fields plus nested task execution fields:

```json
{
  "repository": "owner/repo",
  "requiredCapabilities": ["git", "claude"],
  "targetRuntime": "claude",
  "auth": {
    "repoAuthRef": null,
    "publishAuthRef": null
  },
  "task": {
    "instructions": "Implement feature and run tests",
    "skill": {
      "id": "auto",
      "args": {}
    },
    "runtime": {
      "mode": "claude",
      "model": null,
      "effort": null
    },
    "git": {
      "startingBranch": null,
      "newBranch": null
    },
    "publish": {
      "mode": "branch",
      "prBaseBranch": null,
      "commitMessage": null,
      "prTitle": null,
      "prBody": null
    }
  }
}
```

`targetRuntime` and `task.runtime.mode` select runtime adapter intent (`codex`, `gemini`, `claude`).

### 4.1 Required UI Input

Only `task.instructions` is required at submit time. Other fields can be omitted and resolved safely at execution time.

### 4.2 Capability Derivation

MoonMind derives `requiredCapabilities` from payload:

- runtime capability (`codex` or `gemini` or `claude`)
- `git`
- `gh` when `publish.mode = pr`
- skill-specific capability extensions when configured

### 4.3 Publish Overrides and Producer Guidance

`task.publish` fields are explicit producer-controlled overrides:

- `commitMessage`
- `prTitle`
- `prBody`

Producer best practice:

- keep `task.instructions` required
- make `prTitle` and `prBody` optional but user-editable in typed submit flows
- auto-suggest `prTitle` from task intent and send it when available
- rely on system defaults only when explicit overrides are omitted

### 4.4 Proposal Policy and Target Routing

Tasks may optionally embed a `task.proposalPolicy` object so workers can steer follow-up proposals without changing global settings. The object accepts:

- `targets`: ordered subset of `["project", "moonmind"]`. Omitted or empty lists fall back to `MOONMIND_PROPOSAL_TARGETS` (default `project`).
- `maxItems`: per-target caps (e.g., `{ "project": 3, "moonmind": 2 }`). Invalid, missing, or non-positive values revert to documented defaults.
- `minSeverityForMoonMind`: severity floor (`low`, `medium`, `high`, `critical`) that must be met before emitting MoonMind CI proposals. Defaults to `high` when unspecified.

Global defaults surface through `TaskProposalSettings`/`SpecWorkflowSettings` (see `api_service/config.template.toml`) so API validation, worker routing, and dashboards all share the same knobs.

## 5. Claim and Eligibility Rules

Workers claim jobs only when:

- job type is allowed by worker policy
- repository is allowed by worker policy
- all required capabilities are satisfied by worker capability set

Claim ordering remains:

- priority descending
- created time ascending
- lease-based ownership and heartbeat extension

## 6. Execution Stage Model

Each `type="task"` job executes as wrapper stages:

1. `moonmind.task.prepare`
2. `moonmind.task.execute`
3. `moonmind.task.publish` (if `publish.mode != none`)

### 6.1 Prepare Stage

Prepare stage responsibilities:

- build workspace: `repo/`, `home/`, `skills_active/`, `artifacts/`
- create skill links:
  - `.agents/skills -> skills_active`
  - `.gemini/skills -> skills_active`
- checkout repository
- resolve default branch
- resolve effective working branch
- materialize selected skills (if any)
- emit `task_context.json`

### 6.2 Execute Stage

Execute stage responsibilities:

- run runtime adapter based on task runtime mode (`codex`, `gemini`, or `claude`)
- pass instructions, workspace, skill links, and selected skill id
- collect runtime logs
- emit patch artifact (`patches/changes.patch`) when changes exist

### 6.3 Publish Stage

Publish stage responsibilities:

- `none`: skip commit/push
- `branch`: commit and push to effective working branch
- `pr`: commit/push and create PR with:
  - base = `publish.prBaseBranch` when set, else resolved starting branch
  - head = effective working branch

Publish stage owns final git operations and default commit/PR text generation.

### 6.4 PR Text Generation and Correlation Best Practices

When `publish.mode = pr`, apply the following behavior:

1. Commit message:
   - if `publish.commitMessage` is set, use it verbatim
   - otherwise generate a deterministic default
2. PR title:
   - if `publish.prTitle` is set, use it verbatim
   - otherwise derive a descriptive title from task intent:
     - first non-empty step title (when steps exist)
     - else first sentence/line of `task.instructions`
     - else fallback default
   - keep title concise for list readability (target ~70-90 chars)
   - optional: append short correlation token (`[mm:<jobId8>]`)
   - avoid full UUIDs in title text
3. PR body:
   - if `publish.prBody` is set, use it verbatim
   - otherwise generate a default body with a summary plus metadata footer
   - include full job UUID in the body metadata as source-of-truth correlation

Recommended metadata footer template:

```md
---
<!-- moonmind:begin -->
MoonMind Job: <job-uuid>
Runtime: <codex|gemini|claude>
Base: <base-branch>
Head: <head-branch>
<!-- moonmind:end -->
```

Metadata footer requirements:

- machine-parseable, stable keys
- no secrets or token-like values
- include enough context to reconcile queue job records with PR records

## 7. Branch Semantics

At execution time:

- `defaultBranch` is resolved from repository metadata
- `startingBranch` defaults to `defaultBranch` when omitted
- `newBranch` defaults to:
  - `null` if `startingBranch != defaultBranch`
  - otherwise auto-generated

If auto-generated, recommended branch format:

```text
task/<YYYYMMDD>/<jobId8>
```

Workers should use deterministic branch generation (for example shared utility helpers) to keep behavior stable across runtimes.

## 8. Observability and Artifacts

Required events:

- stage start/finish
- resolved defaults (default branch, effective branch)
- publish outcomes (branch push, PR URL, no-change skip)
- non-secret failure summaries
- live log chunk events (`payload.kind = "log"`) with stream metadata (`stdout`/`stderr`)

Queue event consumers support both:

- incremental polling via `GET /api/queue/jobs/{jobId}/events?after=...`
- SSE streaming via `GET /api/queue/jobs/{jobId}/events/stream`

Required artifacts:

- `logs/prepare.log`
- `logs/execute.log`
- `logs/publish.log` (when publish enabled)
- `patches/changes.patch` (when applicable)
- `task_context.json`
- `publish_result.json` (when publish enabled)

## 9. Security

- No raw secrets in queue payloads, events, or artifacts.
- Worker auth uses worker token or OIDC worker identity.
- Publish credentials should come from worker environment or secret-reference resolution paths (GitHub integration currently uses `GITHUB_TOKEN`).
- Token-like strings must be redacted in logs/artifacts.

## 10. Producer and Worker Examples

### 10.1 REST enqueue example

```json
{
  "type": "task",
  "priority": 10,
  "maxAttempts": 3,
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "requiredCapabilities": ["git", "gh", "gemini"],
    "targetRuntime": "gemini",
    "auth": { "repoAuthRef": null, "publishAuthRef": null },
    "task": {
      "instructions": "Run unit tests and fix regressions",
      "skill": { "id": "auto", "args": {} },
      "runtime": { "mode": "gemini", "model": "gemini-2.5-pro", "effort": "high" },
      "git": { "startingBranch": null, "newBranch": null },
      "publish": { "mode": "branch", "prBaseBranch": null, "commitMessage": null, "prTitle": null, "prBody": null }
    }
  }
}
```

### 10.2 Claim request examples

Runtime-specific worker:

```json
{
  "workerId": "executor-gemini-01",
  "leaseSeconds": 120,
  "allowedTypes": ["task", "codex_exec", "codex_skill"],
  "workerCapabilities": ["gemini", "git", "gh"]
}
```

Universal worker:

```json
{
  "workerId": "executor-universal-01",
  "leaseSeconds": 120,
  "allowedTypes": ["task", "codex_exec", "codex_skill"],
  "workerCapabilities": ["codex", "gemini", "claude", "git", "gh"]
}
```

## 11. Migration and Compatibility

### 11.1 Compatibility Window

During migration, queue workers may accept:

- `type="task"` (preferred)
- `type="codex_exec"` (legacy)
- `type="codex_skill"` (legacy)

Legacy handlers should map into the same internal stage plan (`prepare -> execute -> publish`) to prevent behavior drift.

### 11.2 End State

Steady state after migration:

- UI submits only typed `type="task"` jobs.
- Legacy compatibility shims can be removed when producers are fully cut over.

## 12. Related

- `docs/TaskArchitecture.md`
- `docs/TaskUiArchitecture.md`
- `docs/UnifiedCliSingleQueueArchitecture.md`
- `docs/WorkerGitAuth.md`
- `docs/SecretStore.md`
