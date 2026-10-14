# Workflow Console Architecture

**Document Class:** Module Architecture View
Owners: MoonMind Engineering

## 1. Purpose

This document describes the architecture of the MoonMind dashboard: route model, source model, runtime config, Temporal integration, action mapping, artifact flows, and workflow-oriented presentation.

The dashboard is MoonMind's **Workflow Execution console**. The canonical product entity is the **Workflow Execution**, identified by `workflowId`, as defined in `docs/Temporal/WorkflowExecutionProductModel.md`. Detailed backend contracts live in the Temporal docs; this document describes the UI architecture that consumes them.

---

## 2. Related docs

- `docs/Temporal/WorkflowExecutionProductModel.md` — product model and terminology
- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/StepLedgerAndProgressModel.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Observability/LiveLogs.md` — artifact-first logs, observability APIs, SSE live follow, and the log viewer
- `docs/Steps/SkillSystem.md`
- `docs/UI/WorkflowsListPage.md` — Workflows List columns, filters, sorting, and URL state
- `docs/UI/DashboardDesignSystem.md`
- `docs/UI/WorkflowWorkspaceSidebar.md` — desktop workspace shell, sidebar navigation, list-context preservation, and mobile detail presentation
- `docs/UI/TypeScriptSystem.md`

---

## 3. Product stance

The dashboard is the operator console for MoonMind Workflow Executions. It presents work as **Workflow Executions** (shortened to **workflows** in UI copy), uses Temporal-backed executions as the durable source of truth, keeps provider/runtime internals out of the main scanning experience, and exposes exact execution state without requiring raw Temporal or provider-native terms outside advanced/debug surfaces.

Key distinctions:

- `workflowId` is the stable product identity and route key; `runId` is the current/latest Temporal run instance.
- **Agent skills** are instruction bundles, not runtimes.
- **Temporal** is the orchestration substrate, not a selectable runtime.
- Unqualified MoonMind work is never called a "task". That word is reserved for Temporal internals (Temporal Task, Workflow Task, Activity Task, Task Queue) and qualified external systems (Jira task, Codex provider task), so it never blurs product and substrate concepts.

---

## 4. Implementation overview

The dashboard is a **server-hosted React/Vite UI**:

- FastAPI serves the HTML shell and owns canonical routes; it can optionally load page modules from a Vite dev server during local development.
- One Vite-built React entrypoint selects the requested page module from the boot payload.
- Runtime config is generated server-side.
- REST APIs are the only browser/backend boundary.

Main pieces:

- HTML shell: `api_service/templates/react_dashboard.html`
- shared entrypoint: `frontend/src/entrypoints/dashboard.tsx`
- lazy-loaded page modules: `frontend/src/entrypoints/*.tsx` (for example `workflow-list.tsx`, `workflow-start.tsx`, `workflow-detail.tsx`, `schedules.tsx`, `skills.tsx`, `settings.tsx`, `oauth-terminal.tsx`)
- shared stylesheet source: `frontend/src/styles/dashboard.css`
- Vite build output (JS and CSS): `api_service/static/workflow_console/dist/`
- runtime config builder: `api_service/api/routers/workflow_console_view_model.py`
- route shell: `api_service/api/routers/workflow_console.py`

---

## 5. Static assets and CSS

### 5.1 Tailwind content

Tailwind (`tailwind.config.cjs`) scans `api_service/templates/react_dashboard.html` and `frontend/src/**/*.{js,jsx,ts,tsx}`. React source must be scanned directly because production-aligned builds generate CSS before Vite output exists; scanning only `dist` would produce incomplete CSS in Docker builds.

If a React route renders structurally correct HTML but spacing, grids, or layout styling are missing, the likely cause is stale or incomplete Vite-emitted CSS, usually because Tailwind did not scan the relevant source files.

### 5.2 Generated outputs

The dashboard relies on two generated outputs:

- Vite `dist/` bundles (JS plus CSS extracted from `frontend/src/styles/dashboard.css`). This is runtime build output, built from source in local verification, CI, and Docker, and not committed.
- `frontend/src/generated/openapi.ts`, the checked-in API types.

`npm run ui:build:check` rebuilds `dist/` and verifies the manifest. `npm run generate` regenerates `openapi.ts`. `npm run contracts:check` is the CI drift check that keeps `openapi.ts` in sync with backend schema sources; generation writes its intermediate schema to a temporary file rather than a tracked `openapi.json`.

---

## 6. Route map

| Route | Purpose |
| --- | --- |
| `/workflows` | Workflow list |
| `/workflows/new` | Start Workflow page |
| `/workflows/{workflowId}` | Workflow detail |
| `/workflows/{workflowId}/{chat\|overview\|execution\|evidence\|debug}` | Workflow detail tabs |
| `/schedules`, `/schedules/{definitionId}` | Recurring schedule list and detail |
| `/skills` | Agent skill management |
| `/settings/*` | Configuration pages (see [SettingsPage.md](./SettingsPage.md)) |
| `/oauth-terminal` | OAuth terminal sessions (the only place xterm.js is used) |
| `/artifacts`, `/remediations`, `/omnigent/agents`, `/omnigent/policies` | Supporting console pages |

The detail subroutes `steps` and `runs` are aliases for `execution`, and `artifacts` is an alias for `evidence` (`frontend/src/lib/workflowDetailRoutes.ts`). `/secrets` redirects to `/settings/providers-secrets` and `/workers` to `/settings/operations`.

- `/workflows` and `/workflows/{workflowId}` are the primary product routes. `/tasks/*` routes are not in the route table and are not redirected.
- Unknown console routes return a 404 with the `dashboard_route_not_found` detail naming the canonical `/workflows*` routes.
- There are no Manifest pages; `/manifests/*` resolves to no route.
- Detail routing does not require users to understand source-specific route families.

---

## 7. Source model

The main workflow list/detail flow is a **Temporal-native workflow surface**. Browser clients never talk directly to Temporal Server or Temporal Web UI; they always go through MoonMind REST APIs, which own authorization and the product projection.

Runtime config exposes sources such as `schedules`, `temporal`, and a MoonMind-owned GitHub branch lookup. The Start Workflow branch selector fetches branches for the selected repository through that configured MoonMind source, never GitHub directly.

`source=temporal` is an implementation/debug hint, not a user concept. The UI does not spend primary space on a `Source` filter or a constant `Type = Temporal` column. Normal workflow pages are scoped to the authenticated principal and do not expose arbitrary owner pickers.

### 7.1 Temporal is not a runtime

Temporal is orchestration, not a worker runtime. `temporal` is not a runtime option, runtime selection never stands for execution engine or skill selection, skill sets are not presented as workflow source types, and runtime choice and agent-skill choice are separate UX concepts.

---

## 8. Information hierarchy

### 8.1 List pages are for scanning

The list shows the minimum fields needed to compare rows quickly (title, status, progress, repository, Provider Profile, updated time, compact workflow ID); deeper metadata lives in detail. [WorkflowsListPage.md](./WorkflowsListPage.md) describes the columns, filters, sorting, pagination, and URL state.

### 8.2 Detail pages are for evidence and control

Detail is where operators find exact execution state, step ledger state, workflow and run IDs, namespace, repository, integration, owner, waiting reason, attention requirement, execution-context metadata, step evidence and checks, artifact evidence, managed-run observability (artifact-backed logs and diagnostics, not terminal embeds), and actions.

Advanced metadata such as exact `temporalStatus`, `closeStatus`, `rawState`, provider-specific metadata, and the latest run ID belong in a facts rail, metadata drawer, or the Debug tab rather than dominating the normal layout.

### 8.3 Preset provenance

Preset provenance is explanatory metadata, not an execution model. Detail may show **Manual**, **Preset**, and **Preset path** chips and provenance summaries; edit/rerun reconstruction may show preserved preset bindings, detached state, and include paths from the submitted input snapshot; list rows may show compact preset context only when it does not crowd out core fields. Flat steps remain the execution ordering model. When provenance is unavailable, stale, or policy-hidden, the UI falls back to flat steps without implying missing work, and preset grouping never implies nested runtime behavior, sub-plans, or separate runs.

---

## 9. Runtime config

### 9.1 `sources.temporal`

The runtime config exposes MoonMind API endpoint templates (defined in `moonmind/config/settings.py`):

```json
{
  "sources": {
    "temporal": {
      "list": "/api/executions",
      "create": "/api/executions",
      "detail": "/api/executions/{workflowId}",
      "steps": "/api/executions/{workflowId}/steps",
      "update": "/api/executions/{workflowId}/update",
      "signal": "/api/executions/{workflowId}/signal",
      "cancel": "/api/executions/{workflowId}/cancel",
      "artifacts": "/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts",
      "artifactCreate": "/api/artifacts",
      "artifactMetadata": "/api/artifacts/{artifactId}",
      "artifactPresignDownload": "/api/artifacts/{artifactId}/presign-download",
      "artifactDownload": "/api/artifacts/{artifactId}/download"
    }
  }
}
```

The `{temporalRunId}` token in the artifacts template is the run instance ID. Clients use these MoonMind endpoints only; they do not construct Temporal calls or embed knowledge of queue families or worker topology.

### 9.2 Status fields

Temporal-backed rows and details carry `dashboardStatus` for compatibility grouping, `rawState` for exact MoonMind state, `temporalStatus` and `closeStatus` for advanced views, and `waitingReason` and `attentionRequired` for blocked states. The client may keep a fallback compatibility state map, but prefers the server's normalized fields.

### 9.3 Feature flags

Feature flags in the boot payload gate areas such as Temporal list/detail, actions, submit flows, debug fields, agent skill selection and detail, scheduling on submit, and log streaming. For example, the list is disabled when `features.temporalDashboard.listEnabled` is false, and `logStreamingEnabled` (default `true`, from `MOONMIND_LOG_STREAMING_ENABLED`) is the operator-visible switch for live log follow.

---

## 10. Detail route and data loading

### 10.1 Route identity

The canonical detail route is `/workflows/{workflowId}`. Because `workflowId` is the durable identity, new runs and Continue-As-New do not change the route. Source resolution prefers the server-side source mapping and execution index, then an explicit `?source=temporal` override, with client heuristics only as a fallback.

### 10.2 Detail fetch sequence

1. `GET /api/executions/{workflowId}`
2. `GET /api/executions/{workflowId}/steps`
3. `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`, using the latest run ID from the detail response
4. per-artifact metadata or download requests as the operator expands or downloads artifacts

Steps load before generic artifact browsing because the step ledger is the primary comprehension surface.

### 10.3 Observability fetch sequence

The Live Logs panel loads in this order:

1. `GET /api/agent-runs/{id}/observability-summary` — run status, artifact refs, live stream availability, and the freshest managed-session snapshot when present.
2. `GET /api/agent-runs/{id}/logs/merged` — the initial merged tail, which is visible before any SSE connection is attempted.
3. If the run is active and `supports_live_streaming` is true, attach to `GET /api/agent-runs/{id}/logs/stream`.
4. If the stream fails or is unavailable, stay in artifact-backed mode rather than blanking the panel.

Ended runs skip step 3 entirely. For managed sessions, `activeTurnId` and related header fields come from the managed-session record when it is fresher than the run record. Loading the artifact tail first means operators always see durable content, and the live stream is only an enhancement on top of it.

---

## 11. Detail page architecture

### 11.1 Layout

The detail page has a summary header (title, normalized status badge, concise summary, allowed actions), a compact facts row (workflow label, runtime, model/effort when applicable, start and updated times, current run state), a secondary facts rail (workflow ID, latest run ID, namespace, repository, integration, owner, terminal timestamps, execution-context metadata), and durable evidence (steps, step-scoped logs, diagnostics and checks, artifacts, outcome summaries, timeline). These are organized into the Chat, Overview, Execution, Evidence, and Debug tabs.

### 11.2 Steps

Steps appear above the timeline and the generic artifact listing. Each step row shows number and title, exact status, short summary, timing, execution/attempt count, dependency blockers, and quick evidence links. Expanded rows group **Summary**, **Checks**, **Logs & Diagnostics**, **Artifacts**, and **Metadata**.

- The default view is the latest/current run, as a single flat ordered list with inline dependency metadata. There is no separate always-visible DAG for the same ledger.
- The plan artifact is the planned-step source; the step-ledger API is the live-state source.
- Step rows may carry `childWorkflowId`, `childRunId`, and `agentRunId`. A step with an `agentRunId` embeds or links the `/api/agent-runs/*` observability surfaces in its Logs & Diagnostics area.
- The client never infers step completion or "latest output" by parsing logs or sorting raw artifacts, because the server-side ledger is authoritative.
- Preset chips explain provenance only and do not change order, dependencies, or completion rules.

### 11.3 Observability

Managed-run evidence is organized as **Live Logs** (merged stream, artifact-backed by default, upgrading to live follow when active), **Stdout** and **Stderr** (artifact-backed viewers with tail, download, copy, and line-wrap), **Diagnostics** (exit code, failure class, duration, artifact refs, parsed errors), and **Artifacts**. This area is artifact-backed and MoonMind-native and does not embed terminals; xterm.js is reserved for OAuth sessions. Steps remain the primary comprehension surface.

### 11.4 Log viewer states

| State | Meaning |
| --- | --- |
| `not_available` | No artifacts and no live stream; the run may be pre-launch |
| `starting` | Summary or initial tail is loading |
| `live` | Connected to an active stream |
| `ended` | Run is terminal; final artifact-backed tail only |
| `error` | Stream failed; artifact-backed content is shown |

Transitions: `not_available` → `starting`; `starting` → `live`, `ended`, or `error`; `live` → `ended` or `error`; `error` → `live` (reconnect while still active) or `ended`.

Connections follow panel visibility. A collapsed panel holds no connection and collapsing disconnects immediately. An open panel on an active run fetches summary, then tail, then connects; on an ended run it never connects. A background tab disconnects or pauses and reconnects on foreground only if the panel is open and the run is active. Stream errors never erase visible logs, and completed runs are fully inspectable without a live connection. When `supports_live_streaming` is false, no stream is attempted.

Every log record carries a `stream` field (`stdout`, `stderr`, or `system` for supervisor annotations, reconnect notices, and truncation warnings), which the merged view needs to render lines correctly.

### 11.5 Timeline and waiting states

The timeline is synthesized from execution state, step-ledger transitions, waiting metadata, notable action results, artifact evidence, finalization, and outcome summaries. Raw Temporal event history browsing is not offered without a dedicated backend contract.

A blocked execution shows its exact `rawState`, `waitingReason`, and whether `attentionRequired` is true, and does not imply that the operator must act when the block is merely external, provider, or system waiting.

---

## 12. Action mapping

### 12.1 Actions

| Console action | API | Contract |
| --- | --- | --- |
| Start workflow | `POST /api/executions` | Start workflow |
| Edit inputs | `POST /api/executions/{workflowId}/update` | `UpdateInputs` |
| Rename / retitle | `POST /api/executions/{workflowId}/update` | `SetTitle` |
| Start new run | `POST /api/executions/{workflowId}/update` | `RequestRerun`; follow the returned destination |
| Approve | `POST /api/executions/{workflowId}/signal` | `Approve` |
| Pause | `POST /api/executions/{workflowId}/signal` | `Pause` |
| Resume paused workflow | `POST /api/executions/{workflowId}/signal` | `Resume` |
| Recover from failed step | `POST /api/executions/{workflowId}/recover-from-failed-step` | Linked follow-up execution |
| Cancel | `POST /api/executions/{workflowId}/cancel` | Graceful cancel by default |

An active rerun may keep its `workflowId` through Continue-As-New, while a terminal rerun creates a new linked `workflowId`/`runId` and preserves the closed source. The console therefore follows `execution.workflowId` and `execution.redirectPath` from the update response rather than assuming from `applied = continue_as_new`. [Executions API Contract, section 12](../Api/ExecutionsApiContract.md#12-update-execution) describes admission and response details. `ExternalEvent` is part of the execution contract but normally arrives through system or integration paths rather than a button.

### 12.2 Actions by state

| State | Typical actions |
| --- | --- |
| `scheduled`, `initializing`, `waiting_on_dependencies`, `awaiting_slot`, `planning` | cancel, set title |
| `executing` | cancel, pause, set title |
| `awaiting_external` | cancel, pause, resume, approve when applicable |
| `finalizing` | cancel only if policy allows |
| `completed`, `failed`, `canceled` | new run or recovery per policy, view/download artifacts |

The server reports which actions are available; the UI shows only those and gives disabled reasons otherwise.

### 12.3 Copy

Product language is preferred: **Start New Run** rather than Continue-As-New, **Workflow title** rather than memo title, **Pause workflow** / **Resume workflow**, and **Recover from Failed Step** for checkpointed recovery (never a bare "Resume", which would be confused with resuming a pause). Advanced/debug views may show the underlying terms.

---

## 13. Submit integration

[`docs/Omnigent/CodexCreateToHostContract.md`](../Omnigent/CodexCreateToHostContract.md) describes `/workflows/new` selection and trusted compilation for **Codex via Omnigent**. The form authors product intent, not runtime host, session, credential, filesystem, or daemon authority.

### 13.1 Submit UX

- Runtime selection and skill selection are distinct; the runtime picker never represents skill sets.
- Raw source-precedence logic is not exposed to ordinary users, and the backend decides workflow type and execution routing.
- Browser clients call MoonMind APIs only; Jira, GitHub, object storage, Temporal Server, and model providers stay behind MoonMind APIs.
- `/workflows/new` expands composed presets into editable executable steps before submission. Unresolved preset includes are rejected before submission rather than sent for a worker to resolve later, so the submitted payload is always flat resolved steps plus durable input-snapshot metadata.
- Repository, branch, and publish controls live together in the Steps card: choose a repository, fetch branch options through the MoonMind branch source, populate the Branch dropdown, and show Publish Mode alongside it. Publish Mode is part of submission semantics.

### 13.2 Backend mapping

The console submits workflow-shaped requests with instructions, runtime choice, repository context (including the single authored branch), artifacts, optional scheduling intent, and skill-selection intent. The backend resolves these into workflow start inputs and immutable runtime context; user workflow submissions run as `MoonMind.UserWorkflow`. Manifest-oriented submissions are rejected with an actionable error rather than launched. The UI submits skill **selection intent**, not full mutable skill bodies.

### 13.3 Redirect after create

Immediate and deferred one-time executions redirect to `/workflows/{workflowId}?source=temporal`; recurring schedule creation redirects to `/schedules/{definitionId}`. The route stays stable across new runs and Continue-As-New.

### 13.4 Inline scheduling

`/workflows/new` may include a feature-flagged schedule panel offering run immediately, schedule for later, or recurring. It stays simple, without a deep policy matrix; advanced scheduling policy belongs on schedule detail pages.

---

## 14. Agent skill presentation

Agent skills are instruction bundles, not runtimes, workflow sources, or queue types.

At submit time the UI favors named skill sets over granular controls, a concise summary of inherited/default skill context, and an optional advanced drawer for include/exclude and policy-constrained controls, without serializing skill selections into long query strings. Detail views may show a resolved skill snapshot summary, execution-context constraints, and limited provenance/debug metadata when policy allows. The default list table does not carry skill-set columns.

---

## 15. Artifact integration

Temporal-managed flows are artifact-first for large inputs and outputs: large user inputs upload as artifacts before create/update, workflow detail shows execution-linked artifacts as durable evidence, and downloads go through MoonMind artifact authorization and grant flows.

The dashboard supports creating an artifact placeholder, uploading directly or through presigned multipart flows, completing the upload, fetching metadata and execution-scoped lists, and downloading through presigned or direct endpoints.

Presentation:

- Labels and metadata come from execution linkage when available; preview flows are used when preview refs exist; preview/raw access policy is respected; and artifacts are not assumed safe for inline display.
- Artifacts are immutable references.
- Expansion summaries and preset include-tree artifacts are secondary explanatory evidence. Flat steps, step logs, diagnostics, and output artifacts are the canonical evidence, and the UI does not infer order, completion, or latest output from expansion summaries.

Execution-scoped artifact listing is keyed by `namespace`, `workflowId`, and run ID, and defaults to the **latest run**. The generic artifact panel is secondary to the step-expander artifact groups; when step-scoped evidence exists, the UI uses server-grouped step refs instead of deriving "latest step output" from the execution-wide list.

---

## 16. Vocabulary and identifiers

- Primary product term: **Workflow Execution** (**workflow** in UI copy).
- **Agent skill** or **skill set** for instruction bundles; **preset** for operator-facing authored presets; **binding** or **provenance** for internal preset metadata. Preset includes are not described as sub-plans or separate runs.
- Temporal Task Queues are never what the UI means by "queue".
- `workflowId` is the durable identity and route handle; `runId` is the current/latest run instance, shown as detail/debug metadata; `agentRunId` links a step to managed agent run observability.
- Some live contracts still use older field names, such as `taskInstructions` on executions, the `{temporalRunId}` artifacts template token, and frontend fallback parsing of `taskId`/`temporalRunId` response fields.
- Operators do not need to reason about `source` for the main list/detail flow.

---

## 17. README screenshots

The README dashboard images (`docs/assets/workflow-list.png`, `docs/assets/workflow-detail.png`) are captured from the real Vite-built dashboard on its normal routes with deterministic synthetic fixture data. They show presentation only, not a live provider run.

1. Build with `npm run ui:build`.
2. Serve `api_service/templates/react_dashboard.html` with boot payload `{"page":"dashboard","apiBase":"/api"}` on `/workflows` and `/workflows/{workflowId}`.
3. Use deterministic synthetic rows over the `/api/executions` list/detail/steps contracts, shaped like `frontend/src/entrypoints/workflow-list.test.tsx`, with only public repository names and no private repositories, prompts, emails, tokens, or signed URLs. Capture detail from the Overview tab (`/workflows/{workflowId}/overview?source=temporal`).
4. Use a 1600×1000 viewport in dark mode (`localStorage["moonmind.theme"] = "dark"` before loading; confirm `data-theme="dark"` on the root) and wait for rows, detail summary, and fonts.
5. Capture PNGs from the browser, framed without excess empty space (list 1600×500, Overview 1600×880), and check them at README display size without restyling the UI or editing content.

The captures are refreshed when the pictured flow materially changes. They are not checked byte-for-byte and are not a required CI gate.

---

## 18. Reading one engineering journey

This section walks through one supported journey using existing Workflow Detail surfaces; it adds no panel or status vocabulary.

**Journey.** A repository workflow fails in a middle step. The remediation path repairs it, verifies the exact candidate, and publishes saved work as a pull request.

- **Operator entrypoint.** Follow README Quick start steps 2–5, connect a Provider Profile in **Settings → Providers & Secrets**, add a **Source Control** connection with assigned repositories, then use **Create** with Omnigent, a compatible profile, and the pull-request publishing option.
- **Authority.** The Provider Profile selects credentials and model defaults, the Source Control connection limits repository access, and publication is permitted separately from saving results (see [Workflow Publishing](../Workflows/WorkflowPublishing.md)).
- **Test coverage.** `tests/integration/reliability/test_failure_recovery_publication_journey.py` composes the real workflow controllers, PostgreSQL and artifact owners, checkpoint capture and cold restore, the remediation verifier, and saved-work publication. The failed run keeps its saved bytes and failure diagnostic, repair resumes only unfinished phases, and verification records `verified_resolved` against the exact candidate checkpoint. A transient provider read failure during publication is retried without rerunning implementation, producing exactly one push and one pull-request create.
- **Limitation.** Provider compute and Temporal transport are controlled fixtures, so this is not live provider qualification.

**What Workflow Detail shows.** The Overview tab answers the basic questions from fields the run already records:

| Question | Surface |
| --- | --- |
| What happened? | **Summary** (operator summary or execution summary) and its **Outcome** reason |
| What failed? | **Run Summary**: outcome code and stage, **Failed Step**, **Failure Category**, and the last-step message. A quality-gate stop says no failed Step Execution was fabricated. |
| Where is the output? | Pull-request link, **Open failure diagnostics** (a canonical `artifact://<id>` ref links to its download; other refs stay visible as text), the remediation workspace head, and run artifacts on the **Evidence** tab |
| What can I do next? | The **Workflow actions** menu offers only server-available actions, such as Edit, Rerun, or Continue remediation, with disabled reasons otherwise. |
| Was a governance report produced? | The **Governance report** block shows `ready`, `partial`, `pending`, or `failed`, with the recorded reason code and recoverability when no report was produced. Only its summary is kept, and a report failure never changes the run or publication outcome. Runs whose terminal state was committed outside the workflow terminal-recording path (for example operator cancellation) have no block; see [RunGovernanceReports](../Governance/RunGovernanceReports.md) §6. |

Overview and Evidence do not depend on native chat; when chat is unavailable, the Debug tab keeps a read-only session transcript. `frontend/src/entrypoints/workflow-detail.test.tsx` covers the failed-run questions and the governance block.
