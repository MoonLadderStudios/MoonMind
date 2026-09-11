# Create Page

**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Status:** Proposed  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-06  
**Audience:** Dashboard, workflow authoring, schema-form, and API contributors  
**Authority:** Create-page information architecture, single-context presentation, schema-driven task inputs, preview/error behavior, and submission/reconstruction UX. Backend providing contracts remain authoritative for execution and policy.  
**Owning Surface:** Shared /workflows/new authoring form and its catalog/compiler consumers  
**Related Implementation:** `frontend/src/`, `frontend/src/styles/dashboard.css`, and existing workflow draft/submission helpers.  
**Related Docs:** [Workflow Publishing](../Workflows/WorkflowPublishing.md), [Input Schema Guidance](../Steps/InputSchemaGuidance.md), [Step Types](../Steps/StepTypes.md), [Settings System](../Security/SettingsSystem.md); further related owners are listed below.

The Create page is MoonMind's primary workflow composition surface. It lets a user describe work, select its execution context, configure task-specific steps, and submit without understanding internal orchestration roles.

This specification describes the long-term UI, not a claim that every current control or backend path implements it. Workflow Publishing owns publishing semantics; Input Schema Guidance owns context bindings.

## Design Principles

- One workflow-level source of truth for repository context, branch context, and publishing. Equivalent Skill/Preset fields are not separately editable, including in Advanced mode.
- Steps remain the primary composition unit. Their type selects a capability and its task-specific schema, not another execution-context configuration.
- Presets can be configured without expansion and are expanded authoritatively by the backend at submission.
- One shared schema renderer serves Skills, Presets, and supported Tools. Reusable widgets are allowed; preset- or Skill-name branches in page code are not.
- Defaults are executable, explainable behavior. Explicit choices survive composition changes until the user changes them or resolves an incompatibility.
- Backend context, target, expansion, authorization, and publishing validation are authoritative.
- Advanced disclosure exposes meaningful specialization, not duplicate authority or hidden contradictory values.

The generic runtime is labeled **Omnigent**. Runtime and one ordinary **Profile** selection resolve the supported execution configuration through the runtime-selection owner. Publishing does not introduce a Target/Harness/Agent Profile/Host Class choice or select another runtime.

## Primary User Flows

### Simple Workflow

The user enters instructions, chooses repository/source and branch when needed, accepts the normal step or selects another capability, reviews the single publishing selection and its explanation, and clicks **Start Workflow**. A goal without an explicit step, plan, tool/Skill, or preset can be conservatively mapped to a seeded preset by the backend. The same compiler then validates and admits it.

### Preset Workflow

The user selects Preset, chooses a catalog entry, and fills task-specific required inputs such as a Jira issue, issue range, or document directory. Repository, branch, and publication are supplied from workflow context. The user may Apply or leave the preset unexpanded. Start Workflow validates context/inputs, expands the definition, checks the final plan, and submits.

### Batch Workflow

The user selects a batch and its task inputs, such as Jira project/status plus Run, or GitHub issue range. Publishing is selected once for the batch. An adjacent explanation distinguishes coordinator behavior from child output. No separate parent None and child PR selectors appear.

Example:

```text
Repository  MoonLadderStudios/MoonMind
Branch      release/1.2
Publishing  Auto
            Create one pull request per implementation child against release/1.2.
            This batch workflow queues children and does not publish changes itself.
```

Changing Batch Jira's Run from Verify to Implement updates that explanation while Auto is selected. It does not overwrite an explicit None or PR choice. Unsupported combinations show a correction at the shared publishing control before launch.

### Existing Pull Request

The user selects a PR as the task target. Its repository, head, and base are resolved through the authorized target surface and displayed read-only. A head-branch locator can be a supported alternative to the PR reference, not a second simultaneous editable branch selector.

A PR-resolution batch displays “Branches come from discovered pull requests.” It never forces all children onto the coordinator's branch. Auto explains whether the selected resolver repairs and merges, or repairs without merging under `fix_only`. Historical startingBranch/targetBranch or non-default generic checkout branches are not new PR-locator fallbacks.

## Page Structure

The information architecture has five regions. Their visual placement may vary, but each value has one editable owner.

| Region | Contents |
| --- | --- |
| Workflow overview | Title/generated title, workflow instructions, Runtime and Profile, dependencies/starting context |
| Repository and publishing | One source/repository control, its applicable branch control or derived-target display, one publishing selector, effective outcome explanation |
| Steps | Ordered steps, type/capability selectors, task-specific schema inputs, Apply/Reapply, validation and provenance |
| Context and attachments | Issue context, file/artifact references, additional task notes |
| Review and submit | Final validation and output summary, expanded-plan summary when needed, Start Workflow |

Repository and publishing controls may remain near the bottom of the current layout. Skill/Preset cards do not repeat them elsewhere. A read-only “Uses workflow repository” explanation is not another input.

A no-repository source does not render a dummy repository or branch. Genuinely separate source and later-publication destination roles follow [Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md), use explicit role labels, and appear only for supported operations. They are not ambiguous generic override fields.

## Step Authoring Model

A draft contains ordered typed steps. The canonical types remain those defined in [Step Types](../Steps/StepTypes.md):

| Step type | Meaning |
| --- | --- |
| Tool | Invoke a typed, bounded, policy-checked operation |
| Skill | Invoke a resolved portable Skill bundle |
| Preset | Expand a reusable composition |

Instructions, Managed Agent, and External Agent may be friendly shortcuts that normalize to Skill execution. A supported controlled script runner is a typed Tool, not another canonical Step Type. Runtime choice remains configuration under its existing owner. Labels do not create alternate authoring authorities or additional repository, branch, or publishing selectors.

Example unexpanded preset:

```json
{
  "type": "preset",
  "presetId": "jira-orchestrate",
  "title": "Jira Orchestrate",
  "inputs": {"jira_issue": {"key": "MOON-123"}},
  "expansionState": "not_expanded"
}
```

An ordinary chain such as Assess, Implement, Test, and Update Documentation uses one repository and publication intent. Read-only steps do not need None selectors. Incompatible publishing owners or unrelated repository targets require a compatible declared composition or separate workflows. No per-step publish override is offered as a universal escape hatch.

## Schema-Driven Inputs and Bindings

Capabilities expose a normalized contract:

```json
{
  "id": "jira-orchestrate",
  "kind": "preset",
  "label": "Jira Orchestrate",
  "description": "Build and execute a workflow from a Jira issue.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {}
}
```

The renderer supports standard types, titles, descriptions, defaults, required fields, properties, arrays, enums, required alternatives, formats, and registered semantic hints.

Before rendering editable fields it identifies authoritative `x-moonmind-context-binding` declarations. Bound repository, branch, and publication values are supplied by context, not copied into form state. The renderer shows their source or resolved target when useful and routes errors to the actual editable source control.

For remaining Skill inputs, guided mode shows root required fields and every field participating in root `oneOf`/`anyOf` required alternatives. Other authored properties appear in Advanced mode. A required bound field is satisfied by valid context and does not force a duplicate input into guided mode. Advanced/raw JSON cannot override it.

Unknown harmless UI hints degrade safely. Unknown authority-bearing bindings, incompatible target roles, and unavailable required context block with actionable errors. A schema-less Skill remains selectable with instructions and attachments under the existing workflow context.

The binding contract is not inferred from names such as `repository` or `branch`. A comparison branch or distinct target can be a meaningful input when the capability declares that role. A portable `repo` argument can remain in its external interface while MoonMind supplies it at the adapter boundary.

## UI Schema and Widgets

Validation and binding semantics belong to normalized schema/execution contracts. UI schema supplies safe presentation only.

```yaml
inputSchema:
  type: object
  required: [jira_issue]
  properties:
    jira_issue:
      type: object
      title: Jira issue
      required: [key]
      properties:
        key: {type: string}
        summary: {type: string}
        description: {type: string}
        url: {type: string, format: uri}
uiSchema:
  jira_issue:
    widget: jira.issue-picker
    searchPlaceholder: Search Jira issues
    allowManualKeyEntry: true
```

Equivalent safe semantic or colocated widget hints are supported. The page understands a reusable Jira picker, not a special Jira Orchestrate page branch.

The local widget registry provides text, textarea/markdown, numeric, boolean, select, multi-select, structured JSON, Jira/GitHub issue, repository/branch, profile/model, and file-reference controls. Unknown widgets use a safe standard field when possible; otherwise they show an unsupported-widget error while preserving entered values. Widget availability never overrides target authorization or an authoritative binding.

## Jira Issue Picker

The shared picker supports authorized search by key, title, status, or assignee where available, safe manual key entry when allowed, and optional display enrichment. Its durable minimum is:

```json
{"key": "MOON-123"}
```

Optional summary, description, URL, status, and assignee do not become mandatory just because the picker once fetched them. Backend validation obtains required trusted details when needed. Integration errors preserve the entered key and explain setup or permission failures. Issue context can also come from attachments or trusted lookup, but all paths feed the same draft model.

## Repository and Branch Selection

The ordinary repository-backed flow renders one repository/source selection and one branch role. The branch picker uses authorized provider data with a supported manual fallback, not an unconditional `main` default.

| Operation | Branch presentation |
| --- | --- |
| Work without publication | Starting branch |
| Push to branch | Branch being updated |
| Create PR | Base branch, with generated head information |
| Existing PR | Resolved PR head and base, not editable competing branch fields |
| PR batch | Per-target branch explanation |

The generic label is Branch unless the current operation benefits from an explicit Base branch or PR head label. `Target Branch` is not a second ordinary authoring field. Generated work branches are runtime-owned. Show an exact name only when it has been durably selected; otherwise say a work branch will be generated, or generated per child.

A dependency may offer a verified candidate as a proposed starting point. It does not silently replace an explicit authored base or prove that predecessor code is merged. Required handoffs follow the dependency/publishing contracts.

Repository, branch, and target lookups are keyed to the principal, source/connection identity, repository, and relevant query. Out-of-order responses are discarded after any source change. Changing context invalidates incompatible selected PRs, cached branch options, previews, and generated bindings while preserving recoverable task input. A PR URL naming a different repository cannot silently switch the selected authority.

### Text-first branch input

Authored branch text is authoritative user input. Autocomplete is optional assistance: typing and pasting update the draft immediately, suggestions never overwrite the text or move focus, and submission reads the latest authored value rather than the last suggestion response.

- Form initialization and repository changes resolve only small default-branch metadata. The client never enumerates the whole branch collection automatically.
- Empty-input suggestions show the verified repository default first, then a small user-scoped recently-used list for that repository (recorded from accepted submissions, bounded, deduplicated with case preserved), capped at 20 visible suggestions.
- Typed search is debounced and served as one bounded page per operation with an explicit partial-list notice when more pages exist. One continuation action fetches one page; remaining pages are never drained automatically.
- A pasted or typed exact name is validated by an independent exact-name lookup, never by membership in the bounded suggestion page. The picker distinguishes not-checked, checking, found, definitively absent, and inconclusive (unavailable, denied, rate-limited, or unknown repository) outcomes. Only a definitive absence under a successfully authorized lookup reports the name as missing, and lookup failure never blocks authoring or submission: the authoritative backend validation still applies at submit time.
- Explicit clearing stays cleared: late default-branch or lookup responses cannot restore a cleared value or validate a newer draft.

## Publishing Control

There is one selector for the authored workflow/batch policy:

| Label | New authored value |
| --- | --- |
| Auto | `default`, also the meaning of omission |
| Do not publish code | `none` |
| Push to branch | `branch` |
| Create pull request | `pr` |
| PR with merge automation | `pr_with_merge_automation` |

Compiled execution `auto` is the Skill-owned protocol, not another UI option. The backend retains the authored selection separately from each step/coordinator's derived execution mode. Reconstructing a batch must not replace its PR policy with local coordinator `none`.

The selector shows only supported choices or disabled choices with reasons. It never silently switches a selected value to make a new step fit. Selection of Auto follows declared definition metadata and current task inputs, not model improvisation. An unresolved or conflicting default blocks submission rather than guessing a permissive policy.

The retired `workflow.default_publish_mode` and environment aliases are not lower-priority fallbacks and must not hydrate the selector as an apparent explicit value. For affected historical settings/drafts/schedules, the UI presents proven effective intent or a review requirement under [Settings System section 10.6](../Security/SettingsSystem.md#106-publication-default-ownership-and-retired-setting). It cannot silently change a configured None/Branch to PR or use an old global value instead of the selected composition's Auto.

### Effective Explanation

The explanation is returned or validated by the shared backend compiler and identifies the output behavior, scope, and consequential effects. Examples:

> Auto: Create one PR per implementation child. This batch queues work and does not publish changes itself.

> Auto: Verify issues without publishing code. Jira status updates remain enabled.

> Auto: Repair existing pull requests and merge them when the resolver's required gates pass.

> Auto: Repair and review this pull request without merging.

Auto must not be described as read-only when the selected workflow can merge. A task option such as Merge when ready or a review provider remains in the capability settings because it changes the task's finish behavior, not because it is another generic publish mode.

### Explicit Choice and Compatibility

An explicit None/Branch/PR selection survives changes to Run, task inputs, and preset expansion. Returning to Auto is an explicit change at the same shared control. Invalid combinations explain the conflict and focus the relevant control or step.

Explicit None constrains repository publication throughout scoped descendants. It is not a dry-run switch or a promise of no tracker effects. A resolver that must push cannot be made compatible by `fix_only`, which still pushes. The UI must show that incompatibility before launch, not silently promote None to Auto.

Parallel independent children cannot all update one shared branch without a declared serialized handoff. A dependent workflow that requires predecessor code cannot lose its merge/candidate-transfer contract because the user selected PR-only or None. These restrictions are capability-derived, not per-preset frontend conditionals.

Merge automation is an extension of the selected PR policy, not a second parallel selection flag. Advanced review/wait/timeout settings remain visible when applicable and cannot broaden the selected policy. A gate's draft-publication option cannot override None or Branch to create a PR.

## Preset Apply, Reapply, and Submission

Selecting a preset loads its metadata and task fields. Required inputs and context must be valid enough for Apply. Configured values remain in the draft even while unexpanded.

Apply calls the backend expansion service and inserts generated steps with provenance. Reapply uses the selected definition and current task/context inputs, with clear notice that edited generated content may be replaced. Neither action resets the explicit publishing selection or revives stale repository/branch values in generated text.

Start Workflow performs local feedback, then sends the authored draft to authoritative backend admission. The backend resolves bindings/defaults, validates inputs, recursively expands presets, compiles publication/target roles, validates the concrete plan and known child/handoff requirements, and starts the execution. It does not trust a client-supplied compiled mode or resolved provenance.

Goal-driven preset selection is not a separate UI mode. Explicit steps, selected tools/Skills, plans, and presets take precedence. Selected definition evidence and the resolution reason are retained.

## Validation UX

Validation includes shape/type checks, context-binding validation, authorized target lookup, preset expansion, and final plan/policy compatibility.

Errors identify the editable source and consuming step. For example:

```json
{
  "path": "steps[0].inputs.jira_issue.key",
  "message": "A Jira issue is required.",
  "code": "required"
}
```

A binding error identifies the workflow repository/branch or selected PR control, not a hidden Skill field the user cannot edit. A publication conflict identifies the single publishing selection and the incompatible objective. Values survive failures; a summary supplements rather than replaces inline errors.

Static conflicts are rejected before parent launch, issue creation, or child enqueue. Dynamic discovery failures are reported as blocked/partial outcomes with exact accepted child links, not disguised as full batch success.

## Context Retrieval Controls

Context retrieval/RAG authoring remains advanced. Guided mode uses deployment policy. Its disclosure appears only in Advanced mode alongside optional Skill inputs, required capabilities, worker routing, Priority, and Max Attempts.

Hidden controls cannot retain invisible authored policy. While hidden, submission uses the unauthored retrieval default. Reconstructing a source with authored `rag`/`followUpRetrieval` enables Advanced mode so that policy stays visible. Turning Advanced off explicitly clears that authoring rather than resurrecting it later. The server always re-clamps values to deployment ceilings.

This disclosure rule does not clear or hide the single repository/branch/publishing intent. Those remain ordinary visible context, not optional per-step overrides.

## Remediation Prefill

Workflow Detail can open `/workflows/new?intent=remediate&draftId=…` with a tab-scoped draft. Import remains ordinary editable authoring and never submits implicitly.

The Remediation Draft separates immutable target identity from repair intent. The pinned target includes the workflow, exact run, original outcome, failed Step Execution/checkpoint evidence, and selected source lineage. Editable repair instructions, supported workspace/destination roles, one applicable authored branch, publication, Runtime/Profile, and permitted policy options use the same Create controls. An isolated runtime-generated recovery work branch is derived execution state, not a duplicate generic branch selector. Genuinely distinct source/checkpoint and repair-destination roles are explicitly labeled and independently admitted.

Draft bodies stay in `sessionStorage`. A non-sensitive presence marker only explains another-tab links. Schema version 1, `createdAt`, and the two-hour TTL remain the draft transport contract. Import is single-use: clear storage after complete successful validation/copy or explicit discard. Missing, malformed, expired, and cross-tab drafts have distinct errors and do not partially prefill. Discard removes storage and the `intent`/`draftId` query parameters.

Submission uses ordinary `POST /api/executions` with canonical `task.remediation`. The server revalidates target visibility/current run, evidence, selected profiles and policy, repository/branch/publication, and durable bidirectional linkage. Stale target pins request a refreshed remediation draft. Failed-step recovery without reauthoring keeps immutable original intent; changing it requires the supported newly admitted path.

## Draft Persistence and Historical Reconstruction

Drafts preserve overview, single source/repository/branch context, authored publication choice, task-specific step inputs, preset definitions/expansion state, attachments, and provenance. Derived bound values and compiled modes are not serialized back as new authored choices.

A historical equivalent repository/branch copy can collapse only when the backend establishes equivalence. Conflicts, unknown provenance, old two-branch intent, and mixed per-step publication require an explicit reconstruction diagnostic. A historical batch parent None with PR children must reconstruct as a PR scope with a non-publishing coordinator when evidence supports it. Historical literal Auto retains Skill-owned meaning, not the new generic recommendation semantics.

Rerun, schedule editing, and remediation use the same compiler and visible explanation. Historical execution artifacts remain immutable. A new draft cannot quietly adopt changed catalog defaults, credentials, or merge authority.

## Provenance and Catalog Loading

The catalog normalizes Presets, Skills, Tools, and supported runtime capabilities into shared schema metadata. Large schemas can be fetched by immutable reference. Empty schemas remain valid where allowed.

Generated steps show a compact source annotation. Provenance includes selected preset/Skill identity, definition/input-contract digest, task input snapshot, and context/policy derivation evidence. It distinguishes what the user entered from what the compiler bound, and never leaks credentials or unnecessary runtime handles.

## Accessibility, Security, and Conformance

Every input has an accessible label, help text, and associated error. Keyboard navigation, focus transfer on validation, narrow layouts, advanced disclosure, and read-only target explanations are tested. Bound-value explanations must not masquerade as disabled editable fields requiring user input.

Schemas and UI hints are data. Widget identifiers are allowlisted, markdown is sanitized, secrets are excluded from defaults, secret inputs use typed references, and backend authorization is mandatory. Unsupported authority metadata cannot degrade into permissive behavior.

Required production-boundary coverage includes:

- New supported schemas render without Skill/Preset-name page branches.
- Guided/Advanced required alternatives remain usable without duplicate context fields.
- Apply/Reapply and unexpanded Submit preserve explicit policy and current bindings.
- Auto and omission resolve identically; the explanation accurately shows child output and merge behavior.
- Retired workspace fallback values do not become active hidden defaults; affected historical operator intent is preserved or explicitly reviewed.
- Main Jira/GitHub batches, both breakdown families, document orchestration, PR/Dependabot batches, direct resolvers, and the review loop use the same control.
- Existing PR targets derive head/base correctly, non-default bases survive fan-out, and stale async responses cannot change targets.
- None, incompatible resolvers, shared-branch batches, and missing prerequisite-code handoffs fail visibly before prohibited effects.
- Edit/rerun/schedule/remediation reconstruction preserves authored versus derived values and reports legacy ambiguity.
- Validation failure preserves task input, while changed context invalidates stale generated authority.
- No alternate Target/runtime wizard or second publishing selection is introduced.

A rendered selector or passing component fixture alone is not proof that the API, compiler, fan-out, or runtime honors the same policy.

## Related Documents

- `docs/Workflows/WorkflowPresetsSystem.md`
- `docs/Steps/StepTypes.md`
- `docs/Steps/SkillSystem.md`
- `docs/Steps/SkillInputSchemaUIGeneration.md`
- `docs/Steps/InputSchemaGuidance.md`
- `docs/Steps/JiraIntegration.md`
- `docs/Workflows/WorkflowArchitecture.md`
- `docs/Workflows/WorkflowPublishing.md`
- `docs/Workflows/PrMergeAutomation.md`
- `docs/Workflows/WorkflowEditingSystem.md`
- `docs/UI/WorkflowConsoleArchitecture.md`
- `docs/UI/DashboardDesignSystem.md`
- `docs/Security/SettingsSystem.md`
