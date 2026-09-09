# Workflow Presets System

**Document Class:** Canonical declarative  
**Viewpoint:** Module Architecture View  
**Status:** Draft  
**Owners:** MoonMind Engineering  
**Updated:** 2026-09-06  
**Audience:** Preset/catalog, workflow compiler, API, and dashboard contributors  
**Authority:** Preset catalog, task-input composition, expansion/default metadata, nested provenance, and their integration with the shared publication compiler. Workflow Publishing owns publication semantics and Input Schema Guidance owns binding keys.  
**Owning Surface:** Preset authoring/catalog and backend expansion boundary  
**Related Implementation:** `api_service/data/presets/`, `.agents/skills/_shared/batch_workflows.py`, and existing preset expansion services.

This document defines the schema-driven composition layer for reusable workflows. Presets collect task-specific inputs, expand into executable steps, and preserve provenance. Repository, branch, and publication are single workflow-level choices, not repeated preset inputs.

**Related Docs:** [Workflow Publishing](WorkflowPublishing.md), [Create Page](../UI/CreatePage.md), [Input Schema Guidance](../Steps/InputSchemaGuidance.md), [Step Types](../Steps/StepTypes.md), [Skill System](../Steps/SkillSystem.md), [Jira Integration](../Steps/JiraIntegration.md), [Workflow Architecture](WorkflowArchitecture.md), [Workflow Editing System](WorkflowEditingSystem.md), [Settings System](../Security/SettingsSystem.md).

The document specifies the long-term design. Current seed files and helpers are implementation artifacts, not exceptions that override this contract. Rollout status belongs in issues or temporary execution notes.

## Purpose and Goals

Presets let a user start with a known workflow shape without authoring every step. A preset may describe a simple action, coding workflow, Jira/GitHub orchestration, remediation, or bounded fan-out.

The Create page is not responsible for knowing each preset's behavior. It reads a normalized catalog contract and renders task-specific inputs with shared widgets. Backend expansion and validation are authoritative.

The system preserves these properties:

- Presets are first-class step types, not a separate Create-page mode.
- A configured preset can remain unexpanded until submission.
- Apply, Reapply, unexpanded Submit, API, MCP, schedules, edit/rerun, and goal-selected presets use the same backend expansion/compiler path.
- Input schemas align with Skill schemas and remain declarative and portable.
- Nested presets preserve validation, ancestry, and content evidence.
- One authored repository/branch/publication context remains authoritative throughout expansion and child dispatch.
- Preset metadata supplies defaults and requirements, never permission to override explicit user intent.

Presets do not replace Skills, require per-preset React forms, grant execution rights, or create a second publishing engine. Their expanded steps pass the normal policy, runtime, repository, and publication boundaries.

Trusted issue loaders persist the complete GitHub or Jira brief as a linked JSON
artifact before returning to the workflow. The existing `briefArtifactRef` carries
that source into attachment-capable assessment, implementation, remediation,
and verification workspaces. Direct managed Codex sessions retain their existing
prepared-context path because their session adapter rejects raw input refs.
Inline context may be shortened for prompt limits;
the full attachment remains authoritative. Agent-generated copies do not replace
the loader's brief. Loader authority comes from the dispatched native tool identity,
never from an agent's `trustedSource` field. Artifact storage failure stops the
handoff before assessment.

GitHub implementation presets declare `docker` for their repository verification
work. Admission validates Docker readiness; agents use the
scoped Docker Backend Service to execute tests.

## Core Concepts

| Concept | Meaning |
| --- | --- |
| Preset | Catalog entry with identity, metadata, task inputs, context bindings, publication-role/default metadata, and an expansion plan |
| Preset step | Authored `type: preset` selection with `preset_slug`, scope, and task-specific inputs |
| Input schema | JSON Schema-compatible contract for value shape and required inputs |
| Context binding | Authoritative projection from workflow context, not an editable copied default |
| UI schema | Optional safe presentation hints, not semantic or authorization authority |
| Expansion | Backend transformation of a selected definition and validated context into concrete steps |
| Provenance | Preset slug/scope, definition digest, ancestry, authored inputs, bound-source evidence, and compiler derivation |

## Preset Catalog Contract

A representative entry is:

```yaml
id: jira-orchestrate
kind: preset
label: Jira Orchestrate
description: Build and execute an implementation workflow from a Jira issue.
category: issue-tracker
input_schema:
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
ui_schema:
  jira_issue:
    widget: jira.issue-picker
    placeholder: Select a Jira issue
expansion:
  steps:
    - type: skill
      skill_name: jira-orchestrate
      title: "Implement {{ inputs.jira_issue.key }}"
      inputs:
        jira_issue_key: "{{ inputs.jira_issue.key }}"
        jira_issue_summary: "{{ inputs.jira_issue.summary }}"
        jira_issue_description: "{{ inputs.jira_issue.description }}"
        jira_issue_url: "{{ inputs.jira_issue.url }}"
```

Examples illustrate the contract rather than asserting a particular seed is implemented that way. Stable identity, description, category/tags, definition evidence, schema, optional UI hints, deterministic expansion, and provenance are required concepts. Internal preset identity is slug plus scope, not a semantic version selector. Content digests identify the resolved definition.

API responses may use camelCase while storage uses snake_case. Mapping is lossless. Presets, Skills, and Tools share one normalized input model such as:

```json
{
  "id": "jira-orchestrate",
  "kind": "preset",
  "label": "Jira Orchestrate",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {},
  "contractDigest": "sha256:...",
  "capabilities": {"apply": true, "submitTimeExpansion": true}
}
```

Publication-role/default and context-binding metadata are included in the normalized definition and its digest. They do not become independently editable browser configuration.

## Input Schema Strategy

Schemas describe task-specific values: issue references, document paths, verification choices, discovery filters, review settings, constraints, and meaningful comparison or destination roles. Equivalent repository, branch, runtime/profile, and publication passthrough inputs use workflow context rather than another ordinary control.

The shared practical subset includes `type`, `title`, `description`, `default`, `required`, `properties`, `items`, `enum`, `oneOf`/`anyOf`, standard formats, and safe namespaced semantic extensions. Optional schemas remain compatible with the Skill adoption policy in [Input Schema Guidance](../Steps/InputSchemaGuidance.md).

A widget can be selected through safe semantic hints or a registered UI identifier:

```yaml
ui_schema:
  jira_issue:
    widget: jira.issue-picker
```

A new preset must not require:

```tsx
if (preset.id === "jira-orchestrate") {
  return <JiraOrchestrateSpecialForm />
}
```

The reusable widget registry may include text, textarea/markdown, number, checkbox, select, multi-select, structured JSON, Jira/GitHub issue pickers, repository/branch pickers, profile/model pickers, and file-reference pickers. The existence of a widget does not authorize adding a duplicate workflow-level selection to every preset.

## Context-Bound Inputs

The workflow owns its repository/source target, branch role, and publication selection. A portable preset or helper can retain arguments such as `repository` or `publish_mode`, but MoonMind supplies them at the adapter boundary from validated context.

A normalized field can declare:

```yaml
repository:
  type: string
  x-moonmind-context-binding: repository.name
```

The semantic binding keys, validation, and portability rules are owned by [Input Schema Guidance](../Steps/InputSchemaGuidance.md). `publication.policy` projects the frozen scope intent to the target's typed contract, never the coordinator's local publication mode and never an unresolved Auto string.

Bound values are resolved before required-field validation. They are absent from editable preset forms and authored input snapshots. Compiler-produced execution inputs retain source provenance. Conflicting caller-supplied copies are rejected; proven equivalent historical copies can be collapsed by the versioned reconstruction boundary.

Do not infer binding by field name. A source document repository, issue identity, comparison branch, or separately authorized publication destination may have a distinct role. The supported operation must declare and visibly name that role. An ordinary same-repository batch is not a hidden multi-repository workflow.

Changing workflow context invalidates dependent lookups, previews, and generated bindings. Apply/Reapply and Submit cannot retain an old repository/base embedded in generated instructions after the user changed the visible control.

## UI Generation Rules

When a preset is selected, the frontend loads its normalized metadata, task inputs, defaults, existing draft values, and context bindings. It renders the remaining authored fields through the shared schema renderer and exposes bound-context explanations without duplicate editors. Local feedback is followed by authoritative backend validation.

Supported draft states include no preset, missing required input, configured but unexpanded, applied with editable generated task content, submitted without manual expansion, and expansion failure. Values and provenance survive validation failures.

Generated-step editing does not reopen repository/branch/publish override controls. Material changes to a generated step are revalidated against the single workflow policy. An incompatible combination requires a supported composition or separate workflows, not a hidden override.

## Jira Issue Input Pattern

Jira-driven presets request an issue object rather than relying only on prose:

```yaml
input_schema:
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
        status: {type: string}
        assignee: {type: string}
ui_schema:
  jira_issue:
    widget: jira.issue-picker
    allow_manual_key_entry: true
```

The durable minimum is the issue key. Optional enrichment is fetched/validated through trusted integration operations when needed. A picker label or untrusted description does not provide issue-transition authority.

## GitHub Issue Search

`github-issue-search-and-implement` uses the workflow repository binding. Its typed `github.load_issue_preset_brief` operation accepts `issueSearch`: a nonempty query selects GitHub's best available open issue match, skipping issues already marked status in-progress; an empty query scans open issues in descending creation order for the first candidate without blocker evidence and without an in-progress status. The scan is bounded to five pages of 100 candidates, excludes pull requests, and records pages/candidates examined. Missing, malformed, incomplete, or exhausted evidence stops before implementation or issue mutation.

Explicit `Depends on`, `Completion depends on`, and `Integration prerequisites:` sentences contribute prerequisite evidence. Short references, qualified references, issue URLs, and inclusive numeric ranges are resolved through the selected repository's authorized access, with at most 100 declared prerequisites per candidate. The scan shares a 100-request prerequisite-lookup budget and reuses validated repeated identities. Exhaustion requests an explicit issue or narrower search. Confirmation uses fresh prerequisite reads with its own 100-request budget and re-fetches current issue detail to reject an in-progress status added between search and brief loading; the later implementation preflight also checks current state.

Issue lists under `Child issues` or `Sub-issues` headings declare completion prerequisites through that same lookup. Markdown section nesting and legal heading indentation apply: categorized children remain in scope until a heading of the same or higher level ends the section. Fenced code, indented code, and HTML-comment examples do not declare children. Bulleted, numbered, and checkbox entries may start with short or qualified issue references, issue URLs, or Markdown links to issues. A spaced dash starts the child title; spaced ranges require an explicit `#` endpoint, such as `#10 — #12`, so `#10 — 2FA support` cannot invent a range. GitHub issue state is authoritative regardless of a checked or unchecked box: an open child blocks the parent, closed children allow its remaining requirements to be assessed, and unverifiable child state stops admission. Parent links, sibling related-work sections, and contextual references after a child entry do not become dependencies. This prevents automatic selection of an unfinished parent epic while its implementation belongs to open child issues; explicit selection is protected by the same implementation preflight.

Open prerequisites block admission; closed prerequisites do not. Only the leading reference list after a declaration contributes dependencies, not subsequent parent/related prose. Unknown or unavailable evidence blocks. These checks do not edit labels or issue content.

The selected issue and brief travel through trusted context and a durable attachment. Downstream blocker/status tools use the same identity and reject conflicts. Instructions do not perform dynamic binding or read agent-local files. Explicit resolved tool repository/issue inputs remain execution arguments, not competing user-level overrides. A pinned historical plan keeps its old search/selection behavior until newly admitted authoring selects the current definition.

The initial assessment controls whether an issue needs implementation and a PR. A later `FULLY_IMPLEMENTED` verifier result approves the candidate for publication; a clean restored work branch or an already-pushed commit does not make that candidate a no-change outcome. The search preset declares the same `code-review-handoff` role as explicit issue implementation. Before updating issue status, the workflow creates or adopts a missing PR from its accepted remote branch and carries the confirmed URL into the trusted status tool. The resolved issue identity supplies the PR closing reference and post-merge completion target.

Remediation Continue-As-New carries the compact trusted issue identity, initial assessment verdict, assessment/brief artifact references, and assessed repository/branch alongside the accepted candidate head. The resumed run restores that authority before publication or issue finalization; issue bodies and detailed requirements remain in artifacts.

## Preset Step and Provenance

An authored step contains task-specific inputs:

```json
{
  "type": "preset",
  "preset_slug": "jira-orchestrate",
  "title": "Jira Orchestrate",
  "inputs": {"jira_issue": {"key": "MOON-123"}},
  "expansion_state": "not_expanded"
}
```

Expanded steps retain definition and input evidence:

```json
{
  "type": "skill",
  "skill_name": "jira-orchestrate",
  "title": "Implement MOON-123",
  "inputs": {"jira_issue_key": "MOON-123"},
  "provenance": {
    "source_type": "preset",
    "preset_slug": "jira-orchestrate",
    "preset_digest": "sha256:...",
    "input_snapshot": {"jira_issue": {"key": "MOON-123"}}
  }
}
```

Context and policy derivation are retained in existing snapshot/plan evidence separately from user-entered task inputs. A generated `none` step or coordinator mode never overwrites the user's authored publication selection in that snapshot.

## Expansion Semantics

One shared backend path owns expansion:

```text
expandPreset(preset_slug, scope, inputs, context) -> expanded_steps
```

It serves Apply, Reapply, unexpanded Submit, API/MCP, edit/rerun, scheduled authoring, and goal-selected presets.

The compiler loads and pins definition evidence, resolves context bindings, applies semantic defaults to unbound inputs, validates the complete input contract, recursively expands nested presets, resolves the single publication scope, validates the concrete plan and required handoffs, and returns field-addressable diagnostics. Validation happens before effects. External target acquisition records its source and immutable resolved result through trusted boundaries rather than hiding non-deterministic lookup inside a template expression.

Identical definitions, inputs, context, and recorded external evidence produce identical expansion. Large evidence remains artifact-backed.

## Input Binding Expressions

Task-value mappings use a safe deterministic expression language:

```yaml
steps:
  - type: skill
    skill_name: moonspec-breakdown
    inputs:
      jira_issue_key: "{{ inputs.jira_issue.key }}"
      jira_issue_summary: "{{ inputs.jira_issue.summary }}"
```

Bindings may read admitted `inputs`, safe project/repository/branch context, safe user metadata, and declared defaults. They do not execute arbitrary code or grant access to secrets. Template bindings to portable arguments do not make those arguments independently authored fields.

## Workflow-Level Publication Metadata

[Workflow Publishing](WorkflowPublishing.md) is canonical for policy semantics and the complete built-in matrix. Presets use the existing `workflowPublish` annotation as definition metadata for role, default behavior, supported choices, and derived stage/child responsibilities. They do not own another root selection.

The target normalized metadata separates **role** from **default output policy**. For example:

```yaml
annotations:
  workflowPublish:
    role: coordinator
    defaultModeFrom:
      field: run_ref
      map:
        'skill:jira-verify': none
        'preset:jira-implement': pr
        'preset:jira-orchestrate': pr
    children: inherit
```

A fixed default uses `defaultMode` instead of `defaultModeFrom`. These are mutually exclusive sources of the recommendation. An internal declared default `auto` means the selected consumer requires the Skill-owned publication protocol; it is not the user-facing generic Auto string. Catalog validation requires the relevant Skill/provider-evidence and finish contracts. All new managed and agent-owned publication evidence follows the unified repository schema, not a preset-specific format.

The exact seed serialization can be normalized by the catalog, but it must preserve these semantics:

- An explicit workflow-level selection wins over recommendations and is validated, not coerced.
- Omission or authored `default` displays Auto and resolves the selected composition's declared behavior.
- A coordinator role derives its own compiled `none`; it does not force the authored workflow or descendants to None.
- Included presets contribute their step roles and requirements. Their standalone defaults do not override the enclosing scope.
- Children inherit the frozen scope intent. They do not re-evaluate the latest child preset default or inherit local coordinator `none`.
- Managed implementation explicitly declares its output behavior instead of depending on absence of metadata as an implicit PR default.
- A plan with early PR handoff, read-only verification, tracker updates, or merge automation retains one policy and one owner per effect.
- Capability metadata and schema expressions cannot broaden permissions, choose another repository, or duplicate Skill semantics.

The retired `workflow.default_publish_mode` and environment aliases are not fallback sources for missing preset/default metadata or helper arguments. [Settings System section 10.6](../Security/SettingsSystem.md#106-publication-default-ownership-and-retired-setting) preserves configured historical intent through proven explicit reconstruction or visible review. New/defaulted expansion follows only the publication compiler, and missing authority-sensitive declarations fail rather than selecting the old workspace default.

A read-only assessment included within implementation remains read-only without disabling publication of the implementation's cumulative candidate. A workflow with both its own repository deliverable and fan-out declares both responsibilities rather than using coordinator status to suppress its output.

## Built-in Batch and Resolver Contracts

The detailed policy matrix is in [Workflow Publishing, section 6](WorkflowPublishing.md#6-built-in-behavior-matrix). Catalog expansion must cover all of the following, not just the two presets named Batch:

| Family | Definition obligations |
| --- | --- |
| Batch Jira | Auto follows Run: Verify uses None, Implement/Orchestrate use PR. Query/status/verification options remain inputs; repository/publish overrides do not. |
| Batch GitHub | Both supported issue runs default to PR. The selected repository/base survives issue-range discovery and child creation. |
| GitHub breakdown Implement/Orchestrate | Non-publishing issue/child creation with PR children; consistent PR-and-merge support requires the real child payload and lifecycle, not only an enum edit. |
| Jira breakdown Implement/Orchestrate | Preserve PR-and-merge child defaults and required predecessor-code handoffs. |
| Document update orchestration | Non-publishing discovery with PR-and-merge children; discovery and child source context agree. |
| PR and Dependabot batches | Existing-PR children use Skill-owned Auto on each discovered head/base. Preserve eligibility, caps, dry run, and deduplication. |
| Direct resolver and fix Skills | Preserve portable agent-owned publication and exact terminal evidence; `fix_only` still pushes and is not None. |
| Fix and Review Loop | Adopt the existing PR; the coordinator and resolver have derived roles under one scope. Optional final merge remains off by default. |

Jira/GitHub Orchestrate implementation presets produce repository work even though their name includes Orchestrate. Classification must follow metadata and plan responsibilities, not names.

A batch's options are constrained by the selected child contract. Parallel children cannot all publish to one selected branch without a qualified serialized handoff. A dependent batch cannot switch off merging or publication when its next child requires predecessor code unless another explicit candidate/checkpoint handoff is provided. PR-URL-required flows cannot pretend to support None by silently skipping required tracker semantics.

Static incompatibility is detected before creating issues or launching the parent. Dynamic targets are validated before each child dispatch, with truthful partial outcomes after external failure. Enqueue evidence does not prove child completion. The fan-out API independently checks parent authority, pinned policy, target derivation, and idempotency consistency.

## Dependent Defaults

A task-specific default can depend on another input through a validated semantic default rule. The UI can display the rule, but execution semantics cannot exist only in `uiSchema`.

Only an unauthored value is defaulted. Explicit values are preserved and validated. Rule source/target names, maps, types, allowed enum values, and cycles are validated with the definition. A map contains declared own entries only, never inherited object members. A legitimate task-input rule can have a declared static fallback; missing authority-sensitive publication mappings instead block rather than guessing.

Publication's old `uiSchema.publish_mode.defaultFrom` is replaced by workflow-level publication recommendation metadata. The form has one Auto selection, not a hidden dependent preset field plus a second workflow selector.

## Nested Presets

A parent explicitly maps task inputs into child presets:

```yaml
steps:
  - type: preset
    preset_slug: jira-implement
    inputs:
      jira_issue: "{{ inputs.jira_issue }}"
```

Repository/branch/publishing flow through the enclosing context, not nested input overrides. Each child schema is validated, missing required inputs include ancestry, cycles fail safely, and provenance records the full preset ancestry. Nested preset expansion and dynamically created workflow children remain different operations; both preserve the same publication authority rules.

## Jira Breakdown Reconciliation

Jira Breakdown Orchestrate includes repository reconciliation between MoonSpec breakdown and issue creation. Fully implemented stories are skipped, partially implemented stories retain traceability and `remainingWork`, and unverifiable stories are marked for manual review rather than automatically converted to implementation work.

The deterministic story-output tool consumes only eligible reconciled stories. Downstream workflow creation consumes only issue mappings actually created or reused. Policy changes cannot turn a skipped or unresolved story into a fabricated child target.

## Submit-Time Auto-Expansion and Goal Selection

Start Workflow validates non-preset fields, resolves and validates preset inputs/context, expands all unexpanded presets, checks the final policy/plan, and submits through normal admission. Errors identify the relevant workflow control or task input and preserve the draft.

A goal-only request without authored steps, plan, tool/Skill, or explicit preset may be mapped conservatively to a seeded preset. Jira-key goals select the relevant Jira implementation/orchestration shape, breakdown goals select breakdown orchestration, and general implementation goals select MoonSpec Orchestrate under the existing goal selector. This does not authorize a model to choose a more permissive publication mode.

`presetSchedule` records goal source, selected slug/scope, content digest, and reason. The ordinary compiler then resolves inputs, context, and publishing. Explicit execution shape takes precedence over goal inference.

## Apply and Reapply

Apply replaces or augments the selected draft segment according to the declared UI mode. Generated task content remains editable with provenance; editing does not mutate the template or create independent authority controls.

Reapply uses the saved selected definition and task inputs unless the user selects a new definition/input. It resolves bindings against current authored workflow context, invalidates stale previews, and preserves explicit publication selection. A policy/default or target change requiring new authority is visible and revalidated before submission. Historical immutable execution snapshots are never rewritten during draft reconstruction.

## Validation and Errors

Errors are field-addressable and include ancestry when applicable:

```json
{
  "errors": [
    {
      "path": "steps[0].inputs.jira_issue.key",
      "preset_path": ["parent-orchestrate", "jira-orchestrate"],
      "message": "A Jira issue is required.",
      "code": "required"
    }
  ]
}
```

A bound-field error points to the editable workflow repository, branch, publication, or target control and identifies the consuming step. An incompatible publishing objective is a policy error, not a request to fill a hidden `publish_mode` field. Duplicate or conflicting caller-owned bindings are rejected even through API/raw JSON.

## API, Security, and Persistence

Catalog operations list/read definitions, validate inputs, preview expansion, and expand for submission. The API exposes enough metadata for the shared form and explainable publication preview without exposing credentials or executing arbitrary schemas.

Widget names are allowlisted, markdown is sanitized, expressions use the safe interpreter, and secrets never appear in defaults or provenance. Binding/default declarations are untrusted data until normalized and admitted. Unsupported authority-bearing metadata blocks execution rather than degrading to a permissive editor.

Persist authored task inputs, selected definition evidence, context/policy intent, expanded-step provenance, and derived target/effect contracts in existing workflow artifacts. Definition evidence is immutable for admitted runs. A preset update changes its content digest, not the old run. A new draft may review and adopt the change; a retry or child cannot silently do so.

Versioned reconstruction distinguishes equal historical passthrough values, conflicting copies, coordinator-local None, old Skill-owned Auto, and mixed per-step policies. Equal proven copies can collapse; ambiguous intent requires review. Supported historical replay retains original bytes and semantics. New authoring has no permanent override aliases.

## Conformance

Required coverage exercises the real catalog, expansion, compiler, fan-out, and form boundaries:

- New supported schemas render without per-preset frontend branches.
- Required fields, alternatives, safe widgets, task defaults, and nested errors behave consistently for Skills and Presets.
- Apply/Reapply and unexpanded Submit produce equivalent targets, policy, and provenance.
- Changing repository/base/Run/publishing invalidates stale generated values without losing explicit user choices.
- No editable repository/branch/publish passthrough duplicates exist in guided or Advanced preset settings.
- Every built-in family in the matrix produces its declared Auto behavior, supported explicit choices, and required code handoffs.
- Coordinators preserve descendant PR/Auto intent through arbitrary supported nesting without acquiring unnecessary publication authority themselves.
- A child or included assessment cannot replace the scope policy. A contradictory composition fails before effects.
- Partial dispatch, duplicate requests, policy-conflicting idempotency reuse, dry run, and zero-target runs retain truthful outcomes.
- Schedules, edit/rerun, MCP/API, legacy reconstruction, and immutable-history replay preserve the correct contract and definition evidence.
- Retired workspace/default aliases cannot affect new expansion, and historical configured choices are preserved or reviewed rather than silently replaced.

Implementation backlogs and rollout checklists belong outside this canonical design. A documentation or seed update alone is not proof of conformance.
