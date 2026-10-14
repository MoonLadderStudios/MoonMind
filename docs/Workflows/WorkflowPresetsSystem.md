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

This document describes the schema-driven composition layer for reusable workflows. Presets collect task-specific inputs, expand into executable steps, and preserve provenance. Repository, branch, and publication are single workflow-level choices, not repeated preset inputs.

**Related Docs:** [Workflow Publishing](WorkflowPublishing.md), [Create Page](../UI/CreatePage.md), [Input Schema Guidance](../Steps/InputSchemaGuidance.md), [Step Types](../Steps/StepTypes.md), [Skill System](../Steps/SkillSystem.md), [Jira Integration](../Steps/JiraIntegration.md), [Workflow Architecture](WorkflowArchitecture.md), [Workflow Editing System](WorkflowEditingSystem.md), [Settings System](../Security/SettingsSystem.md).

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

Workflow-scoped expansion does not require a human account. Machine execution principals are execution authority and are not written into UUID catalog attribution. Recent-choice attribution records only an explicit catalog selection, so goal-selected submissions expand without recording usage under the execution owner.

Trusted issue loaders persist the complete GitHub or Jira brief as a linked JSON artifact before returning to the workflow. The artifact carries portable assessment fields (`issue_provider`, `issue_ref`, `issue_url`, `title`, `description`, `acceptance_criteria`, `labels`, `preset_brief`, `constraints`, `source_resolution`, `trusted_source`, `truncated`, `truncated_fields`) alongside provider and claim handoff fields. Native text is retained exactly, including whitespace, and Jira's structured description is kept in `jiraIssue.description` beside its text rendering. `source_resolution` records missing mandatory source fields; absent optional enrichment does not make the source incomplete. Completeness and truncation describe the native read before prompt compaction.

`briefArtifactRef` carries that source into attachment-capable assessment, implementation, remediation, and verification workspaces, which receive the exact JSON under `.moonmind/attachments/` with an opaque filename. Reading it fulfills the assessment source requirement. Direct managed Codex sessions keep their prepared-context path because their session adapter rejects raw input refs. Inline context may be shortened for prompt limits while the full attachment remains authoritative, and agent-generated copies do not replace it. Loader authority comes from the dispatched native tool identity, not from an agent's `trustedSource` field, so an agent cannot claim to be a trusted loader. Artifact storage failure stops the handoff before assessment. Freeform textarea/Markdown inputs and whole-value string bindings preserve source spacing and string values such as `false`; boolean settings keep their boolean type.

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

A new preset does not need a per-preset React branch such as `if (preset.id === "jira-orchestrate") return <JiraOrchestrateSpecialForm />`. The reusable widget registry may include text, textarea/markdown, number, checkbox, select, multi-select, structured JSON, Jira/GitHub issue pickers, repository/branch pickers, profile/model pickers, and file-reference pickers. The existence of a widget does not authorize adding a duplicate workflow-level selection to every preset.

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

Binding is not inferred from field names, because a source document repository, issue identity, comparison branch, or separately authorized publication destination may have a distinct role; the supported operation declares and visibly names that role. An ordinary same-repository batch is not a hidden multi-repository workflow.

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

`github-issue-search-and-implement` uses the workflow repository binding. Its typed `github.load_issue_preset_brief` operation accepts `issueSearch` plus `includeAllAuthors` (preset input `include_all_authors`, default `false`); the implementation lives in `moonmind/workflows/temporal/github_issue_search.py`.

**Author scope.** By default only issues created by the GitHub account used for the search are eligible. That account is the one behind the exact credential used for the issue-search API requests (`GET /user`, compared by durable ID because logins can change), not the MoonMind session user, repository owner, assignee, Git author, or an unrelated `gh` login; bot-created issues count under the actual creating account. The generated Create-page checkbox **Include issues created by other users** (unchecked, directly below the search field) broadens only author eligibility; repository binding, open state, lifecycle, blockers, attempt ownership, retry, verification, and publication policy still apply. A nonempty query adds `author:<resolved-login>` around the whole parenthesized query so `OR` cannot escape the scope; an empty query scans open issues by descending creation with `creator=<resolved-login>`. User-entered `author:` qualifiers are backend-owned: `author:@me` or the same account collapses to one constraint, while another author, an exclusion of the required author, or ambiguous author syntax is a field-addressable search error that requires the explicit opt-in. In all-author mode authored filters are preserved and only the automatic constraint is removed. Candidates are checked by user ID before any expensive check, announcement, or mutation, and the selected issue is revalidated against fresh detail before the trusted brief handoff. No self-authored match returns an explicit no-eligible-match result and never auto-broadens, and unavailable identity in self-only mode stops with a diagnostic instead of falling back to all authors. Explicit Issue Implement / Issue Orchestrate selection may still choose another author's issue, and dependency reads are not author-filtered. `searchEvidence` records `authorScope`, `authenticatedUser`, `selectedIssueAuthor`, and `authorMismatchesSkipped` with page/candidate counts. One resolved credential is reused per bounded operation, and no raw token appears in inputs, history, artifacts, errors, URLs, or logs. A missing input materializes `false`, saved values persist through edit, rerun, and scheduling, and admitted executions keep their pinned behavior.

**Candidate eligibility.** Search results only discover candidates; each is revalidated through the shared lifecycle eligibility decision. Available means no MoonMind status label. A Recovery-needed issue is a continuation candidate only with sufficient stopped-writer, prior-work, retry, and policy evidence. In-progress, code-review, needs-attention, closed, mixed/unknown states, unresolved active comments, and unsafe handoffs are excluded. Safe recoveries are preferred under a bounded fair policy so one difficult issue cannot starve new work. The scan covers at most five pages of 100 candidates, excludes pull requests, and records what it examined. Missing or exhausted evidence stops before implementation or issue mutation with distinct no-eligible-candidate, incomplete-evidence, cooldown/hold, permission, and GitHub-unavailable outcomes. The preset does not silently widen the query, treat a rate-limited scan as empty, or launch a fresh attempt to escape exhausted retry history.

Declared dependencies carry an explicit scope. **Blocked from starting** means no useful implementation can begin, so the candidate is not selected. **Blocked from completing** means implementation can proceed but final acceptance or closure waits. `Depends on`, `Blocked by`, `Integration prerequisites:`, `Child issues` / `Sub-issues` lists, and GitHub tracking-list children are start-blocking. `Completion depends on` is completion-blocking, because that is what the declaration says: an issue whose body states that its *completion* depends on other issues can still receive the independently useful work it also describes, while `github.check_issue_blockers` still reports `blocked` and names the open dependency. Open completion dependencies travel with the brief as `completionDependencies`.

Explicit `Depends on`, `Blocked by`, `Completion depends on`, and `Integration prerequisites:` sentences contribute dependency evidence. Short references, qualified references, issue URLs, and inclusive numeric ranges are resolved through the selected repository's authorized access, with at most 100 declared prerequisites per candidate. The scan shares a 100-request prerequisite-lookup budget and reuses validated repeated identities. Exhaustion requests an explicit issue or narrower search. Confirmation uses fresh prerequisite reads with its own 100-request budget and re-fetches current issue detail to reject a lifecycle state added between search and brief loading; the later implementation preflight also checks current state. A `status: todo` label is not required for availability and is not added or removed by lifecycle transitions. Issues with older lifecycle labels are assessed and repaired through the `github_issue.assess_legacy` / `github_issue.plan_legacy_repair` activities onto the same lifecycle policy; see [GitHub Issue Legacy Cutover](GitHubIssueLegacyCutover.md).

Child issue lists declare prerequisites through the same lookup, recognized two ways. A `Child issues` or `Sub-issues` heading declares every entry in its section. Elsewhere, GitHub's tracking-list rendering is the declaration: a checkbox entry whose leading content is an issue reference, such as `- [ ] #7 Split reliability tests`, is a tracked child under any heading, because epic bodies name that section differently every time and the parser does not enumerate heading wording. A heading that names its section as context (`related`, `superseded`, `see also`, `non-goals`, `out of scope`) keeps its references contextual, and a checkbox entry that does not lead with an issue reference is prose, so acceptance checklists declare nothing. Markdown section nesting applies, and fenced code, indented code, and HTML comments do not declare children. Spaced ranges require an explicit `#` endpoint (`#7 — #9`), so `#7 — 2FA support` cannot invent a range. GitHub issue state, not the checkbox, decides: an open child blocks the parent and unverifiable child state stops admission. An issue labelled `epic` also declares a child with each top-level paragraph sentence that leads with an issue reference, because epics assign work to owning issues in prose as well as lists; an open epic named that way is a peer program rather than a child, so epics that name each other cannot block each other. Ordinary issues use the same prose shape to divide scope with siblings, so it stays contextual for them. This keeps an unfinished parent epic from being selected automatically while its work belongs to open child issues; explicit selection is protected by the same implementation preflight.

Open start-blocking prerequisites block admission; closed prerequisites do not, and open completion dependencies block completion instead of admission. Only the leading reference list after a declaration contributes dependencies, not subsequent parent/related prose. Unknown or unavailable evidence blocks. These checks do not edit labels or issue content.

The selected issue and brief travel through trusted context and a durable attachment, and downstream blocker/status tools use the same identity and reject conflicts. The journey composes candidate discovery, shared advisory admission, trusted brief/prior-work resolution, assessment, implementation only when needed, verification/remediation/publication, and durable terminal handoff. The brief announces before expensive assessment and settles this attempt's own announcement before recandidating on a detected competitor. The resolved issue survives replay and retry: recovery never repeats search, resets a continuation to the base branch, or creates a second PR. Explicit resolved tool repository/issue inputs are execution arguments, not competing user-level overrides.

The immutable initial assessment identifies apparently present implementation and bounded gaps. It does not prove objective acceptance or landing. Assessment, verification, remediation, and finalization use the resolved `moonspec-verify` [acceptance policy](../../.agents/skills/moonspec-verify/references/acceptance-policy.md). Objective evidence binds actual candidate content, original scope and requirement identities, freshness, and the configured completion target. Only current target evidence allows the no-new-work completion path; candidate-only work requires the existing PR path. A later `FULLY_IMPLEMENTED` verifier result approves the candidate for publication; a clean restored branch or already-pushed commit does not make it a no-change outcome, and a partial PR is never a fully-implemented verdict. The search preset declares the same `code-review-handoff` role as explicit issue implementation. Failed, blocked, canceled, and exhausted outcomes are owned by the durable failed-attempt boundary (the `github_issue` finalize_failed_attempt activity plus the periodic handoff reconciler) rather than a success-ordered preset node, so they still produce a safe-release handoff. Before updating issue status, the workflow creates or adopts a missing PR from its accepted remote branch and carries the confirmed URL into the trusted status tool; a resolved existing PR continues from its validated current head and is updated rather than duplicated. Completed work routes to missing verification, review/merge follow-up, or status finalization under the admitted policy. The resolved issue identity supplies the PR closing reference and post-merge completion target.

Remediation Continue-As-New carries the compact trusted issue identity, initial assessment verdict, assessment/brief artifact references, and assessed repository/branch alongside the accepted candidate head. The resumed run restores that authority before publication or issue finalization; issue bodies and detailed requirements remain in artifacts.

## Preset Step and Provenance

An authored step contains task-specific inputs:

```json
{
  "type": "preset",
  "preset_slug": "jira-orchestrate",
  "title": "Jira Orchestrate",
  "inputs": {"jira_issue": {"key": "PROJ-123"}},
  "expansion_state": "not_expanded"
}
```

Expanded steps retain definition and input evidence:

```json
{
  "type": "skill",
  "skill_name": "jira-orchestrate",
  "title": "Implement PROJ-123",
  "inputs": {"jira_issue_key": "PROJ-123"},
  "provenance": {
    "source_type": "preset",
    "preset_slug": "jira-orchestrate",
    "preset_digest": "sha256:...",
    "input_snapshot": {"jira_issue": {"key": "PROJ-123"}}
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

The normalized metadata separates **role** from **default output policy**. For example:

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

The catalog may normalize seed serialization while preserving these semantics:

- An explicit workflow-level selection wins over recommendations and is validated, not coerced.
- Omission or authored `default` displays Auto and resolves the selected composition's declared behavior.
- A coordinator role derives its own compiled `none`; it does not force the authored workflow or descendants to None.
- Included presets contribute their step roles and requirements. Their standalone defaults do not override the enclosing scope.
- Children inherit the frozen scope intent. They do not re-evaluate the latest child preset default or inherit local coordinator `none`.
- Managed implementation explicitly declares its output behavior instead of depending on absence of metadata as an implicit PR default.
- A plan with early PR handoff, read-only verification, tracker updates, or merge automation retains one policy and one owner per effect.
- Capability metadata and schema expressions cannot broaden permissions, choose another repository, or duplicate Skill semantics.

The retired `workflow.default_publish_mode` setting and its environment aliases are not fallback sources for missing preset/default metadata or helper arguments. [Settings System section 10.6](../Security/SettingsSystem.md#106-publication-default-ownership-and-retired-setting) preserves configured historical intent through proven explicit reconstruction or visible review. New/defaulted expansion follows only the publication compiler, and missing authority-sensitive declarations fail rather than selecting the old workspace default.

A read-only assessment included within implementation remains read-only without disabling publication of the implementation's cumulative candidate. A workflow with both its own repository deliverable and fan-out declares both responsibilities rather than using coordinator status to suppress its output.

## Workflow-Level Title Enrichment Metadata

Presets that resolve their target issue at runtime (rather than at authoring time) may declare the opt-in `titleEnrichment` annotation. The workflow owns the single shared title transition used by both manual `SetTitle` renames and automatic enrichment; the annotation only connects an accepted typed resolver result to that transition. It never renames the preset or schedule, changes workflow/run identity, rewrites the plan, retargets repository work, or changes branches, checkpoints, artifacts, commits, or publication behavior.

```yaml
annotations:
  titleEnrichment:
    enabled: true
    provider: github
    sourceTool: github.load_issue_preset_brief
    targetStyle: base-colon-hash
```

Catalog expansion preserves the declaration in the admitted plan snapshot with the frozen `presetTitle`/`presetSlug`. The preset title is the base label; once an issue is accepted the workflow renders `<base>: #<number>`, and it leaves the base unchanged while selection is pending or no issue is selected. Explicit user titles (including same-text renames) are protected, eligibility is evaluated at the mutation point with a revision guard, and duplicate results are idempotent. `github-issue-search-and-implement` is the preset that opts in.

## Built-in Batch and Resolver Contracts

The detailed policy matrix is in [Workflow Publishing, section 6](WorkflowPublishing.md#6-built-in-behavior-matrix). Catalog expansion covers all of these families, not just the two presets named Batch:

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

Jira/GitHub Orchestrate implementation presets produce repository work even though their name includes Orchestrate. Classification follows metadata and plan responsibilities, not names.

A batch's options are constrained by the selected child contract. Parallel children cannot all publish to one selected branch without a qualified serialized handoff. A dependent batch cannot switch off merging or publication when its next child requires predecessor code unless another explicit candidate/checkpoint handoff is provided. PR-URL-required flows cannot pretend to support None by silently skipping required tracker semantics.

Static incompatibility is detected before creating issues or launching the parent. Dynamic targets are validated before each child dispatch, with truthful partial outcomes after external failure. Enqueue evidence does not prove child completion. The fan-out API independently checks parent authority, pinned policy, target derivation, and idempotency consistency.

## Dependent Defaults

A task-specific default can depend on another input through a validated semantic default rule. The UI can display the rule, but execution semantics cannot exist only in `uiSchema`.

Only an unauthored value is defaulted. Explicit values are preserved and validated. Rule source/target names, maps, types, allowed enum values, and cycles are validated with the definition. A map contains declared own entries only, never inherited object members. A legitimate task-input rule can have a declared static fallback; missing authority-sensitive publication mappings instead block rather than guessing.

Publication recommendations come from workflow-level publication metadata rather than a `uiSchema` dependent default, so the form has one Auto selection, not a hidden dependent preset field plus a second workflow selector.

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

Coverage exercises the real catalog, expansion, compiler, fan-out, and form boundaries: schemas render without per-preset frontend branches; Apply/Reapply and unexpanded Submit produce equivalent targets, policy, and provenance; context changes invalidate stale generated values without losing explicit choices; every built-in family produces its declared Auto behavior and required handoffs; coordinators preserve descendant intent through nesting; contradictory compositions fail before effects; and schedules, edit/rerun, MCP/API, reconstruction, and history replay keep the correct definition evidence. A documentation or seed update alone is not proof of conformance.
