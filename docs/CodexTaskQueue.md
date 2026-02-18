# Task Queue Contract and Execution Model

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-02-18

## 1. Purpose

Define how MoonMind queue jobs are submitted and executed for task automation, with Codex/Gemini/Claude compatibility and deterministic Git behavior.

This document aligns queue APIs, worker behavior, and MCP tools to the canonical Task contract in `docs/TaskArchitecture.md`.

## 2. Queue Model

### 2.1 Primary Job Type

Task submissions use:

- `type = "task"`
- `payload = CanonicalTaskPayload`

Legacy job types (`codex_exec`, `codex_skill`) remain supported during migration but are compatibility shims.

### 2.2 Statuses

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

### 3.2 MCP Tools

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
  "requiredCapabilities": ["git", "codex"],
  "targetRuntime": "codex",
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
      "mode": "codex",
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

### 4.1 Required UI Input

Only `task.instructions` is required at submit time. Other fields can be omitted and resolved safely at execution time.

### 4.2 Capability Derivation

MoonMind derives `requiredCapabilities` from payload:

- runtime capability (`codex` or `gemini` or `claude`)
- `git`
- `gh` when `publish.mode = pr`
- skill-specific capability extensions when configured

## 5. Claim and Eligibility Rules

Workers claim jobs only when:

- job type is allowed by token policy
- repository is allowed by token policy
- all required capabilities are satisfied by worker capability set

Claim ordering remains:

- priority descending
- created time ascending
- lease-based ownership and heartbeat extension

## 6. Execution Stages

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

- run runtime adapter based on task runtime mode
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
- GitHub token should come from worker environment (`GITHUB_TOKEN`) or secret reference resolution path.
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
    "requiredCapabilities": ["git", "codex"],
    "targetRuntime": "codex",
    "auth": { "repoAuthRef": null, "publishAuthRef": null },
    "task": {
      "instructions": "Run unit tests and fix regressions",
      "skill": { "id": "auto", "args": {} },
      "runtime": { "mode": "codex", "model": "gpt-5-codex", "effort": "high" },
      "git": { "startingBranch": null, "newBranch": null },
      "publish": { "mode": "branch", "prBaseBranch": null, "commitMessage": null, "prTitle": null, "prBody": null }
    }
  }
}
```

### 10.2 Claim request example

```json
{
  "workerId": "executor-01",
  "leaseSeconds": 120,
  "allowedTypes": ["task", "codex_exec", "codex_skill"],
  "workerCapabilities": ["codex", "git", "gh"]
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
- `codex_exec` and `codex_skill` remain internal compatibility shims and can be removed when producers are cut over.

## 12. Related

- `docs/TaskArchitecture.md`
- `docs/TaskUiArchitecture.md`
- `docs/UnifiedCliSingleQueueArchitecture.md`
- `docs/WorkerGitAuth.md`
- `docs/SecretStore.md`
