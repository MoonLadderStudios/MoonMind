# Executions API Contract

**Document Class:** Canonical declarative  
**Viewpoint:** Module Contract Specification  
**Project:** MoonMind  
**Status:** Desired-state API contract  
**Owner:** MoonMind Platform  
**Last updated:** 2026-09-06  
**Audience:** backend, dashboard, integrations

Implementation sequencing, rollout status, and backlog notes live in issues or `docs/tmp/`, not as the primary story of this contract. The single-context/publication rules below are long-term target semantics, not evidence that current API/worker deployments already accept the new authoring values.

## 1. Purpose

This document defines the direct Temporal-backed execution lifecycle API under `/api/executions`: HTTP operations, request/response shapes, ownership, and lifecycle semantics. It remains an adapter-first contract, usable alongside workflow-console product routes without requiring a queue-centric model or a separate publishing service.

[Workflow Publishing](../Workflows/WorkflowPublishing.md) owns publication intent and compiled effects. [Input Schema Guidance](../Steps/InputSchemaGuidance.md) owns context bindings. This API enforces those contracts independently of the UI or caller.

## 2. Scope

In scope are authenticated lifecycle operations, create/describe/list/update/signal/cancel, identifiers, filters/pagination/counts, and the narrow execution-scoped fan-out boundary. Artifact upload/download and direct Temporal server APIs have their own contracts. Worker-internal helpers are not public authoring APIs.

`workflowId` is the canonical durable handle. Product UI routes may coexist with this surface. Remediation uses normal `POST /api/executions` with canonical `task.remediation`, not a privileged one-click submission path.

`GET /api/executions/{workflowId}/remediations?direction=inbound|outbound` returns bidirectional lineage and bounded canonical remediation projections, including authored intent, selected evidence, context availability, approvals/locks/operator controls, action results, verification, lifecycle artifacts, and Checkpoint Branch state.

`POST /api/executions/{remediationWorkflowId}/remediation/approvals/{requestId}` accepts authenticated `decision` and optional rationale `comment`. Takeover/pause/resume/cancel remain normal workflow controls. Create admission independently validates exact target/run visibility, selected Step Executions/checkpoints/Agent Runs, profile/configuration snapshots, action/launch policy, and repository/branch/publication intent.

The current `TemporalExecutionService`/`TemporalExecutionRecord` projection is an implementation owner, not a permanent public storage abstraction. Public semantics survive a change to Visibility-backed or mixed reads.

## 3. Related Documents

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/SourceOfTruthAndProjectionModel.md`
- `docs/Temporal/WorkflowRunHistoryAndNewRunSemantics.md`
- `docs/Temporal/StepLedgerAndProgressModel.md`
- `docs/Temporal/WorkflowExecutionProductModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/UI/WorkflowConsoleArchitecture.md`
- `docs/UI/DashboardDesignSystem.md`
- `docs/UI/CreatePage.md`
- `docs/Workflows/WorkflowPublishing.md`
- `docs/Workflows/WorkflowPresetsSystem.md`
- `docs/Workflows/WorkflowEditingSystem.md`
- `docs/RepositoryAccessAndWorkspaceDesign.md`

## 4. Vocabulary and Identifiers

An execution is Temporal-managed MoonMind work. A workflow type is its root category, such as `MoonMind.UserWorkflow`. A run is one Temporal run instance under a durable workflow identity. Task is reserved for Temporal internals, qualified external systems, and explicitly supported task-shaped transport envelopes, not a new product entity.

| Identifier | Role |
| --- | --- |
| `workflowId` | Primary durable execution handle and path key |
| `runId` | Exact run instance; changes across supported rerun/Continue-As-New |
| `namespace` | Temporal namespace, returned for detail/debugging |
| `taskId` | Compatibility product identifier where exposed; equals `workflowId` for Temporal work |

Clients must not treat `runId` as the durable workflow identity or ignore it when checking exact-attempt evidence. Publication-scope lineage includes the applicable run/admission identity, so a later run with the same workflowId cannot silently retarget earlier children.

## 5. API Surface

All request/response bodies are JSON with camelCase external fields.

| Method | Path | Purpose | Success |
| --- | --- | --- | --- |
| POST | `/api/executions` | Create/start | 201 |
| GET | `/api/executions` | List visible executions | 200 |
| GET | `/api/executions/{workflowId}` | Describe | 200 |
| GET | `/api/executions/{workflowId}/steps` | Current/latest step ledger | 200 |
| POST | `/api/executions/{workflowId}/update` | Validated update/rerun request | 200 |
| POST | `/api/executions/{workflowId}/signal` | Asynchronous signal | 202 |
| POST | `/api/executions/{workflowId}/cancel` | Cancel/terminate | 202 |

## 6. Authentication and Authorization

All operations require an authenticated user except the exact create/describe operations authorized by section 6.5. Ownership is derived from authentication, never a caller-set create field.

Non-admin list requests are scoped to the caller. Listing another owner returns 403. Non-admin direct describe/update/signal/cancel for a nonexistent or invisible workflow returns 404 to avoid disclosing another owner's execution. Admin access remains policy-controlled.

A schema, required-capability token, selected preset, copied parent ID, or publication mode is not an authorization grant. Repository credentials, allowed operations, runtime/model authority, artifacts, and tracker effects are independently validated through their existing owners.

### 6.5 Workflow-scoped Execution Fan-out

An admitted runtime with the normalized `execution.fanout` requirement may receive a short-lived bearer and `X-MoonMind-Execution-Fanout: v1`. This is not a user session, worker token, container token, or general API credential.

The capability is bound to the parent execution/run, agent run, optional step, runtime session/id, source kind, and expiry. The server resolves the parent's authoritative owner and permits only:

- One task/workflow child per `POST /api/executions`, with `runtimeInheritance="caller"` and stable `idempotencyKey`.
- `GET /api/executions/{workflowId}` for an admitted child of that parent with the same owner.

Schedules, ungoverned direct-create payloads, arbitrary source overrides, unrelated reads, and all other operations are denied. Unauthorized child describe returns 404. Runtime adapters mint/materialize this bearer only after trusted capability provenance and policy checks.

Publication and repository inheritance are enforced by the server, not an optional helper flag. The server retrieves the parent's frozen authored/resolved scope intent and validates the submitted target against its admitted child contract. A coordinator's local compiled `none` is never the inherited policy. A client cannot omit an inheritance field, forge a new root, select `auto`, or submit an independent publish override to broaden the scope.

A target-derived PR head under the same authorized repository is permitted only through the declared resolver target derivation. It is not an arbitrary source override. Non-default implementation bases must survive discovery unchanged. Genuinely distinct repository roles require an explicitly supported mapping and independent target admission.

Static child-policy/handoff incompatibility is checked before parent effects when known. Dynamically discovered children are revalidated before acceptance. A rejected later child does not undo earlier accepted children; responses and artifacts preserve exact partial results.

## 7. Workflow Catalog and Lifecycle

Supported root workflow types are `MoonMind.UserWorkflow` and `MoonMind.ManifestIngest` until explicitly extended.

Domain states include `scheduled`, `initializing`, `waiting_on_dependencies`, `planning`, `awaiting_slot`, `executing`, `awaiting_external`, `finalizing`, `no_commit`, `completed`, `failed`, and `canceled`.

| Close status | `temporalStatus` |
| --- | --- |
| null | running |
| completed | completed |
| canceled | canceled |
| failed, terminated, timed_out | failed |

Continue-As-New is a real Temporal close concept; clients use the documented run-history surface rather than assuming a separate `temporalStatus` enumeration value.

Publication policy, actual publication outcome, and lifecycle state are different. A coordinator can be completed after its enqueue objective while its children remain active. A saved result can exist after failed compute or publication. No API projection may equate those states.

## 8. Shared Response Model

### 8.1 ExecutionModel

Create, describe, signal, and cancel return this materialized shape; list nests it.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| namespace | string | yes | Temporal namespace |
| workflowId | string | yes | Durable identity |
| runId | string | yes | Current run identity |
| workflowType | string | yes | Root workflow type |
| state | string | yes | Domain lifecycle state |
| temporalStatus | running/completed/failed/canceled | yes | Simplified Temporal status |
| closeStatus | string/null | no | Terminal close disposition |
| agentRunId | string/null | no | Top-level observability binding when applicable |
| progress | object/null | no | Bounded current-run progress |
| searchAttributes | object | yes | Indexed safe metadata |
| memo | object | yes | Small display metadata |
| artifactRefs | string array | yes | Linked artifact references |
| startedAt | datetime | yes | Initial start |
| queuedAt | datetime/null | no | Stable queued ordering fallback |
| updatedAt | datetime | yes | Meaningful progress/lifecycle update |
| closedAt | datetime/null | no | Terminal time |

### 8.2 ExecutionProgress

Progress remains bounded and is not a substitute for `/steps`.

Required counts are `total`, `pending`, `executing`, `completed`, and `failed`. Optional counts include `ready`, `awaitingExternal`, `reviewing`, `skipped`, and `canceled`. `currentStepTitle` and `updatedAt` provide current safe context. Missing optional counters do not imply an exact zero unless the contract says so.

### 8.3 Search Attributes and Memo

Baseline attributes are `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, and `mm_entry`. Optional bounded metadata includes `mm_repo`, `mm_integration`, `mm_target_runtime`, and `mm_target_skill`.

Runtime/Skill facets are authoritative only after the namespace registers their `KeywordList` types. Before registration, dependent queries degrade without sending invalid Visibility queries. Unknown values are omitted rather than blank. These fields are filters, not sortable scalar strings.

Memo carries title/summary and optional safe input/manifest refs. Clients tolerate additional documented-safe keys. Projection-authored state is not a second source of truth for publication or terminal evidence.

### 8.4 ExecutionListResponse

The response includes `items`, optional `nextPageToken`, optional `count`, required `countMode = exact | estimated_or_unknown`, and required `degradedCount`.

An exact count requires a successful bounded count query as well as the page read. Count failure preserves the page and returns `count = null`, `countMode = estimated_or_unknown`, and `degradedCount = true`. Clients do not present an exact total/page count from degraded evidence.

### 8.5 Authored Context and Publication Projection

Detail/reconstruction surfaces expose enough safe input and plan evidence to distinguish:

- the single authored source/repository and branch context;
- the authored publication selection, including Auto as `default`;
- the resolved scope behavior and definition/derivation provenance;
- the particular execution's compiled mode/owner/target;
- actual enqueue, save, publication, and merge results.

Use the existing input artifacts, plan, result objects, and bounded detail projection rather than a new mutable publication record. Derived mode is not written back into authored input. In particular, a PR batch's coordinator may have compiled `none` while its authored policy remains PR or Auto.

The projection supplies a safe effective explanation for Create/Details. It does not expose another editable policy. Clients cannot infer descendant prohibition from local None, child completion from enqueue success, or merge authority from a label. Missing evidence is reported as unavailable, not reconstructed from current catalog defaults.

## 9. Create Execution

### 9.1 Endpoint and Direct Request

`POST /api/executions`

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| workflowType | supported root type | yes | UserWorkflow or ManifestIngest |
| title | string/null | no | Display title |
| inputArtifactRef | string/null | no | Input reference |
| planArtifactRef | string/null | no | Plan reference |
| manifestArtifactRef | string/null | conditional | Required for ManifestIngest |
| failurePolicy | fail_fast/continue_and_report/best_effort/null | no | Initial failure policy |
| initialParameters | object | no | Small JSON authored parameters |
| idempotencyKey | string/null | no | Deduplication key |

Task-shaped envelopes are covered in section 16 and use the same admission compiler.

### 9.2 Validation

A UserWorkflow has at least one valid planning source before persistence/start: nonempty instructions, selected Skill, input artifact, or plan artifact. Parameters are small and JSON-serializable. Artifact refs are references, not embedded blobs.

All public forms, including caller-supplied plan artifacts and execution-shaped input, pass shared validation. A client-provided compiled mode, bound input, parent lineage, or accepted-evidence object does not bypass context/policy compilation.

### 9.3 Single Authored Context and Publishing

The ordinary authored repository/source target is selected once through the canonical repository contract. There are no simultaneous workflow, preset-input, Skill-input, `startingBranch`, and `targetBranch` authorities. Role-specific source/target/destination distinctions must be explicit and supported.

In direct requests the authored selection is under `initialParameters.task.publish.mode`; in a task-shaped transport it is under `payload.task.publish.mode`. Both normalize to the same authored snapshot. New authoring values are:

```text
default | none | branch | pr | pr_with_merge_automation
```

Omission and `default` are equivalent user-facing Auto. Compiled execution `publishMode` remains `none | branch | pr | auto`. The compiler resolves `default` and the compound PR-and-merge selection before workers or helpers execute. Literal historical `auto` retains its Skill-owned meaning under its recorded ingress/history contract, never the meaning of generic default.

Context bindings are resolved before required-field validation and preset expansion. Bound fields are execution projections, not authored duplicates. Conflicting or redundant caller-owned copies in the new contract are rejected with an actionable path; a supported legacy decoder may collapse proven equivalent historical values.

The compiler pins selected definitions, context/target identity, authored intent, resolved default behavior, child requirements, and effect owners in existing snapshot/plan evidence. It validates known composition conflicts before parent launch or tracker mutation. Unknown future targets are validated at the declared child boundary.

Explicit None is never promoted. An incompatible publishing Skill, parallel shared-branch batch without a qualified serial handoff, or missing required predecessor-code transfer produces a pre-effect error. A read-only step or non-publishing coordinator does not erase the scope's PR intent.

The following are semantic examples, not promises that current deployed schemas already accept target-state authoring:

```json
{"task": {"publish": {"mode": "default"}}}
```

```json
{"task": {"publish": {"mode": "pr_with_merge_automation"}}}
```

An admitted implementation batch can retain the second authored selection while its coordinator compiles to None and its children compile to PR plus merge automation. The child automation may invoke a Skill-owned Auto resolver as an implementation of that same intent.

### 9.4 Idempotency

Create deduplication is scoped to owner, workflow type, and idempotency key. A repeated equivalent logical request returns the existing execution without creating another.

For the new authoring/fan-out contract, reuse also verifies admitted context, target, publication policy, and definition evidence. A conflicting request under the same key cannot mutate the existing execution or report it as accepted under the new intent. It returns a bounded conflict with the existing authorized reference where visible. This is a coordinated contract change, not a claim that historical keys already have full request-digest validation.

Dependabot's cross-run repository/PR/head identity is preserved. A policy edit does not silently create a competing resolver for the same head or retarget an old one. Helpers report the existing child's actual disposition and use explicit execution controls for an authorized replacement.

### 9.5 Create Result

Successful creation allocates workflow/run identity, starts `initializing` unless the admitted lifecycle calls for a preceding schedule gate, and materializes safe metadata. The response is an `ExecutionModel` with status 201. Persistence/enqueue acceptance is not objective success.

Title resolution is deterministic. A meaningful caller title wins. Otherwise combine the selected capability/preset label with up to two structured targets such as an issue, PR, branch, or failing check. Bookkeeping step IDs are not targets. A label without a target remains a fallback. Titles are bounded to 150 characters.

### 9.6 Errors

Domain create validation uses 422 `invalid_execution_request`, with field-addressable details for context/policy conflicts. Malformed bodies may use framework validation errors. Authentication uses the auth layer's 401/403 behavior. A conflicting idempotent intent is a distinct conflict, not a successful create. Error semantics and generated clients must be updated together when the new authoring boundary is enabled.

## 10. List Executions

`GET /api/executions` supports workflow type/state, authorized owner type/ID, entry, repository, integration, registered runtime/Skill facets, page size, and opaque page token.

| Parameter | Default/constraint |
| --- | --- |
| workflowType, state, ownerType, ownerId, entry, repo, integration | Optional filters |
| targetRuntime / targetRuntimeIn | Registered canonical runtime facet |
| targetSkillIn | Registered primary Skill facet |
| pageSize | Default 50; range 1–200 |
| nextPageToken | Opaque continuation token |

Non-admin scope is always the authenticated owner. Runtime/Skill attributes use membership queries over their registered one-item `KeywordList` values. Default ordering is meaningful `updatedAt` descending by one-minute stability bucket, then queued order descending within a bucket, then workflowId descending. Small refresh differences do not reorder otherwise stable queued rows.

A null next token means no further pages. Offset-based implementation details are not public cursor semantics. Count confidence follows section 8.4.

Success is 200 with `ExecutionListResponse`. Unauthorized owner scope is 403 `execution_forbidden`. Invalid tokens use 422 `invalid_pagination_token`; older route wrappers may also report some filter errors through that code. Clients do not treat such errors as empty results.

## 11. Describe Execution and Steps

`GET /api/executions/{workflowId}` returns 200 `ExecutionModel` or 404 `execution_not_found` for absent/invisible work. Reconstruction uses the exact input/plan refs and section 8.5's authored-versus-derived distinction, not mutable defaults.

### 11.5 Step Ledger

`GET /api/executions/{workflowId}/steps` returns the latest/current run's bounded ledger without forcing clients to parse logs. The response identifies `workflowId`, `runId`, `runScope`, and `steps`.

Each row includes:

- logicalStepId, order, title, tool, dependsOn;
- status, waitingReason, attentionRequired, attempt, startedAt, updatedAt;
- timing.startedAt, endedAt, durationMs, elapsedMs, serverNow, precision, preserved;
- summary, checks, lastError;
- refs.childWorkflowId, childRunId, agentRunId;
- artifacts.outputSummary, outputPrimary, runtimeStdout, runtimeStderr, runtimeMergedLogs, runtimeDiagnostics, providerSnapshot.

`logicalStepId` is stable within its plan. Attempts are scoped to workflowId/runId/logicalStepId. Checks contain structured verdict/retry evidence. A step agentRunId can coexist with a top-level observability binding.

Timing is logical step timing, not runner workload duration. Precision is `exact`, `live`, `fallback`, or `unavailable`. Live elapsed time is as of serverNow; fallback is displayable but not exact terminal evidence. Preserved rows set preserved true and display original timing rather than newly executed work. Do not infer this from `workload.durationSeconds`.

`GET /api/executions/{workflowId}/steps/{logicalStepId}/step-executions` returns the same timing object per attempt; UI totals can derive from those attempt-local values. Absent/invisible step-route targets use the same ownership-preserving 404 behavior.

Derived per-step publication disposition does not create a step-authoring override. Accepted publication or Skill evidence is consumed only from its authoritative producer, not untrusted raw metadata keys.

## 12. Update Execution

`POST /api/executions/{workflowId}/update` accepts:

| Field | Meaning |
| --- | --- |
| updateName | UpdateInputs, SetTitle, or RequestRerun; default UpdateInputs |
| inputArtifactRef / planArtifactRef | Candidate replacement refs |
| parametersPatch | Small structured patch |
| title | Required for SetTitle |
| idempotencyKey | Update reconciliation key |

UpdateInputs replaces/adds refs and patches admitted parameters. No-op updates can succeed. Executing/awaiting-external work may accept supported changes for the next safe point; major changes may require a newly admitted run through the lifecycle owner. SetTitle changes display metadata immediately and does not change execution authority.

RequestRerun requests a clean re-execution from confirmed/replacement inputs. Supported lifecycle paths preserve workflowId and allocate a new runId. Terminal rerun availability is determined by the authoritative action/lifecycle contract, not an assumption that a closed Temporal run accepts an update. When unsupported or no longer allowed, the response is non-accepted rather than a queue fallback.

### Response and Idempotency

The response includes required `accepted`, `applied = immediate | next_safe_point | continue_as_new`, and a human-readable message. A 200 response alone does not mean accepted. Older update idempotency retains only the most recent key/response and is not an arbitrary historical deduplication ledger.

A terminal/unsupported update returns 200 with accepted false, applied immediate, and an explanation. Missing/invisible targets use 404; invalid updates use 422 `invalid_update_request` or framework errors.

### Context and Policy Immutability

The single authored context/publication rules apply equally to UpdateInputs and RequestRerun. A patch cannot directly change compiled mode, overwrite bound inputs, revive a preset override, or change a child's policy through the parent's projection.

An admitted scope's effect authority is immutable for its active work and already accepted children. An authoring edit before admission can recompile the draft. A change after target/candidate/child admission requires the supported new-admission/rerun/continuation path with lineage, not an in-place broadening at a nominal safe point. Display-title changes and other non-authority updates retain their normal behavior.

Reruns reconstruct authored intent and its original default-resolution evidence. Changed definitions/defaults are visible and revalidated. Old None-to-Auto normalization or mixed per-step policies cannot silently become a new authority grant. Historical artifacts and prior child results remain unchanged.

## 13. Signal Execution

`POST /api/executions/{workflowId}/signal` accepts required signalName, optional payload defaulting to an empty object, and optional payloadArtifactRef.

| Signal | Required payload | Behavior |
| --- | --- | --- |
| ExternalEvent | source, event_type | Record the event/ref; clear only the relevant external wait when not paused. |
| Approve | approval_type | Apply the approval under the owning gate; normal lifecycle resumes only when its requirements are satisfied. |
| Pause | Signal-specific payload | Set paused overlay and waiting metadata while preserving the underlying state. |
| Resume | Signal-specific payload | Clear the pause overlay and return to the underlying scheduled/dependency/slot/active gate. |

Remediation approval uses its durable pending approval record and authenticated endpoint described in section 2. Expired, terminal, self-approved, or reviewer-ineligible decisions fail with bounded validation errors.

Success is 202 with the materialized execution. Terminal signals and invalid signal/payload combinations are rejected, normally 409 `signal_rejected`; absent/invisible targets use 404. A signal or approval cannot replace the authored publication intent, broaden repository authority, or authorize duplicate effects.

## 14. Cancel Execution

`POST /api/executions/{workflowId}/cancel` accepts an optional body with reason and graceful defaulting true.

Graceful cancellation clears relevant pause/wait flags and records canceled/closeStatus canceled with the reason. Forced termination records failed/closeStatus terminated and a `forced_termination:` summary. An already terminal execution is returned unchanged. Success is 202 with the execution; invisible targets use 404.

Cancellation acceptance is not proof of physical runtime teardown or remote-effect rollback. Independently verified pushes, PRs, merges, or accepted children remain evidence. Preservation and credential release follow their existing lifecycle owners. Policy inheritance does not imply new recursive cancellation semantics.

## 15. Error Model

Structured domain errors use:

```json
{
  "detail": {
    "code": "invalid_execution_request",
    "message": "The selected publication policy is incompatible with this workflow."
  }
}
```

Input/policy errors additionally identify the actionable authored field and affected consumer when available. A missing bound repository points to the workflow source, not an invisible Skill input.

Existing domain codes include execution_not_found/404, execution_forbidden/403, invalid_execution_request/422, invalid_update_request/422, invalid_pagination_token/422, and signal_rejected/409. The coordinated single-context boundary also distinguishes conflicting idempotent intent from successful reuse. Framework JSON/type/coercion errors remain possible before route logic.

Errors are safe and bounded. They do not contain credentials, unrestricted parent data, raw provider sessions, or private target content beyond authorized identifiers.

## 16. Task-shaped and Historical Payloads

The task-shaped transport may map to Temporal work, but it is not an alternate policy compiler. WorkflowId remains canonical; compatibility taskId equals workflowId. Supported `task.tool`/`step.tool` selectors and older Skill aliases use the established selector normalization, with `type: skill` where required by that transport.

New authoring has one repository/source target, one applicable branch, and one publication selection. It cannot use old Skill args, preset `publish_mode`, `startingBranch`, `targetBranch`, or a worker-facing mode to create another authority.

Historical decoding is versioned and evidence-preserving. Old literal Auto stays Skill-owned; old None-to-Auto coercion is confined to recorded old execution semantics. A coordinator's old local None and explicit child PR policy reconstruct as one PR intent only when provenance proves that meaning. Conflicts and unknown origins require review, not inference.

Replay and supported resets retain original bytes/digests and workflow command semantics. New drafts and schedule occurrences cross current admission. Mixed API/worker versions cannot receive a new authoring value they would reinterpret or default. The rollout boundary rejects incompatible consumers before launch without rewriting historical hashes.

## 17. Implementation Boundary Notes

The current projection row can materialize identifiers, lifecycle state, attributes/memo, artifact refs, pending updates, counters, and timestamps. Those implementation details do not replace Temporal history or authoritative artifacts.

Continue-As-New/rerun uses stable workflowId with a new runId and refreshed lifecycle where supported. Projection/Visibility-backed list implementations preserve successful pages when bounded count enrichment fails. Current pagination/filter error naming is not a reason to change input authority or silently report empty work.

The new authoring/compiler rules require coordinated schemas, generated clients, preset/helper consumers, and compatible workers. This documentation-only specification does not implement or qualify that cutover.

## 18. Change Rules and Conformance

Changes identify whether they are additive, behavioral with stable shape, or breaking and coordinated. Supported root types, update/signal names, error semantics, ordering/count confidence, identity rules, and authored-versus-compiled mode semantics are contract changes.

Conformance exercises actual API/compiler/preset/fan-out boundaries for:

- equivalent direct/task-shaped/UI/MCP authoring;
- omission versus explicit default and the distinct compiled Auto protocol;
- one context binding with required-input validation and conflicting-copy rejection;
- non-publishing coordinators with inherited PR/Auto children through nesting;
- explicit None, existing-PR target derivation, non-default bases, branch collision prevention, and required code handoffs;
- partial fan-out, lost acknowledgement, equivalent retry, and policy-conflicting idempotency reuse;
- immutable admitted intent across update, schedule, rerun, recovery, and mixed-version history decoding;
- truthful authored/local/child/result projections without treating enqueue or process exit as publication evidence;
- ownership, bearer scope, stale parent/run identity, and attempts to bypass inheritance through raw payloads.

Use existing selected suites and release qualification. A documentation edit or isolated schema test is not a passed deployment journey.

## 19. Summary

The API is execution-oriented, authenticated, ownership-scoped, and grounded in workflowId. It preserves explicit lifecycle controls while compiling one authored repository/branch/publication intent for every supported producer. Steps and children receive derived contracts and independently verified outcomes, not competing user-authored settings.
