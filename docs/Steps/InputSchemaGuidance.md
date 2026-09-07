# Input Schema Guidance

**Document Class:** Canonical declarative  
**Viewpoint:** Module Contract Specification  
**Status:** Draft  
**Owners:** MoonMind Engineering (Workflow Platform + UI)  
**Updated:** 2026-09-06  
**Audience:** Skill, Preset, Tool, API, and schema-form contributors  
**Authority:** Optional input-schema semantics, authoritative context-binding keys, input origin, and validation/portability rules shared by selectable step options.  
**Owning Surface:** Shared capability input normalization and context-binding boundary  
**Related Implementation:** `moonmind/services/skill_step_inputs.py`, `.agents/skills/`, and `api_service/data/presets/`.

**Related Docs:** [Step Types](StepTypes.md), [Skill System](SkillSystem.md), [Skill Input Schema UI Generation](SkillInputSchemaUIGeneration.md), [Workflow Presets System](../Workflows/WorkflowPresetsSystem.md), [Create Page](../UI/CreatePage.md), [Workflow Publishing](../Workflows/WorkflowPublishing.md).

## Purpose

This document defines optional input schemas and authoritative context bindings for Skills, Presets, Tools, and future selectable step options.

Structured input collection improves when a selected option supplies a schema, but schema authoring must not become a barrier to third-party Skill adoption. A plain Agent Skills-style `SKILL.md` with instructions and no structured input schema remains valid. The runtime agent may infer, request, or extract task-specific information from natural-language instructions and available context. It may not infer new repository, publication, credential, or merge authority from that prose.

This guidance concerns structured inputs, not the `requiredCapabilities` execution-requirement contract.

## Core Policy

1. `inputSchema` / `input_schema` is optional for Skills and third-party integrations.
2. When present, it defines structured values, types, required fields, and validation. A field's existence does not mean the user must edit it separately in MoonMind.
3. Schema-less Skills retain instruction-driven execution, not rejection merely for lacking a schema.
4. Authors describe data and semantics, not MoonMind UI components.
5. MoonMind derives forms through one shared schema-to-form layer and widget registry.
6. `uiSchema` / `ui_schema` is optional presentation metadata, never execution or authorization authority.
7. Repository, branch, and publication passthrough values bind to the single workflow context. They are not independently editable Skill/Preset overrides, including in Advanced mode or raw JSON.
8. Portable interfaces remain portable. MoonMind-specific binding belongs in the normalized catalog/adapter boundary and does not require modifying a third-party Skill to consume it.

## Step Option Catalog Shape

A selected option exposes a normalized contract such as:

```json
{
  "id": "jira-implement",
  "kind": "preset",
  "label": "Jira Implement",
  "description": "Move a Jira issue through implementation.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {}
}
```

The fields are optional unless the option has a stronger typed contract. Deterministic Tools normally have stronger requirements than an instruction-only Skill.

An omitted or empty schema is not a broken Skill. The UI provides instructions, attachments, and the existing workflow context rather than inventing an input form or requiring a wrapper schema.

## Authoritative Context Bindings

### Meaning

A context binding says **this value comes from the admitted workflow context**, not **copy this value into an editable field as a default**.

For example, a portable Skill may accept `repo`. MoonMind supplies that argument from the workflow's selected repository and displays a read-only explanation such as “Uses workflow repository.” The user's draft stores the repository once. Resolved execution inputs may include the projected `repo` value for the portable interface, together with provenance, but that projection is never a second authored source.

The normalized input contract uses the semantic extension `x-moonmind-context-binding`. A binding is an allowlisted semantic key, not executable code, an arbitrary object path, or an ambient environment lookup:

| Binding key | Value | Scope |
| --- | --- | --- |
| `repository.name` | Repository name in the format required by the supported capability | The execution's admitted repository role |
| `repository.branch` | The effective branch for that role | Authored base/update branch or validated target-derived PR head as appropriate |
| `publication.policy` | The frozen scope policy projected into the target consumer's declared representation | Publication intent, never a coordinator's local `none` |

A binding is supported only when the source role, projected type, and destination consumer are compatible. The full provider-discriminated repository, connection, access policy, and target identity remain in the plan. Projecting an `owner/repository` string for a GitHub-only Skill does not replace that authority or permit GitHub operations on a Lore-authoritative repository.

`publication.policy` does not mean passing an unresolved `default` string to every helper. The compiler/adapter projects the already resolved scope intent into the specific child submission or publication contract. If one scalar cannot represent the policy and required merge/finish configuration, the adapter passes the complete existing typed contract or rejects the unsupported handoff. It must not drop configuration or substitute a helper-local default.

These keys extend the existing input normalizer and context argument. They do not create a new expression engine, user-selectable inheritance system, or parallel metadata store.

### Binding and Defaulting Are Different

`x-moonmind-context-default` retains its meaning for genuine defaultable task inputs and historical decoding. It cannot implement an authoritative binding for the workflow's repository, branch, or publication policy. An old copied value may be stale even if it was originally derived from context.

For a bound field:

- Resolve its authoritative source before required-field and type validation.
- Do not render an editable control or store an authored duplicate.
- Recompute the execution projection when its context changes.
- Reject conflicting caller-supplied values at API/expansion boundaries. During supported historical reconstruction, equivalent copies can collapse only after their equivalence is established.
- Bring a missing or incompatible binding error to the visible workflow control or target input. Never hide the error because the generated field is not editable.
- Treat unknown or malformed authority-bearing bindings as an execution blocker. The ordinary lenient policy for harmless presentation hints does not apply.

A new authoring request cannot supply a bound value merely because it happens to equal the selected context. A versioned compatibility reader may accept and remove a proven redundant historical copy. Resolved internal inputs remain allowed because their provenance is compiler-owned, not caller-asserted.

### Semantic Identity, Not Field Names

Never automatically collapse every field named `repo`, `repository`, `branch`, or `publish_mode`. A comparison branch, an issue's identity, an independently authorized destination, and the workflow workspace may be genuinely different.

When the operation supports distinct roles, their metadata and UI name the distinction. Ordinary same-repository workflows coalesce equivalent roles into one authoring surface. Cross-repository work and publication to a different destination require the explicit typed roles defined by the repository contract, not an arbitrary hidden per-step override.

For existing-PR work, the PR reference is an actual task input. Its resolved repository, head, and base are target evidence. A branch-only PR lookup is an alternative locator, not another independently editable workspace branch. Conflicting explicit context is rejected before mutation. A batch resolves each child's PR head separately while keeping one publication policy.

### Snapshot and Staleness

The input-contract digest includes binding declarations and their semantic interpretation version. Execution evidence also records the source context/target and the projected values. It never includes raw credentials.

Changing repository, branch, target PR, Run selection, or publishing invalidates affected async lookups, expansion previews, and bindings. Reapply uses the current authored context and pinned/selected definition according to the draft's contract, not a stale copy embedded in generated instructions. Rerun and schedule reconstruction preserve the distinction between authored, bound, and defaulted values.

## Recommended Skill Frontmatter

A Skill that wants structured input fields may declare:

```yaml
---
name: pr-resolver
description: Resolve a pull request by diagnosing state and delegating to specialized skills.
metadata:
  required-skills: "fix-comments fix-ci fix-merge-conflicts"
  required-capabilities:
    - git
    - gh
inputSchema:
  type: object
  required:
    - pr
  properties:
    repo:
      type: string
      title: Repository
      description: Repository supplied from the admitted workflow target in MoonMind.
      x-moonmind-context-binding: repository.name
    pr:
      type: string
      title: Pull request
      description: PR number, PR URL, or an alternative head-branch locator.
    mergeMethod:
      type: string
      title: Merge method
      enum: [merge, squash, rebase]
      default: squash
---
```

This is a target metadata example, not a requirement to edit an imported Skill. A trusted catalog adapter can provide the binding to an unchanged portable `repo` argument. Outside MoonMind the same Skill/CLI may accept explicit repository and branch arguments through its normal interface.

The same Skill remains valid with standard Agent Skills frontmatter and prose inputs only. Optional schemas improve usability, not execution authority.

## Preset Guidance

Presets requiring deterministic expansion should declare enough schema to validate and persist task-specific inputs and binding metadata. A no-input preset may use:

```yaml
inputSchema:
  type: object
  properties: {}
```

A preset declares publication roles, supported policies, and defaults through its workflow metadata as specified in [Workflow Presets System](../Workflows/WorkflowPresetsSystem.md). It does not expose a `publish_mode` input in addition to the workflow publishing control. An internal portable helper argument can remain, populated by the compiler/adapter.

Defaults that influence authority or publication must be semantic catalog/compiler metadata, not presentation-only `uiSchema` expressions. Changing a task input can recompute the workflow's Auto recommendation, but cannot overwrite an explicit policy or alter an admitted descendant's frozen intent. The retired workspace publish setting is not a source for a binding or another fallback; its historical operator-intent treatment is defined in [Settings System section 10.6](../Security/SettingsSystem.md#106-publication-default-ownership-and-retired-setting).

## UI Derivation Policy

The normal path is:

1. Load the selected option and optional schema.
2. Identify typed task inputs and validated context bindings.
3. Resolve bound values and expose their read-only source explanations.
4. Derive controls for the remaining authored fields from schema, semantic hints, deployment policy, and integration availability.
5. Validate locally for immediate feedback.
6. Re-resolve and validate through the backend before apply or execution.

| Schema signal | Default interpretation for an authored field |
| --- | --- |
| `type: string` | Text input |
| Long string or markdown semantic format | Textarea or markdown editor |
| `type: boolean` | Checkbox |
| `enum` | Select |
| Array with enum items | Multi-select |
| URI, date, or date-time format | Corresponding typed input |
| Jira issue semantics | Jira picker with safe manual reference entry when supported |
| GitHub repository/PR semantics | Appropriate target picker when supported |
| Unknown object | Structured object editor fallback |
| Validated context binding | No duplicate editor; source explanation and workflow-level error routing |

A Skill or Preset does not need to name a React component or concrete widget for the UI to be usable. Guided/Advanced disclosure never turns a context binding into an authored override.

## Semantic Hints

Authors may include safe semantic metadata when plain JSON Schema is insufficient:

```yaml
inputSchema:
  type: object
  properties:
    target_issue:
      type: object
      title: Target issue
      x-moonmind-semantic-type: issue-reference
      x-moonmind-provider: jira
      required: [key]
      properties:
        key:
          type: string
```

Semantic hints describe meaning; MoonMind chooses presentation.

Custom fields are namespaced with `x-moonmind-*`. They contain no executable code, credentials, secret defaults, or credential-bearing URLs. Unknown harmless presentation hints may be ignored with a diagnostic. A required semantic binding, target discriminator, or authority contract must instead be understood and validated before execution. Missing support cannot degrade into an editable field that broadens authority.

## Optional UI Schema

`uiSchema` supports narrow presentation choices such as safe placeholders, grouping, ordering, or advanced disclosure. It is not the preferred source of Skill semantics.

It must not couple a capability to arbitrary React components, define validation absent from `inputSchema`, execute code, include secrets, override a bound value, or redefine publication behavior.

Effective presentation may layer platform defaults, deployment/admin presentation policy, semantic hints, and allowed UI hints. Backend validation always consumes the schema and execution policy, never UI-only state.

## Fallback Behavior Without a Schema

A schema-less Skill shows its description and prose guidance, accepts instructions and context attachments, and receives the single admitted workflow context. The agent may request missing task information and report blockers. This does not add duplicate repository/branch/publish controls or make natural-language instructions a permission grant.

The Skill remains selectable. An unsupported publishing or target authority contract is handled as an execution-policy incompatibility, not mislabeled as a missing-schema error. Third-party discovery does not become a requirement to adopt MoonMind-specific schema extensions.

## Validation and Conformance

The shared normalizer, renderer, preset expander, API, and runtime boundary must demonstrate:

- Schema-less third-party Skills remain importable and usable under ordinary admitted policy.
- Equivalent Skill and Preset schemas receive equivalent generated controls.
- User inputs retain types, required alternatives, errors, and safe draft values.
- Context-bound repository/branch/publication fields are absent from editable guided, Advanced, and raw authoring surfaces.
- A workflow-context change updates every bound consumer and invalidates stale lookups and expansions.
- Required bound inputs validate successfully when context supplies them, and missing context points to the actual editable source.
- Conflicting duplicate values, unsupported projections, forged resolved provenance, and malformed binding metadata fail before mutation.
- Existing-PR targets and genuine source/destination/comparison roles are not collapsed by field-name heuristics.
- Publication binding forwards frozen scope intent, never a coordinator's local None, retired workspace fallback, or unresolved authoring Auto.
- Schema, binding, and context digests survive apply/reapply, unexpanded submit, API/MCP, schedules, edit/rerun, and recovery without changing admitted authority.
- Harmless unknown hints degrade safely; authority-bearing unknowns do not.
- No secrets enter defaults, input-contract metadata, provenance, or diagnostics.

These are target contracts, not an assertion that the current form or seeds already satisfy them.
