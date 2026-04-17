# Contract: Submit Work Form Runtime Selector

## 1. Runtime selector + field visibility

| Runtime `value` | Label | Mode | Visible Fields | Hidden Fields | Redirect on success |
| --- | --- | --- | --- | --- | --- |
| `codex` (default) | `Codex worker` | worker | `instruction`, queue step editor, presets, model/effort, repo + branch + publish controls, worker priority, max attempts, propose tasks | Orchestrator fields (`targetService`, `priority`, `approvalToken`) | `/tasks/queue/{jobId}` |
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
| `payload.task.git` | object | ✅ | `{ branch }` with blank values trimmed before send. New submissions must not include `targetBranch`. |
| `payload.task.publish.mode` | enum (`pr`, `branch`, `none`) | ✅ | Derived from dropdown. Additional PR metadata remains `null`. |
| `payload.task.steps` | array | optional | Only present when explicit steps exist (primary mismatch, extra steps, or template-bound steps). |
| `payload.task.appliedStepTemplates` | array | optional | Includes preset metadata when templates were applied. |
| `payload.task.proposeTasks` | boolean | ✅ | Mirrors checkbox state. |

Response: `{ id: "uuid" }` (or `jobId`) is required for redirect.

## 2.1 Steps-card repository controls

The Steps card owns the compact repository execution row:

| Control | Behavior |
| --- | --- |
| Repository | Existing repository selector/input sourced from runtime config and MoonMind APIs. |
| Branch | Replaces the old `Starting Branch` label. This is the single authored branch field. |
| Publish Mode | Rendered inline with Branch in the Steps card. `Publish Mode` remains part of submission semantics; only visual placement changes. |

Rules:

- The old `Target Branch` control is removed entirely and must not be rendered by create, edit, or rerun forms.
- Branch and Publish Mode use compact inline dropdown treatment in one row: Branch on the left, Publish Mode on the right.
- This compact row has no label above either control; the affordance label is conveyed by inline text/icon styling and accessible names.
- The controls should use Codex-like inline dropdown styling but must not copy the exact Codex screenshot iconography.
- The branch control uses a branch-like affordance with a different icon than the Codex screenshot.
- Branch options are runtime-config/API-driven, never hardcoded in the browser bundle.

## 2.2 GitHub-backed branch dropdown

Branch selection is MoonMind-owned:

- The browser talks only to MoonMind APIs, never directly to GitHub.
- Runtime config exposes the branch lookup source endpoint used by the Create page.
- The Branch dropdown is disabled until a valid repository is selected.
- After repo selection, the browser fetches branches through the configured MoonMind API.
- Loading state keeps the dropdown disabled or clearly busy.
- Empty state explains that no branches were returned for the selected repository.
- Error state explains that branch lookup failed and lets manual task creation continue when policy allows.
- When repository changes, any selected branch that is not present in the new repo's branch list is cleared or marked stale before submit.
- New submissions emit the selected value as `payload.task.git.branch`.

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
