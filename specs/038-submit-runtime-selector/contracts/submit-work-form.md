# Contract: Submit Work Form Runtime Selector

## 1. Runtime selector + field visibility

| Runtime `value` | Label | Mode | Visible Fields | Hidden Fields | Redirect on success |
| --- | --- | --- | --- | --- | --- |
| `codex` (default) | `Codex worker` | worker | `instruction`, queue step editor, presets, model/effort, repo + branch inputs, publish mode, worker priority, max attempts, propose tasks | Orchestrator fields (`targetService`, `priority`, `approvalToken`) | `/tasks/queue/{jobId}` |
| `gemini` | `Gemini worker` | worker | Same as worker list | Orchestrator fields | `/tasks/queue/{jobId}` |
| `claude` | `Claude worker` | worker | Same as worker list | Orchestrator fields | `/tasks/queue/{jobId}` |
| `orchestrator` | `Orchestrator` | orchestrator | `instruction`, `targetService`, enum `priority`, optional `approvalToken` | All queue-only fields | `/tasks/orchestrator/{runId}` |

- Options originate from `system.supportedTaskRuntimes` and are displayed in config order.  
- `orchestrator` is appended client-side if missing so both legacy routes always expose it.  
- Switching runtimes must not mutate hidden-field drafts; state is restored the next time the runtime is activated.

## 2. Worker submission payload contract

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `type` | string | ✅ (`"task"`) | Envelope expected by `/api/queue/jobs`. |
| `priority` | integer | ✅ | Bound to `workerPriority` input. |
| `maxAttempts` | integer ≥ 1 | ✅ | Bound to `maxAttempts` input. |
| `payload.repository` | string | ✅ | Value from repo input or `system.defaultRepository`; must match owner/repo, https URL, or SSH URL. |
| `payload.targetRuntime` | string | ✅ | Selected worker runtime; `normalizeTaskRuntimeInput` enforces `codex|gemini|claude|…`. |
| `payload.requiredCapabilities` | array<string> | ✅ | Derived from runtime, publish mode, steps, and template capabilities without duplicates. |
| `payload.task.instructions` | string | ✅ | Shared textarea / resolved feature request text. |
| `payload.task.skill` | object | ✅ | Contains `id`, `args`, optional `requiredCapabilities`. Primary step inherits defaults when blank. |
| `payload.task.runtime` | object | ✅ | `{ mode, model?, effort? }`. Model/effort omitted only when blank. |
| `payload.task.git` | object | ✅ | `{ startingBranch, newBranch }` with `null` values trimmed before send. |
| `payload.task.publish.mode` | enum (`pr`, `branch`, `none`) | ✅ | Derived from dropdown. Additional PR metadata remains `null`. |
| `payload.task.steps` | array | optional | Only present when explicit steps exist (primary mismatch, extra steps, or template-bound steps). |
| `payload.task.appliedStepTemplates` | array | optional | Includes preset metadata when templates were applied. |
| `payload.task.proposeTasks` | boolean | ✅ | Mirrors checkbox state. |

Response: `{ id: "uuid" }` (or `jobId`) is required for redirect.

## 3. Orchestrator submission payload contract

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `instruction` | string | ✅ | Shared textarea text (trimmed). |
| `targetService` | string | ✅ | Default `"orchestrator"`, must remain non-empty. |
| `priority` | enum (`normal`, `high`) | ✅ | Normalized via `normalizePriorityChoice`; defaults to `normal`. |
| `approvalToken` | string | optional | Trimmed; omitted entirely if blank. |

- Payload is sent to `sources.orchestrator.create` (default `/orchestrator/runs`).  
- Response must expose `runId` (preferred) or `id`; otherwise form displays `Missing run ID in response.`

## 4. Validation + error handling

| Scenario | Behavior |
| --- | --- |
| Worker runtime selected, missing primary step instructions | Block submit, show `Primary step instructions are required.` inside `.queue-submit-message`. |
| Worker runtime selected, repo blank when no default configured | Block submit with guidance string about accepted formats. |
| Worker runtime selected, invalid publish mode | Block submit and list allowed values. |
| Orchestrator runtime selected, missing instruction | Display `Instruction is required for orchestrator runs.` |
| Orchestrator runtime selected, missing target service | Display `Target service is required.` |
| API error (any runtime) | Keep drafts untouched, set `.queue-submit-message` to `notice error` with `error.message || fallback`. |
| Successful response | Redirect to runtime-specific detail URL using returned identifier and stop showing the form (browser navigation). |

## 5. Navigation + legacy route behavior

- `/tasks/queue/new` loads `renderSubmitWorkPage(defaultTaskRuntime)` so worker runtime is preselected according to config.  
- `/tasks/orchestrator/new` loads `renderSubmitWorkPage("orchestrator")` so the orchestrator section is visible immediately.  
- Route guards ensure unknown preset runtime values fall back to `defaultTaskRuntime` to satisfy the “invalid query param” edge case in the spec.
