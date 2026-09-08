# Workflow Editing System

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-06  
**Audience:** Workflow lifecycle, API, draft reconstruction, and dashboard contributors  
**Authority:** Shared Create/Edit/Rerun authoring experience, reconstruction of original intent, permitted update boundaries, and immutable input lineage. Execution lifecycle and publication schemas remain with their providing contracts.  
**Owning Surface:** Workflow input reconstruction and Edit/Rerun integration  
**Related Docs:** [Create Page](../UI/CreatePage.md), [Executions API Contract](../Api/ExecutionsApiContract.md), [Workflow Publishing](WorkflowPublishing.md), [Workflow Architecture](WorkflowArchitecture.md), [Temporal Scheduling](../Temporal/TemporalScheduling.md), [Settings System](../Security/SettingsSystem.md)  
**Related Implementation:** `buildTemporalSubmissionDraftFromExecution`, `buildTemporalArtifactEditUpdatePayload`, and the existing UpdateInputs/RequestRerun lifecycle owners.

## 1. Purpose

Create, Edit, and Rerun use the same `/workflows/new` form and Temporal-backed execution contracts. Operators can reconstruct supported executions, change permitted task inputs, and request a newly admitted run without queue-era assumptions or rewriting historical artifacts.

Repository/source context, branch context, and publication are authored once under [Workflow Publishing](WorkflowPublishing.md) and [Create Page](../UI/CreatePage.md). Reconstruction preserves those choices separately from compiler-bound Skill arguments and per-execution publication modes.

This document defines target behavior, not a claim that every current lifecycle path already supports editing or terminal rerun.

## 2. Scope

In scope are the shared submit page, detail/list entry points, reconstruction from execution inputs and artifacts, supported input updates, and explicit rerun requests. Recurring definition editing belongs to the schedule owner but uses the same authored-input/compiler contract.

Out of scope are queue jobs, inline edit-only modals, historical artifact mutation, non-Temporal execution, and a second publishing or profile-selection surface.

## 3. Design goals

### 3.1 Temporal is the source of truth

Execution identity, capabilities, input evidence, and lifecycle state come from authoritative Temporal/execution contracts. No queue-job payload is required.

### 3.2 One submit experience

Create, Edit, and Rerun reuse the normal form, validation, context bindings, presets, attachments, Runtime/Profile selection, and publication explanation.

### 3.3 Preserve create-form ergonomics

The form reconstructs one source/repository, one applicable authored branch, and one publication selection. Historical starting/target branch pairs or duplicate Skill inputs are not silently reintroduced as ordinary controls.

### 3.4 Respect lifecycle correctness

Active executions accept updates only when the backend exposes and validates that capability. Terminal executions are not edited in place; supported reruns use the owning lifecycle path. Failed and canceled executions are first-class rerun candidates when `actions.canRerun` is true.

A closed Temporal run is not assumed to accept an Update merely because the UI calls the shared endpoint. Unsupported actions return explicit non-acceptance.

### 3.5 Preserve auditability

Maintain original workflow/run, operator action, new input/plan artifacts, accepted update or rerun identity, and child/scope lineage. Definition updates and changed publishing choices are visible rather than inferred from current defaults.

### 3.6 Avoid in-place artifact mutation

New authored content creates new artifact references. Original inputs, resolved plans, publication evidence, and saved work remain immutable.

### 3.7 Remove legacy coupling

No editJobId, queue resubmit, queue-first route, or old input alias is a new-write authority.

## 4. Canonical model

### 4.1 Editable object

The durable object is an execution identified by workflowId, with exact run/admission evidence where an action depends on it. A new run does not retrospectively change already-created children under a prior run's policy.

### 4.2 Supported workflow type

The initial supported type is MoonMind.UserWorkflow. Other types require an explicitly added capability and lifecycle contract.

### 4.3 Modes

| Mode | Entry | Submission |
| --- | --- | --- |
| Create | No edit/rerun parameter | New execution |
| Edit | editExecutionId names a supported active execution | Validated UpdateInputs |
| Rerun | rerunExecutionId names a supported terminal execution | Validated RequestRerun through the lifecycle owner |

### 4.4 Lifecycle model

UI availability follows backend capability flags, not inferred state alone. Rerun allows supported edits to a reconstructed draft, but is fresh admission of the submitted intent. It does not rewrite the original terminal result or restore prior credentials/approvals.

## 5. Route model

### 5.1 Canonical routes

```text
/workflows/new
/workflows/new?editExecutionId=<workflowId>
/workflows/new?rerunExecutionId=<workflowId>
```

### 5.2 Deprecated routes and params

New authoring never uses `/tasks/queue/new`, editJobId, queue-job updates, or queue resubmit terminology.

### 5.3 Mode resolution order

Resolve rerunExecutionId, then editExecutionId, then Create. A malformed or unauthorized requested mode is reported, not silently downgraded to unrelated new work.

## 6. Entry points

### 6.1 Workflow detail page

Show Edit when actions.canUpdateInputs and Rerun when actions.canRerun. Navigate to the shared form. Failed/canceled work retains its saved outputs and original result while a rerun draft is reviewed.

### 6.2 Workflow list or card surfaces

Optional list/card actions use the same routes and backend capabilities; they do not maintain another reconstruction implementation.

## 7. Submit-page behavior

### 7.1 Shared page, mode-specific behavior

Mode changes title, CTA, data source, submission handler, and genuinely unsupported controls. It does not create another policy selector.

### 7.2 Mode-specific UI expectations

| Mode | Title | CTA |
| --- | --- | --- |
| Create | New Workflow | Create Workflow |
| Edit | Edit Workflow | Save Changes |
| Rerun | Rerun Workflow | Rerun Workflow |

### 7.3 Hidden or constrained controls

Recurring controls and queue-era fields are absent in execution Edit/Rerun. Unsupported controls are disabled or omitted with a reason. Material context/policy conflicts are never hidden inside Advanced mode.

### 7.4 Loading behavior

Resolve the execution, validate visibility/type/action capabilities, load exact input and definition/plan references, reconstruct authored intent, validate context bindings and historical compatibility, then render. Partial or uncertain reconstruction is clearly labeled and blocks unsafe submission instead of presenting a misleading complete draft.

## 8. Prefill model

### 8.1 Draft reconstruction helper

`buildTemporalSubmissionDraftFromExecution(execution)` remains the shared reconstruction entry point. It consumes safe authoritative evidence rather than current catalog defaults as a replacement for missing history.

### 8.2 Draft data sources

Sources include execution details, original authored parameters, input artifacts, selected preset/Skill definition evidence, and admitted runtime configuration. Compiled plan projections explain what ran, but they are not automatically user-authored values.

### 8.3 Fields that should prefill

Prefill Runtime/Profile, model/effort where authored, workspace source/repository and access intent, one applicable authored branch, authored publication selection, instructions, Skill/Preset selection, task-specific step inputs, expansion/provenance, dependencies, and attachments.

New authored publication values are default, none, branch, pr, and pr_with_merge_automation. Auto is default or omission. The compiled modes none/branch/pr/auto are separate execution facts. A coordinator's local None must not prefill “Do not publish code” when its original scope requested PR children.

A PR locator remains task input. Resolved PR head/base is read-only target evidence, not another branch override. Canonical source/destination roles remain distinct only where supported and explicitly named.

### 8.4 Instructions and artifacts

Whether instructions were inline or artifact-backed, the form reconstructs their readable content without requiring the user to understand storage. Generated instructions retain provenance and are rebound/re-expanded as required after context changes; stale interpolated repository or branch values cannot remain authoritative.

### 8.5 Fallback behavior

Missing evidence, unsupported historical mode, conflicting copies, or inaccessible artifacts produce explicit reconstruction diagnostics. Preserve safe task values, but do not permit a partially reconstructed authority-bearing request to submit as if it were exact.

### 8.6 Authored, defaulted, bound, and derived values

| Original evidence | Reconstruction rule |
| --- | --- |
| Explicit authored selection | Preserve it and validate under the supported new-admission contract. |
| Auto with recorded definition/default resolution | Show Auto plus the recorded resolved behavior. Changes to definitions/defaults require visible review. |
| Compiler-bound equivalent repository/branch argument | Rebind from the single context, not an editable copied value. |
| Coordinator-local None with proven child PR intent | Reconstruct the one scope PR/default selection and explain the coordinator role. |
| Historical literal auto | Preserve Skill-owned meaning, not generic Auto by string substitution. |
| Historical None promoted to Auto | Preserve old execution history; require an explicit reviewed new selection rather than silently granting publication. |
| Recorded omitted mode resolved by the retired workspace fallback | Preserve the proven effective choice explicitly with provenance and validation, or require review under SettingsSystem section 10.6. |
| Conflicting copies, mixed per-step policies, ambiguous two-branch authoring | Show conflict and require a compatible composition or separately authored workflows. |

Unknown origin is not implicit user consent. Compatibility collapse requires proven equivalence. Raw JSON and old argument aliases cannot bypass the new one-context contract. In particular, Checkpoint Branch and PR Resolver reconstruction cannot use historical startingBranch/targetBranch or non-default checkout context as new active branch/PR selectors.

## 9. Submit semantics

### 9.1 Create mode

Normal creation resolves bindings/defaults, expands presets, compiles the single publication scope, validates targets/known child handoffs, and admits work. Other producers use this same compiler.

### 9.2 Edit mode

Supported task input edits externalize new content as needed and send UpdateInputs to:

```http
POST /api/executions/{workflowId}/update
```

The payload includes updateName, candidate refs and parametersPatch as supported. A helper such as buildTemporalArtifactEditUpdatePayload can assemble it but is not the authority.

An unadmitted draft may change context and recompile normally. Once execution authority, candidate identity, or children are admitted, Edit cannot broaden the publication scope, replace repository/PR target, mutate already-created children, or patch worker-facing compiled modes at a nominal safe point. Such changes require the supported newly admitted run/continuation/rerun path with lineage. Non-authority task updates and title changes retain their declared lifecycle behavior.

### 9.3 Rerun mode

RequestRerun uses confirmed or supported edited form values through normal admission. Rerun mode targets a terminal source, so the service creates a new workflowId/runId linked to that immutable source; the UI follows the returned `execution.workflowId` and `execution.redirectPath`. Only a separately supported active-run Continue-As-New retains workflowId. The existing `applied = continue_as_new` response value alone does not distinguish these operations. Replacing an admitted Omnigent plan requires a new execution through normal admission; the update route rejects replacement inputs with 409 `omnigent_execution_plan_replacement_required`. See [Executions API Contract, section 12](../Api/ExecutionsApiContract.md#12-update-execution) for response and validation semantics.

A rerun changes neither historical child policy nor remote effects already performed. Reusing a logical child idempotency key with conflicting target/policy must produce an explicit conflict or existing-child disposition, not a duplicate launch or false acceptance of the new settings.

The UI checks the actual accepted response. Terminal rerun support is supplied by the lifecycle owner, never assumed from a 200 response or implemented by a queue fallback.

### 9.4 No queue fallback

Unsupported Temporal update/rerun fails explicitly. It cannot silently create a queue job or another publishing path.

### 9.5 Auto and explicit selections

While editing task inputs, Auto recomputes its recommendation from the selected definition and current inputs under the draft's update policy. An explicit None/Branch/PR choice survives and is validated. Returning to Auto is a deliberate action. The retired workspace default and environment aliases do not influence new resolution or hydrate an apparently explicit value.

Before submission show the effective output, child scope, possible merge effects, and required branch/candidate handoff. A resolver requiring pushes is incompatible with None even in fix-only. A batch requiring predecessor code cannot silently lose its handoff when publication/merge is disabled.

## 10. Artifact rules

### 10.1 No historical mutation

Inputs, plans, Skill snapshots, saved work, and terminal evidence are immutable historical artifacts. A changed draft creates new refs.

### 10.2 Preserve lineage

Record the source workflow/run, operator action, definition/context changes, new artifacts, admitted scope, and actual update/rerun disposition. Restored workspace bytes are not old session, lease, approval, or publication authority.

### 10.3 Externalization policy

Large instructions and input evidence remain artifact-backed through existing storage rules. No second draft or publication database is needed.

## 11. API contract

### 11.1 Read contract requirements

Describe supplies workflowId/type, exact current/source run identity, authored input refs/parameters, selected definition evidence, permitted actions, and enough safe runtime/context/publication information to reconstruct the form. It distinguishes authored policy from local compiled mode and observed effects.

### 11.2 Update contract requirements

UpdateInputs and RequestRerun accept supported structured patches and refs, revalidate lifecycle/freshness and single-context semantics, and reject forged bound/compiled fields. Current authority is checked independently of historical evidence.

### 11.3 Response handling

accepted and applied timing are explicit. Immediate, next_safe_point, and continue_as_new responses are shown accurately. A 200 non-accepted result is not success. Accepted task updates do not imply permission for an in-place authority change.

## 12. Redirect and refresh behavior

After confirmed success, return to the original workflow's detail route or the returned newly admitted lineage target, using latest-run view where applicable. Refresh authoritative state. Do not redirect to a generic queue or infer a new run before the backend confirms it.

## 13. Validation and guardrails

Block unsupported workflow types/actions, unavailable required artifacts, incomplete reconstruction, stale execution state, conflicting context copies, incompatible publishing/finish requirements, changed definition authority without review, or server-denied target/access.

Errors point to the visible authored source control and consuming step, not an invisible bound Skill input. Safe task values survive. Changing context invalidates stale lookups/expansions rather than restoring them after a failed submit.

## 14. Observability and audit

The original execution, operator action, new artifact identities, accepted/rejected/deferred result, rerun instance, and scoped child links remain traceable. Verified old publication and saved outputs survive failed rerun attempts. A child's local mode cannot rewrite the displayed ancestor policy.

## 15. Non-goals

No separate edit-only form, queue-first workflow, manifest-run editing, recurring-definition editing in this view, inline quick-edit authority bypass, historical artifact mutation, or generic per-step publication override.

## 16. Deprecated legacy references

New UI/docs use workflow Edit/Rerun rather than editJobId, `/tasks/queue/new`, queue-job update, or queue resubmit. Read-only historical displays can show old values with clear provenance but never make them active new controls.

## 17. Completion criteria

Conformance proves shared Create/Edit/Rerun UX through actual read/compiler/update boundaries; failed/canceled supported reruns; one authored context and policy; immutable artifacts; correct acceptance/redirect behavior; and preserved original-versus-new run lineage.

Regression cases cover coordinator None with PR/Auto children, old None-to-Auto coercion, configured workspace-default retirement, conflicting repository/branch copies, unchanged versus changed definition digests, explicit choices surviving Run changes, new-admission requirements for changed authority, stale lookups, and idempotency conflicts. UI-only prefill tests do not establish safe execution or replay.

## 18. Implementation tracking

This document is the canonical target-state contract. Implementation sequencing, cutover constraints, and execution evidence belong in the existing issues or `docs/tmp/`. Historical support and deployed qualification are not inferred from documentation status.
