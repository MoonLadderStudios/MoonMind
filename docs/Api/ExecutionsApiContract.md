# Executions API Contract

**Owner:** MoonMind Platform  
**Audience:** backend, dashboard, integrations  
**Covers:** Execution lifecycle HTTP operations, request admission, response projections, ownership, fan-out, and update/reconstruction semantics. Workflow Publishing owns publication intent and the repository-provider contract owns publication evidence.  
**Owning surface:** `/api/executions` and its Temporal execution service boundary  
**Implementation:** `api_service/api/routers/executions.py` (router), `moonmind/schemas/temporal_models.py` (request/response models such as `CreateExecutionRequest`, `UpdateExecutionRequest`, `ExecutionModel`, `ExecutionListResponse`), `moonmind/workflows/executions/execution_contract.py` (admission compiler), and `moonmind/workflows/temporal/service.py` (`TemporalExecutionService`).  
**Related:** [Workflow Publishing](../Workflows/WorkflowPublishing.md), [Settings System](../Security/SettingsSystem.md), [Workflow Architecture](../Workflows/WorkflowArchitecture.md), [Lore VCS Integration Design](../Workflows/LoreVcsIntegrationDesign.md); more in section 3.

The single-context authoring and publication rules in sections 9.3, 9.4, 12, and 16 describe the intended authoring contract. Not every part of it is accepted by current API and worker deployments; `execution_contract.py` is the authority for what the compiler accepts today.

## 1. Purpose

This document describes the direct Temporal-backed execution lifecycle API under `/api/executions`: operations, request and response shapes at summary level, ownership, lifecycle semantics, and errors. Field-level definitions live in the schema module listed above. The API is adapter-first: it works alongside workflow-console product routes without a queue-centric model or a separate publishing service.

[Workflow Publishing](../Workflows/WorkflowPublishing.md) owns publication intent and compiled effects, and [Input Schema Guidance](../Steps/InputSchemaGuidance.md) owns context bindings. This API enforces those contracts regardless of which UI or caller submits work.

## 2. Scope

In scope: authenticated create/describe/list/update/signal/cancel, identifiers, filters, pagination and counts, and the execution-scoped fan-out boundary. Artifact upload/download and direct Temporal server APIs have their own contracts, and worker-internal helpers are not public authoring APIs.

`workflowId` is the durable handle. Remediation uses ordinary `POST /api/executions` with canonical `task.remediation`, not a privileged one-click path. Related remediation routes:

- `GET /api/executions/{workflowId}/remediations?direction=inbound|outbound` returns bidirectional lineage and bounded remediation projections (authored intent, selected evidence, context availability, approvals, locks, operator controls, action results, verification, lifecycle artifacts, and Checkpoint Branch state).
- `POST /api/executions/{remediationWorkflowId}/remediation/approvals/{requestId}` accepts an authenticated `decision` and optional `comment`. Takeover, pause, resume, and cancel remain normal workflow controls. Create admission independently validates target/run visibility, selected Step Executions, checkpoints, Agent Runs, profile and configuration snapshots, action and launch policy, and repository/branch/publication intent.

`TemporalExecutionService` and its `TemporalExecutionRecord` projection implement the API, but they are not a public storage abstraction; public semantics would hold over Visibility-backed or mixed reads.

## 3. Related docs

- `docs/Temporal/TemporalArchitecture.md`, `TemporalPlatformFoundation.md`, `WorkflowTypeCatalogAndLifecycle.md`, `SourceOfTruthAndProjectionModel.md`
- `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`, `StepLedgerAndProgressModel.md`, `WorkflowExecutionProductModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`, `VisibilityAndUiQueryModel.md`
- `docs/UI/WorkflowConsoleArchitecture.md`, `DashboardDesignSystem.md`, `CreatePage.md`
- `docs/Workflows/WorkflowPublishing.md`, `WorkflowPresetsSystem.md`, `WorkflowEditingSystem.md`, `CheckpointBranchSystem.md`
- `docs/RepositoryAccessAndWorkspaceDesign.md`, `docs/Security/SettingsSystem.md`, `docs/Steps/SkillGithubPrResolver.md`

## 4. Vocabulary and identifiers

An execution is Temporal-managed MoonMind work. A workflow type is its root category, such as `MoonMind.UserWorkflow`. A run is one Temporal run instance under a durable workflow identity. "Task" refers only to Temporal internals, qualified external systems, and the supported task-shaped transport envelope, not a product entity.

| Identifier | Role |
| --- | --- |
| `workflowId` | Durable execution handle and path key |
| `runId` | Exact run instance; changes across rerun and Continue-As-New |
| `namespace` | Temporal namespace, returned for detail and debugging |
| `taskId` | Compatibility identifier where exposed; equals `workflowId` |

`runId` is not the durable identity, but it matters for exact-attempt evidence. Publication-scope lineage includes the run/admission identity so a later run under the same `workflowId` cannot silently retarget earlier children.

## 5. API surface

Bodies are JSON with camelCase fields.

| Method | Path | Purpose | Success |
| --- | --- | --- | --- |
| POST | `/api/executions` | Create/start | 201 |
| GET | `/api/executions` | List visible executions | 200 |
| GET | `/api/executions/{workflowId}` | Describe | 200 |
| GET | `/api/executions/{workflowId}/steps` | Current/latest step ledger | 200 |
| POST | `/api/executions/{workflowId}/update` | Validated update or rerun request | 200 |
| POST | `/api/executions/{workflowId}/signal` | Asynchronous signal | 202 |
| POST | `/api/executions/{workflowId}/cancel` | Cancel or terminate | 202 |
| POST | `/api/executions/{workflowId}/checkpoint-branches/{branchId}/verification` | Admit a runtime-published objective-verifier report for the exact branch head | 200 |

Checkpoint verification accepts only `verifierArtifactRef`, an existing immutable artifact reference. The branch owner validates producer, source run, completed turn, Step Execution, exact head, and verdict before recording the receipt and continuing pending remediation verification. Rejected evidence returns 409 `checkpoint_verification_rejected`; invalid fields return 422. [Checkpoint Branch System §8.5](../Workflows/CheckpointBranchSystem.md#85-launch-branch-turn) covers report admission.

## 6. Authentication and authorization

All operations require an authenticated user except the create/describe operations allowed by section 6.1. Ownership comes from authentication, never from a caller-set create field.

MoonMind is single-user, so executions are instance resources: the admitted operator lists, describes, and controls every execution without a human-owner lookup. Owner filters (`ownerType`/`ownerId`) act as provenance selectors, not 403 gates. Describe, update, signal, or cancel for a nonexistent workflow returns 404 without disclosing another execution. New executions default to the instance (`SYSTEM`) owner, and retained human-owner values persist as non-authoritative provenance. Approval, state, source, and policy validation stay at their owning boundaries, and raw secret access and agent policy permissions are not broadened. See [Single-User Application Design](../SingleUserApplicationDesign.md).

A schema, capability token, selected preset, copied parent ID, or publication mode is not an authorization grant. Repository credentials, allowed operations, runtime and model authority, artifacts, and tracker effects are validated by their own owners.

### 6.1 Workflow-scoped execution fan-out

An admitted runtime with the normalized `execution.fanout` requirement may receive a short-lived bearer and `X-MoonMind-Execution-Fanout: v1`. It is not a user session, worker token, container token, or general API credential.

The capability is bound to the parent execution and run, agent run, optional step, runtime session, source kind, and expiry. The server resolves the parent's authoritative owner and permits only:

- one task/workflow child per `POST /api/executions`, with `runtimeInheritance="caller"` and a stable `idempotencyKey`;
- `GET /api/executions/{workflowId}` for an admitted child of that parent with the same owner.

Schedules, ungoverned direct-create payloads, arbitrary source overrides, unrelated reads, and all other operations are denied, and an unauthorized child describe returns 404. Runtime adapters mint the bearer only after trusted capability provenance and policy checks.

The server, not a helper flag, enforces publication and repository inheritance. It retrieves the parent's frozen authored and resolved scope intent and validates the submitted target against the admitted child contract. A coordinator's local compiled `none` is never the inherited policy, and a client cannot omit an inheritance field, forge a new root, select `auto`, or submit an independent publish override to broaden scope.

A PR head derived from the target under the same authorized repository is permitted only through the declared resolver target derivation, not as an arbitrary source override. Non-default implementation bases survive discovery unchanged, and distinct repository roles need an explicitly supported mapping and independent target admission.

Static child-policy and handoff incompatibility is checked before parent effects when known, and dynamically discovered children are revalidated before acceptance. A rejected later child does not undo earlier accepted children; responses and artifacts preserve exact partial results.

## 7. Workflow catalog and lifecycle

Supported root workflow types are `MoonMind.UserWorkflow` and `MoonMind.MergeAutomation`. `MoonMind.ManifestIngest` is not in the advertised catalog: new launches are rejected with an actionable error, and existing rows remain readable as history.

Domain states are `scheduled`, `initializing`, `waiting_on_dependencies`, `planning`, `awaiting_slot`, `executing`, `awaiting_external`, `finalizing`, `no_commit`, `completed`, `failed`, and `canceled`.

| Close status | `temporalStatus` |
| --- | --- |
| null | running |
| completed | completed |
| canceled | canceled |
| failed, terminated, timed_out | failed |

Continue-As-New is a Temporal close concept; clients use the run-history surface rather than expecting a separate `temporalStatus` value.

Publication policy, actual publication outcome, and lifecycle state are distinct. A coordinator can complete after enqueueing while its children are still active, and a saved result can exist after failed compute or publication, so no projection equates them.

## 8. Shared response model

### 8.1 ExecutionModel

Create, describe, signal, and cancel return `ExecutionModel`. List returns the bounded `ExecutionListItemModel` without detail-only memo, parameters, or summaries. Core fields are identity (`namespace`, `workflowId`, `runId`, `workflowType`), lifecycle (`state`, `temporalStatus`, `closeStatus`), `objectiveOutcome` (also on list rows), optional `agentRunId` and bounded `progress`, `searchAttributes`, `memo`, `artifactRefs`, and timestamps (`startedAt`, `queuedAt`, `updatedAt`, `closedAt`).

`objectiveOutcome` is read from memo and reported separately from lifecycle. Its values are `active`, `succeeded`, `failed`, `cancelled`, `idle`, and `verification_blocked`; missing, unknown, or malformed values project as null without changing memo or lifecycle. A completed execution with an idle objective displays as neutral **Idle** with its explanation, because finishing a scan with nothing to do does not mean an issue was implemented. Completed executions whose objective failed, was verification-blocked, or was cancelled display **Failed**, **Verification blocked**, or **Cancelled**, while lifecycle and filter values stay unchanged. `no_commit` is successful work that needed no commit, not capacity unavailability or an empty scan.

Success metrics follow the same distinction. A completed idle execution has `runMetrics.success = null` and `successRateSample = { "success": 0, "sampleSize": 0 }`, so it is excluded from success rates. Failed or canceled states and failed, verification-blocked, or cancelled objectives count as unsuccessful even when Temporal closed cleanly (one sample, zero successes). Active executions have a zero-size sample, and `no_commit` counts as successful, including older records whose memo still says active. `/metrics` keeps exact lifecycle counts and completion rate, and its separate `objectiveMetrics` sample excludes idle scans from the objective success denominator.

The optional `recommendedNextAction` uses outcome evidence and lifecycle: a completed run without an inline finish summary suggests reviewing its summary, active runs get monitoring guidance, and publication-specific recommendations take precedence when their evidence is available.

### 8.2 ExecutionProgress

Progress is bounded and does not replace `/steps`. Required counts are `total`, `pending`, `executing`, `completed`, and `failed`; optional counts include `ready`, `awaitingExternal`, `reviewing`, `skipped`, and `canceled`, plus `currentStepTitle` and `updatedAt`. A missing optional counter does not mean zero.

### 8.3 Search attributes and memo

Baseline attributes are `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, and `mm_entry`; optional ones include `mm_repo`, `mm_integration`, `mm_target_runtime`, `mm_target_skill`, and `mm_provider_profile`.

`mm_provider_profile` and the memo `providerProfile` entries record the Provider Profile at admission and are updated when a launch resolves a pending selection ([Workflows List §7.2](../UI/WorkflowsListPage.md#72-how-the-projection-is-recorded)). The memo keeps every distinct recorded association for facet discovery; facet continuation keeps overflow values before advancing the workflow page and deduplicates IDs across pages, and truncated memo coverage stays marked truncated. List rows expose a bounded `providerProfile` summary (`selectionState`, up to eight `{id, label, harness}` entries, `profileCount`) that never carries credentials, OAuth paths, provider payloads, or host/container handles.

`providerProfileIdIn` and `providerProfileIdNotIn` are repeated exact-ID parameters whose decoded characters (commas, quotes) are literal. The CSV parameters `providerProfileIn` and `providerProfileNotIn` keep their meaning for shared URLs and saved views and are unioned with the exact-ID sets. Conflicting intersections are rejected; distinct included and excluded IDs may coexist.

Runtime and Skill facets are authoritative only after the namespace registers their `KeywordList` types; before that, dependent queries degrade rather than sending invalid Visibility queries. Unknown values are omitted rather than shown blank, and these fields are filters, not sortable strings.

Memo carries title, summary, and optional safe input/manifest refs; clients tolerate additional documented-safe keys. Projection state is not a second source of truth for publication or terminal evidence.

### 8.4 ExecutionListResponse

The response has `items`, optional `nextPageToken`, optional `count`, `countMode` (`exact | estimated_or_unknown`), and `degradedCount`. An exact count requires a successful bounded count query in addition to the page read. If counting fails, the page is preserved with `count = null`, `countMode = estimated_or_unknown`, and `degradedCount = true`, and clients do not present an exact total from that.

### 8.5 Authored context and publication projection

Detail and reconstruction surfaces expose enough safe input and plan evidence to distinguish the authored repository/branch context, the authored publication selection (Auto as `default`), the resolved scope behavior and its provenance, the execution's compiled mode, owner, and target, and the actual enqueue, save, publication, and merge results.

This uses existing input artifacts, plan, result objects, and the bounded detail projection rather than a mutable publication record, and derived mode is not written back into authored input. For example, a PR batch's coordinator may compile to `none` while its authored policy remains PR or Auto. The projection gives Create and Details a safe effective explanation but no second editable policy. Clients cannot infer descendant prohibition from a local None, child completion from enqueue success, or merge authority from a label, and missing evidence is reported as unavailable rather than reconstructed from current catalog defaults.

Managed and agent-owned publication results come from accepted `moonmind.publish.repository.v1` artifacts under the repository-provider contract. The API preserves their attempt/target ownership and safe references rather than defining another evidence schema. Older payload shapes are decoded only for recorded histories, and a provider projection still pending mapping stays `awaiting_external` rather than reporting PR success.

## 9. Create execution

### 9.1 Request

`POST /api/executions` takes `workflowType` (required; `MoonMind.UserWorkflow` or `MoonMind.MergeAutomation`), optional `title`, `inputArtifactRef`, `planArtifactRef`, `failurePolicy` (`fail_fast | continue_and_report | best_effort`), small JSON `initialParameters`, and `idempotencyKey`. `manifestArtifactRef` is accepted for parsing older payloads but ignored for new launches, and ManifestIngest payloads are rejected. Task-shaped envelopes (section 16) go through the same admission compiler.

A direct UserWorkflow request cannot select Omnigent: it returns 422 `omnigent_product_boundary_required` when `targetRuntime`, `task.targetRuntime`, or a `runtime.mode` block names Omnigent, or when no runtime is named and the deployment default is Omnigent. Omnigent work uses the task-shaped envelope, which persists the immutable execution plan before scheduling; `moonmind workflow run` submits that envelope with `--param key=value` extras in `task.inputs`. A task-shaped request whose `idempotencyKey` already names an admitted execution returns that execution before any preset, Provider Profile, or runtime-default resolution, so an uncertain submission can be reconciled even after catalog state changes.

### 9.2 Validation

A UserWorkflow needs at least one planning source before it starts: nonempty instructions, a selected Skill, an input artifact, or a plan artifact. Parameters are small and JSON-serializable, and artifact refs are references, not embedded blobs. Every public form, including caller-supplied plan artifacts and execution-shaped input, goes through shared validation; a client-supplied compiled mode, bound input, parent lineage, or accepted-evidence object does not bypass compilation.

### 9.3 Single authored context and publishing

The authored repository/source target is selected once through the canonical repository contract, rather than through competing workflow, preset-input, Skill-input, `startingBranch`, and `targetBranch` values. Role-specific source, target, and destination distinctions are explicit.

The authored selection is `initialParameters.task.publish.mode` in direct requests and `payload.task.publish.mode` in task-shaped requests; both normalize to the same snapshot. Authored values are:

```text
default | none | branch | pr | pr_with_merge_automation
```

Omission and `default` both mean Auto. Compiled execution `publishMode` is `none | branch | pr | auto`, and the compiler resolves `default` and the compound PR-and-merge selection before workers or helpers run. A recorded literal `auto` keeps its Skill-owned meaning when decoding history and is never the generic default. PR Resolver authoring uses default/omission with an explicit PR locator, as in the [resolver integration contract](../Steps/SkillGithubPrResolver.md#91-new-authored-userworkflow-request), and a caller cannot pick an older decoder to bypass validation.

The workspace setting `workflow.default_publish_mode` and its environment aliases are not part of this precedence rule. [Workflow Publishing](../Workflows/WorkflowPublishing.md) owns the single precedence rule, and [Settings System section 10.6](../Security/SettingsSystem.md#106-publication-default-ownership-and-retired-setting) covers how configured historical intent is preserved or reviewed. Omitted and explicit-default requests compose the same way, including through schedules and helpers.

Context bindings are resolved before required-field validation and preset expansion. Bound fields are execution projections, not authored duplicates; conflicting or redundant caller copies are rejected with an actionable path, while historical decoding may collapse proven-equivalent recorded values.

The compiler pins selected definitions, context and target identity, authored intent, resolved default behavior, child requirements, and effect owners in the existing snapshot and plan evidence, and checks known composition conflicts before parent launch or tracker mutation. Unknown future targets are validated at the declared child boundary. The task-input snapshot also preserves the workspace authority compiled from the selected Agent Profile; native review admission compares the full compiled graph, including that workspace, with the frozen input bytes, so an altered workspace or artifact digest is rejected before launch. Preserving the value grants no extra authority.

Explicit None is never promoted. An incompatible publishing Skill, a parallel shared-branch batch without a qualified serial handoff, or a missing predecessor-code transfer fails before effects. A read-only step or non-publishing coordinator does not erase the scope's PR intent. For example, a batch authored with `{"task": {"publish": {"mode": "pr_with_merge_automation"}}}` keeps that selection while its coordinator compiles to None and its children compile to PR plus merge automation, which may in turn invoke a Skill-owned Auto resolver.

### 9.4 Idempotency

Create deduplication is scoped to owner, workflow type, and idempotency key, and a repeated equivalent request returns the existing execution. Under the single-context contract, reuse also checks admitted context, target, publication policy, and definition evidence: a conflicting request under the same key cannot mutate the existing execution or be reported as accepted, and returns a bounded conflict with the existing reference where visible.

Dependabot's cross-run repository/PR/head identity is preserved, so a policy edit does not create a competing resolver for the same head or retarget an old one. Helpers report the existing child's actual disposition and use explicit execution controls for an authorized replacement.

### 9.5 Create result

Creation allocates workflow and run identity, starts in `initializing` unless the lifecycle calls for a schedule gate first, materializes safe metadata, and returns `ExecutionModel` with 201. Acceptance is not objective success.

Title resolution is deterministic: a meaningful caller title wins; otherwise the selected capability or preset label is combined with up to two structured targets such as an issue, PR, branch, or failing check (bookkeeping step IDs are not targets). Titles are bounded to 150 characters. Creation records the generated base and title-transition state in memo (`titleBase`, `titleProvenance` of `user_explicit` or `generated`, the monotonic `titleRevision`, and `titleSource`/`titleConfidence`). Preset resolvers that select their target after creation (such as `github-issue-search-and-implement` via its `titleEnrichment` opt-in) enrich the title to `<base>: #<number>` through the same workflow-owned transition, without touching identity, plan, branches, checkpoints, artifacts, commits, or publication.

### 9.6 Errors

Create validation uses 422 `invalid_execution_request` with field-addressable details for context and policy conflicts; malformed bodies may produce framework validation errors, and authentication uses the auth layer's 401/403. A conflicting idempotent intent is a distinct conflict, not a successful create.

## 10. List executions

`GET /api/executions` filters by `workflowType`, `state`, `ownerType`, `ownerId`, `entry`, `repo`, `integration`, registered runtime facets (`targetRuntime`/`targetRuntimeIn`, never a Provider Profile alias), Provider Profile ID/state membership (`providerProfileIdIn`, `providerProfileIdNotIn`, `providerProfileIn`, `providerProfileNotIn`, `providerProfileStateIn`, `providerProfileStateNotIn`, `providerProfileBlank`; see [Workflows List §12.1](../UI/WorkflowsListPage.md#121-filter-encoding)), and `targetSkillIn`. Filters apply before count and pagination. `pageSize` defaults to 50 (range 1–200), and `nextPageToken` is opaque.

Runtime and Skill attributes use membership queries over their registered one-item `KeywordList` values. Default ordering is `updatedAt` descending by one-minute bucket, then queued order descending within a bucket, then `workflowId` descending, so small refresh differences do not reorder stable queued rows. A null next token means no more pages; offsets are not public cursor semantics. Count confidence follows section 8.4.

Success is 200 with `ExecutionListResponse`. Unauthorized owner scope is 403 `execution_forbidden`, and invalid tokens are 422 `invalid_pagination_token` (some route wrappers also report filter errors with that code). Clients do not treat errors as empty results.

## 11. Describe execution and steps

`GET /api/executions/{workflowId}` returns 200 `ExecutionModel` or 404 `execution_not_found` for absent or invisible work. Reconstruction uses the exact input and plan refs and the authored-versus-derived distinction in section 8.5, not mutable defaults.

`GET /api/executions/{workflowId}/steps` returns the current run's bounded step ledger so clients need not parse logs. The response carries `workflowId`, `runId`, `runScope`, and `steps`. Each row has identity and structure (`logicalStepId`, order, title, tool, `dependsOn`), status (`status`, `waitingReason`, `attentionRequired`, `attempt`, timestamps), a `timing` object, `summary`, `checks`, `lastError`, `refs` (child workflow/run, `agentRunId`), and `artifacts` (output summary and primary, runtime stdout/stderr/merged logs/diagnostics, provider snapshot). See the `StepLedger*` models in `moonmind/schemas/temporal_models.py`.

`logicalStepId` is stable within its plan, and attempts are scoped to workflowId, runId, and logicalStepId. A step `agentRunId` can coexist with a top-level observability binding. Timing is logical step timing, not runner workload duration (do not infer it from `workload.durationSeconds`). Its `precision` is `exact`, `live` (elapsed as of `serverNow`), `fallback` (displayable but not exact terminal evidence), or `unavailable`. Preserved rows set `preserved: true` and show original timing rather than new work.

`GET /api/executions/{workflowId}/steps/{logicalStepId}/step-executions` returns the same timing object per attempt, from which UI totals can be derived, with the same ownership-preserving 404 behavior. Derived per-step publication disposition is not a step-level authoring override, and accepted publication or Skill evidence is read only from its authoritative producer.

## 12. Update execution

`POST /api/executions/{workflowId}/update` takes `updateName` (`UpdateInputs` by default, `SetTitle`, or `RequestRerun`), candidate `inputArtifactRef`/`planArtifactRef`, a small `parametersPatch`, `title` (required for SetTitle), and an `idempotencyKey`.

**UpdateInputs** replaces or adds refs and patches admitted parameters; no-op updates can succeed. Executing or awaiting-external work may take supported changes at the next safe point, while major changes may need a newly admitted run.

**SetTitle** changes display metadata immediately without changing execution authority. It runs through the shared workflow-owned title transition on both the service record and the `MoonMind.UserWorkflow` update of the same name (an object payload carrying `title`; the `update_title` method name forwards to it). Titles use the shared display-safe policy, and empty or unsafe text is rejected with `invalid_update_request`. Renaming to the same text still marks the title explicit. The record updates memo `title`, `titleBase`, `titleProvenance`, `titleRevision`, and `titleSource` and recomputes `mm_title` search tokens so state, memo, search, and UI agree. Genuine no-ops succeed without touching the record or bumping the revision.

**RequestRerun** requests a clean re-execution under the [run-history/rerun contract](../Temporal/WorkflowRunHistoryAndNewRunSemantics.md#7-new-run-semantics). A supported active workflow may Continue-As-New, keeping `workflowId` with a new `runId`. For a terminal source (including one that closes before Temporal receives the update), the service creates a fresh execution with a new `workflowId` and `runId` through normal admission, records the source in `rerunSource`, and leaves the source closed; closed Temporal runs never process an ordinary rerun update. An exact rerun preserves the source's immutable inputs, Skill snapshot, and execution choices subject to current validation. An admitted Omnigent execution plan cannot be edited here; replacement inputs need a new execution, and the update returns 409 `omnigent_execution_plan_replacement_required`. Unsupported controls or invalid admission return an explicit non-accepted result or validation error rather than fabricating a destination or falling back to a queue.

### 12.1 Response and idempotency

The response includes `accepted`, `applied` (`immediate | next_safe_point | continue_as_new`), a message, and the materialized `execution`; accepted reruns report `continueAsNewCause = manual_rerun`. A terminal fresh rerun also returns `applied = continue_as_new`, which does not prove that Temporal performed Continue-As-New or kept the `workflowId`, so clients follow the returned `execution.workflowId`, `execution.runId`, and `execution.redirectPath`. A 200 alone does not mean accepted. Update idempotency keeps only the most recent key and response; it is not a historical deduplication ledger.

Ordinary updates to terminal executions return 200 with `accepted: false`, `applied: immediate`, and an explanation; terminal RequestRerun is the fresh-start exception. Missing or invisible targets return 404, and invalid or unsupported updates return 422 `invalid_update_request` or framework errors.

### 12.2 Context and policy immutability

The single authored context and publication rules apply to UpdateInputs and RequestRerun. A patch cannot change compiled mode, overwrite bound inputs, revive a preset override, or change a child's policy through the parent. An admitted scope's effect authority is fixed for its active work and accepted children: edits before admission can recompile the draft, but changes after target, candidate, or child admission go through a new admission, rerun, or continuation with lineage. Reruns reconstruct authored intent and its original default-resolution evidence, revalidate changed definitions and defaults visibly, and never turn an old None-to-Auto normalization or mixed per-step policy into a new authority grant. Historical artifacts and prior child results stay unchanged.

## 13. Signal execution

`POST /api/executions/{workflowId}/signal` takes `signalName`, an optional `payload` (default `{}`), and an optional `payloadArtifactRef`.

| Signal | Required payload | Behavior |
| --- | --- | --- |
| ExternalEvent | `source`, `event_type` | Records the event; clears only the relevant external wait when not paused. |
| Approve | `approval_type` | Applies the approval under the owning gate; the lifecycle resumes only when its requirements are met. |
| Pause | signal-specific | Sets a paused overlay and waiting metadata while keeping the underlying state. |
| Resume | signal-specific | Clears the pause overlay and returns to the underlying gate. |

Remediation approval uses its durable pending approval record and the endpoint in section 2; expired, terminal, self-approved, or ineligible decisions fail with bounded validation errors. Success is 202 with the execution. Signals to terminal executions and invalid signal/payload combinations are rejected, normally 409 `signal_rejected`, and absent targets return 404. A signal or approval cannot replace authored publication intent, broaden repository authority, or authorize duplicate effects.

## 14. Cancel execution

`POST /api/executions/{workflowId}/cancel` takes an optional `reason` and `graceful` (default true).

Graceful cancellation durably requests Temporal cancellation. Until Temporal confirms it, the response keeps the live state and pause/wait metadata, records the request in the intervention audit, and reports cancellation as requested. Only confirmed cancellation records `canceled`, clears pause/wait metadata, and allows terminal dependency fan-out, including when a first-attempt workflow task is queued on a worker version with no pollers. A terminal record is unchanged by graceful cancellation.

Force cancel (`graceful: false`) stays available through `actions.canForceCancel` even for a terminal or orphaned record, and always targets the recorded Temporal run, independent of workers and containers. Termination records `failed` with close status `terminated` and a `forced_termination:` summary. If Temporal reports NOT_FOUND, a run-scoped describe separates an already closed run (result preserved) from an absent execution, which is recorded canceled with an orphan-repair summary and unconfirmed runtime cleanup. Transient, permission, and timeout failures never count as absence; they leave the record unchanged and return a retryable 503. Force-cancel RPCs have a ten-second budget, and ownership checks apply to orphaned records. Success is 202; invisible targets return 404.

Accepted cancellation does not prove runtime teardown or rollback of remote effects. Verified pushes, PRs, merges, and accepted children remain evidence, and preservation and credential release follow their lifecycle owners.

## 15. Error model

Structured domain errors look like:

```json
{
  "detail": {
    "code": "invalid_execution_request",
    "message": "The selected publication policy is incompatible with this workflow."
  }
}
```

Input and policy errors also identify the authored field and affected consumer when available; for example, a missing bound repository points to the workflow source rather than a hidden Skill input.

| Code | Status |
| --- | --- |
| `execution_not_found` | 404 |
| `execution_forbidden` | 403 |
| `invalid_execution_request` | 422 |
| `invalid_update_request` | 422 |
| `invalid_pagination_token` | 422 |
| `signal_rejected` | 409 |
| `omnigent_product_boundary_required` | 422 |
| `omnigent_execution_plan_replacement_required` | 409 |
| `checkpoint_verification_rejected` | 409 |

A conflicting idempotent intent is distinct from successful reuse. Framework JSON, type, and coercion errors can occur before route logic. Errors are bounded and do not contain credentials, unrestricted parent data, raw provider sessions, or private target content beyond authorized identifiers.

## 16. Task-shaped and historical payloads

The task-shaped transport maps to Temporal work but is not a separate policy compiler. `workflowId` is canonical and the compatibility `taskId` equals it. Supported `task.tool`/`step.tool` selectors and older Skill aliases use the established selector normalization, with `type: skill` where that transport requires it.

New authoring has one repository/source target, one applicable branch, and one publication selection; old Skill args, preset `publish_mode`, `startingBranch`, `targetBranch`, or a worker-facing mode cannot add another authority. Checkpoint operations and resolver inputs follow the same rule.

Historical decoding is versioned and preserves evidence. A recorded literal Auto stays Skill-owned, and old None-to-Auto coercion applies only to recorded old executions. A coordinator's old local None and an explicit child PR policy reconstruct as one PR intent only when provenance proves that meaning; conflicts and unknown origins need review rather than inference. Recorded workspace-fallback values follow the Settings contract rather than current configuration.

Replay and supported resets keep original bytes, digests, and workflow command semantics, while new drafts and schedule occurrences go through current admission. Consumers that would reinterpret or default a new authoring value are rejected before launch without rewriting historical hashes, and fresh callers cannot opt into a historical decoder by supplying an old label or `schemaVersion`.

## 17. Implementation notes

The projection row materializes identifiers, lifecycle state, attributes and memo, artifact refs, pending updates, counters, and timestamps, but it does not replace Temporal history or authoritative artifacts. List implementations preserve successful pages when count enrichment fails, and pagination or filter error naming never justifies silently reporting empty work.

Supported root types, update and signal names, error semantics, ordering and count confidence, identity rules, and authored-versus-compiled mode semantics are part of the contract; changes to them are classified as additive, behavioral with stable shape, or breaking.

## 18. Summary

The API is execution-oriented, authenticated, ownership-scoped, and grounded in `workflowId`. It keeps explicit lifecycle controls while compiling one authored repository, branch, and publication intent for every supported producer. Steps and children receive derived contracts and independently verified outcomes, not competing user-authored settings.
