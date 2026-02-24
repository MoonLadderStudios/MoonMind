# Data Model: Submit Runtime Selector

## 1. `SubmitTargetOption`

| Field | Type | Source | Notes |
| --- | --- | --- | --- |
| `id` | string (`codex`, `gemini`, `claude`, `orchestrator`) | Derived from `system.supportedTaskRuntimes` plus appended UI entry | Drives `<option value>` for the runtime selector and is fed into submission routing. |
| `label` | string | `id === "orchestrator" ? "Orchestrator" : "{Capitalized} worker"` | Keeps UX copy consistent even if config adds new worker runtimes. |
| `mode` | enum (`worker`, `orchestrator`) | Computed: `id === "orchestrator" ? "orchestrator" : "worker"` | Determines which field group is visible and which draft object to bind. |
| `endpoint` | string URL | `sources.queue.create` or `sources.orchestrator.create` | Stored separately so submission logic never recomputes config fallbacks. |
| `isDefault` | boolean | Compare against `defaultTaskRuntime` or preset runtime param | Preselects runtime when the page mounts or legacy routes deep-link. |

## 2. `SubmitDraftController`

| Field | Type | Notes |
| --- | --- | --- |
| `workerDraft` | `WorkerSubmitDraft` | Stores the last-saved worker form state. Controller returns deep clones so DOM updates cannot mutate stored drafts. |
| `orchestratorDraft` | `OrchestratorSubmitDraft` | Same behavior for orchestrator-only inputs. |

**Methods**
- `saveWorker(draft)` / `saveOrchestrator(draft)`: Replace the stored draft (deep-cloned).  
- `loadWorker()` / `loadOrchestrator()`: Return clones for rehydration.  
- Internally uses `cloneSubmitDraft` to support nested arrays (step state, template metadata).

## 3. `WorkerSubmitDraft`

| Field | Type | Purpose |
| --- | --- | --- |
| `instruction` | string | Mirrors the shared textarea and primary step instructions. |
| `steps` | array of `StepStateEntry` | Full queue step editor state. Primary step stores `instruction` + skill metadata. |
| `appliedTemplateState` | array | Captures preset bindings (`slug`, `version`, `inputs`, `stepIds`, `capabilities`). |
| `templateFeatureRequest` | string | Cached “Feature Request” text area inside presets card. |
| `model` / `effort` | string | Per-runtime overrides; defaulted from runtime config when untouched. |
| `repository` | string | `owner/repo` style, defaulting to `system.defaultRepository`. |
| `startingBranch` / `newBranch` | string | Git branch overrides. |
| `publishMode` | enum (`pr`, `branch`, `none`) | Queue publish strategy. |
| `workerPriority` | stringified int | Bound to `<input type="number" name="workerPriority">`. |
| `maxAttempts` | stringified int | Bound to `<input name="maxAttempts">`. |
| `proposeTasks` | boolean | Checkbox state. |
| `selectedTemplateKey` | string | Which preset is highlighted in the select element.

`StepStateEntry` mirrors the queue editor structure (`id`, `instructions`, `skillId`, `skillArgs`, `skillRequiredCapabilities`, template metadata). Only populated indices are serialized back into the queue payload.

## 4. `OrchestratorSubmitDraft`

| Field | Type | Purpose |
| --- | --- | --- |
| `instruction` | string | Mirrors the shared textarea but is independent from worker drafts. |
| `targetService` | string | Defaults to `"orchestrator"`; required on submit. |
| `priority` | enum (`normal`, `high`) | Dropdown value normalized via `normalizePriorityChoice`. |
| `approvalToken` | string | Optional secret needed for protected workflows. |

## 5. Submission Payloads

### `QueueSubmissionPayload`

```json
{
  "type": "task",
  "priority": <int>,
  "maxAttempts": <int>,
  "payload": {
    "repository": "owner/repo",
    "requiredCapabilities": ["codex", "git", ...],
    "targetRuntime": "codex",
    "task": {
      "instructions": "Objective text",
      "skill": { "id": "auto", "args": { ... }, "requiredCapabilities": ["git"] },
      "proposeTasks": true,
      "runtime": { "mode": "codex", "model": "gpt-4.1", "effort": "standard" },
      "git": { "startingBranch": "main", "newBranch": "feat" },
      "publish": { "mode": "pr", "prBaseBranch": null, ... },
      "steps": [ { "id": "step-1", "instructions": "..." }, ... ],
      "appliedStepTemplates": [ … ]
    }
  }
}
```

- `priority` / `maxAttempts` live at the job wrapper level.  
- `requiredCapabilities` is computed from runtime, publish mode, step skills, and applied templates.  
- Optional sections (`steps`, `appliedStepTemplates`) appear only when populated to preserve payload size.

### `OrchestratorRunPayload`

```json
{
  "instruction": "Ship release",
  "targetService": "deploy",
  "priority": "normal",
  "approvalToken": "optional-token"
}
```

- `priority` is always lowercase `normal|high`.  
- `approvalToken` is omitted entirely when blank.  
- Response must include a `runId` (or `id`) so the UI redirects to `/tasks/orchestrator/:runId`.

## 6. View State Relationships

| Relationship | Description |
| --- | --- |
| `SubmitTargetOption` → `SubmitDraftController` | Each runtime option maps to one of the controller’s draft buckets; switching runtime calls `save*` on the current bucket, updates `activeRuntime`, and calls `load*` for the target. |
| Shared `instruction` textarea ↔ drafts | When runtime is worker, textarea updates both the step editor’s primary entry and `workerDraft.instruction`. When runtime is orchestrator, it only updates `orchestratorDraft.instruction`. |
| Submission payload ↔ draft | Worker payload fields serialize directly from the worker draft plus transient calculations (required capabilities, resolved instructions). Orchestrator payload is the normalized orchestrator draft. |

These structures guarantee runtime-specific fields never bleed into the wrong payload while keeping the UX responsive.
