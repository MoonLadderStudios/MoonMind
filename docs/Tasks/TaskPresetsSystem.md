# Task Presets System

## Status

Desired-state architecture.

This document defines MoonMind's task preset system as a declarative, schema-driven composition layer. Presets are reusable step plans that may request typed inputs from the user, expand into one or more executable steps, and preserve provenance so task runs remain understandable after expansion.

## Purpose

Task presets let a user start from a known workflow shape without manually authoring every step. A preset may represent a simple one-step action, a multi-step coding workflow, a Jira-driven orchestration flow, a proposal/remediation flow, or another composed task pattern.

The preset system must make the Create page easier to use without making the Create page responsible for knowing the details of every preset. Presets describe their own inputs through a machine-readable schema. The Create page renders those inputs automatically from the schema, validates them, and passes the collected values to the shared preset expansion path.

## Design Goals

- Presets are first-class step types, not a separate Create page mode.
- A preset may remain unexpanded while the user configures it.
- The user may submit a task with unexpanded preset steps; submission expands them automatically after validation.
- The Create page never hard-codes preset-specific forms such as `if presetId === "jira-orchestrate"`.
- Each preset declares its expected inputs with an `input_schema` that can drive UI generation, API validation, apply, reapply, and submit-time expansion.
- Preset input schemas align with the same direction as MoonMind skill input schemas and Agent Skills-style declarative capability metadata.
- Presets may compose other presets without losing input validation, provenance, or debuggability.
- Expansion is deterministic and backend-owned.

## Non-Goals

- Presets are not a replacement for skills. A skill is a reusable agent capability or instruction bundle. A preset is a reusable task/step composition that may invoke skills, scripts, managed agents, external agents, or other presets.
- Presets do not require custom React code for each preset. Only reusable field widgets may have custom components.
- Presets do not require the user to manually expand them before task creation.
- Presets do not grant arbitrary execution rights. Expanded steps still pass through the same validation, policy, runtime, and publishing controls as manually authored steps.

## Core Concepts

### Preset

A preset is a catalog entry that defines metadata, optional user inputs, and an expansion plan. It may expand into one or more concrete steps or into nested preset steps that are recursively expanded.

### Preset Step

A preset step is a step on the Create page with `type: preset`, a `preset_id`, and collected `inputs`. It can be configured and submitted before it is expanded.

### Input Schema

`input_schema` is the canonical machine-readable contract for the inputs a preset expects. It is JSON Schema-compatible and intentionally similar to the input schemas used by MoonMind skills and Agent Skills-style skill manifests.

### UI Schema

`ui_schema` is optional metadata that gives the Create page hints about presentation and widgets without changing validation semantics. The Create page may use `ui_schema` and recognized `x-moonmind-*` extensions to select reusable components such as a Jira issue picker.

### Expansion

Expansion transforms a preset step plus validated inputs into concrete child steps. The backend owns expansion so apply, submit, API-driven task creation, and re-run flows share the same behavior.

### Provenance

Provenance records which preset produced each expanded step, which preset version was used, and which input values or redacted input references influenced expansion.

## Preset Catalog Contract

A preset catalog entry should follow this shape:

```yaml
id: jira-orchestrate
kind: preset
version: 1
label: Jira Orchestrate
description: Build and execute an implementation workflow from a Jira issue.
category: issue-tracker

input_schema:
  type: object
  required:
    - jira_issue
  properties:
    jira_issue:
      type: object
      title: Jira issue
      description: Select the Jira issue that should seed the task instructions.
      required:
        - key
      properties:
        key:
          type: string
          title: Issue key
        summary:
          type: string
          title: Summary
        description:
          type: string
          title: Description
        url:
          type: string
          title: URL
          format: uri

ui_schema:
  jira_issue:
    widget: jira.issue-picker
    placeholder: Select a Jira issue
    data_source: jira.issues
    display_template: "{{ key }} — {{ summary }}"

expansion:
  steps:
    - type: skill
      skill_id: jira-orchestrate
      title: "Implement {{ inputs.jira_issue.key }}"
      inputs:
        jira_issue_key: "{{ inputs.jira_issue.key }}"
        jira_issue_summary: "{{ inputs.jira_issue.summary }}"
        jira_issue_description: "{{ inputs.jira_issue.description }}"
        jira_issue_url: "{{ inputs.jira_issue.url }}"
```

The exact persisted representation may evolve, but these concepts are required:

- stable preset identity
- human-readable label and description
- semantic category or tags
- version
- JSON Schema-compatible `input_schema`
- optional `ui_schema`
- deterministic expansion definition
- provenance metadata for expanded steps

## Input Schema Strategy

`input_schema` is the source of truth for generated inputs. The Create page renders fields by inspecting the selected preset's schema. A preset that expects a Jira issue, branch name, provider profile, model override, boolean option, enum, file reference, or nested object should be able to declare that requirement without adding preset-specific logic to the Create page.

The schema must support at least:

- `type`
- `title`
- `description`
- `default`
- `required`
- `properties`
- `items`
- `enum`
- `oneOf` / `anyOf` where needed for advanced forms
- `format` for standard strings such as URI, email, date, date-time, and path-like values
- custom extension fields prefixed with `x-moonmind-*` when metadata belongs with the schema

The preferred default is standard JSON Schema plus optional `ui_schema`. MoonMind-specific behavior should be expressed as reusable semantic widget hints rather than preset-specific frontend branches.

Valid:

```yaml
ui_schema:
  jira_issue:
    widget: jira.issue-picker
```

Also valid when colocating the hint is more convenient:

```yaml
input_schema:
  type: object
  properties:
    jira_issue:
      type: object
      title: Jira issue
      x-moonmind-widget: jira.issue-picker
```

Not valid as an architecture pattern:

```tsx
if (preset.id === "jira-orchestrate") {
  return <JiraOrchestrateSpecialForm />
}
```

The Create page may contain a reusable widget registry. It may map `jira.issue-picker` to a Jira issue picker component, `github.branch-picker` to a branch picker, or `provider.profile-picker` to a provider profile selector. That is generic field rendering, not preset-specific logic.

## Alignment With Skills and Agent Skills

Preset input schemas should align with MoonMind skill input schemas so the same schema-form renderer can be reused for both presets and skills.

The desired direction is compatible with Agent Skills-style manifests:

- a capability declares metadata in a machine-readable manifest
- the manifest includes a typed input contract
- UI and orchestration layers can discover expected inputs without custom code for every capability
- richer behavior is expressed through portable schema fields and clearly namespaced extensions
- runtime-specific or product-specific UI hints are optional and do not change the core input contract

MoonMind should normalize preset and skill manifests into a shared internal capability input model. If Agent Skills standard field names differ from MoonMind's names, importers may support aliases, but MoonMind's internal canonical field for presets is `input_schema`.

The shared internal model should be able to represent:

```yaml
capability_id: jira-orchestrate
capability_type: preset
input_schema: {}
ui_schema: {}
defaults: {}
```

and:

```yaml
capability_id: moonspec-breakdown
capability_type: skill
input_schema: {}
ui_schema: {}
defaults: {}
```

The Create page should not care whether the selected step type is a preset or a skill when rendering the input form. It should receive a normalized schema contract and render the appropriate fields.

## UI Generation Rules

When a user selects a preset on the Create page:

1. The frontend loads the preset metadata from the catalog API.
2. The frontend reads `input_schema`, `ui_schema`, defaults, and any already-collected values.
3. The generic schema-form renderer creates fields for required and optional inputs.
4. Fields are validated locally for immediate feedback.
5. The same values are validated again by the backend before apply or submit.
6. The configured preset step stores the input values while remaining unexpanded if the user has not chosen to apply expansion.

The Create page must support these states:

- no preset selected
- preset selected but required inputs missing
- preset configured but not expanded
- preset applied into editable child steps
- preset submitted without manual expansion
- preset expansion failed due to invalid inputs or backend validation errors

The Create page may group fields, show descriptions, display examples, and use custom widgets. It must not require a custom code path for each preset.

## Generic Widget Registry

The schema-form renderer uses a reusable widget registry. Initial widgets should include:

| Widget | Purpose |
| --- | --- |
| `text` | single-line string input |
| `textarea` | multi-line string input |
| `number` | numeric input |
| `checkbox` | boolean input |
| `select` | enum / one-of selector |
| `multi-select` | array of enum values |
| `json` | advanced structured object editor |
| `jira.issue-picker` | Jira issue search and selection |
| `github.branch-picker` | repository branch selection |
| `provider.profile-picker` | provider profile selection |
| `model-picker` | model selection constrained by provider/runtime |
| `file-reference-picker` | artifact or uploaded file reference selection |

Only widgets are allowed to have custom UI components. Presets consume widgets declaratively through schema metadata.

## Jira Issue Input Pattern

A Jira-driven preset should request a Jira issue as an input object rather than relying only on free-text instructions. This allows the Create page to display a picker, the backend to validate the issue reference, and the expansion layer to bind the issue fields into child steps.

Recommended schema:

```yaml
input_schema:
  type: object
  required:
    - jira_issue
  properties:
    jira_issue:
      type: object
      title: Jira issue
      description: Issue that will seed the task instructions and orchestration context.
      required:
        - key
      properties:
        key:
          type: string
        summary:
          type: string
        description:
          type: string
        url:
          type: string
          format: uri
        status:
          type: string
        assignee:
          type: string

ui_schema:
  jira_issue:
    widget: jira.issue-picker
    data_source: jira.issues
    search_placeholder: Search Jira issues
    allow_manual_key_entry: true
```

The Jira widget may enrich the selected value with summary, description, URL, status, and assignee, but the minimum durable input is the issue key. Expansion must tolerate missing optional enrichment fields by fetching or summarizing the issue during backend validation when possible.

## Preset Step Shape

Before expansion, a preset step should be representable as:

```json
{
  "type": "preset",
  "preset_id": "jira-orchestrate",
  "title": "Jira Orchestrate",
  "inputs": {
    "jira_issue": {
      "key": "MOON-123",
      "summary": "Add schema-driven preset inputs",
      "url": "https://example.atlassian.net/browse/MOON-123"
    }
  },
  "expansion_state": "not_expanded"
}
```

After apply or submit-time expansion, generated steps should include provenance:

```json
{
  "type": "skill",
  "skill_id": "jira-orchestrate",
  "title": "Implement MOON-123",
  "inputs": {
    "jira_issue_key": "MOON-123"
  },
  "provenance": {
    "source_type": "preset",
    "preset_id": "jira-orchestrate",
    "preset_version": "1",
    "input_snapshot": {
      "jira_issue": {
        "key": "MOON-123"
      }
    }
  }
}
```

## Expansion Semantics

Preset expansion must be available through one shared backend path:

```text
expandPreset(preset_id, inputs, context) -> expanded_steps
```

The same path is used by:

- preset apply
- preset reapply
- task submission with unexpanded presets
- API-created tasks
- task edit and re-run flows that reconstruct preset-originated steps

Expansion must:

1. Load the preset by ID and version.
2. Validate inputs against `input_schema`.
3. Apply defaults.
4. Resolve allowed contextual values such as repository, branch, issue tracker connection, or provider profile.
5. Expand nested presets recursively.
6. Produce concrete executable steps.
7. Attach provenance.
8. Return validation errors in a field-addressable format the Create page can display next to generated inputs.

Expansion must be deterministic for the same preset version, inputs, and context. If expansion requires fresh external data, the expansion output must record which external references were used.

## Input Binding

Preset expansion binds input values into generated steps through explicit expressions. Bindings must not rely on the frontend mutating instructions after expansion.

Example:

```yaml
steps:
  - type: skill
    skill_id: moonspec-breakdown
    inputs:
      jira_issue_key: "{{ inputs.jira_issue.key }}"
      jira_issue_summary: "{{ inputs.jira_issue.summary }}"
      jira_issue_description: "{{ inputs.jira_issue.description }}"
```

Bindings should be limited to a safe deterministic expression language. They should support reading from:

- `inputs.*`
- `context.project.*`
- `context.repository.*`
- `context.branch.*`
- `context.user.*` where safe
- `defaults.*`

Bindings must not execute arbitrary code.

## Nested Presets

A preset may include child preset steps. Parent presets must explicitly map inputs into child presets.

```yaml
steps:
  - type: preset
    preset_id: pr-with-merge-automation
    inputs:
      jira_issue: "{{ inputs.jira_issue }}"
      publish_mode: "pr_with_merge_automation"
```

Rules:

- Child presets are validated with their own `input_schema`.
- Parent-to-child input mappings are explicit.
- Missing required child inputs are reported with a path that identifies the parent preset and child preset.
- Recursive expansion must detect cycles and fail safely.
- Provenance records both parent and child preset ancestry.

## Submit-Time Auto-Expansion

The user may click Create Task while one or more preset steps are still unexpanded. The submit path must:

1. Validate all non-preset step fields.
2. Validate each preset step's collected inputs.
3. Expand all unexpanded preset steps through the backend expansion path.
4. Re-run final task validation on the fully expanded step list.
5. Submit the task.

If a required preset input is missing, Create Task is blocked and the missing field is highlighted. If backend expansion fails, the Create page displays the field-addressable errors and preserves the user's entered values.

## Apply and Reapply

Apply replaces or augments the draft with generated child steps, depending on the selected UX mode.

When applied, generated steps remain editable, but MoonMind must preserve provenance and the original preset input snapshot. Editing generated steps does not mutate the preset template.

Reapply should use the saved preset ID, version, and input snapshot unless the user explicitly changes inputs.

## Validation and Error Shape

Validation errors must be field-addressable so the schema-form renderer can display them next to the right input.

Example:

```json
{
  "errors": [
    {
      "path": "inputs.jira_issue.key",
      "message": "A Jira issue is required.",
      "code": "required"
    }
  ]
}
```

Nested preset errors should include ancestry:

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

## API Requirements

The preset catalog API must expose enough metadata for schema-driven UI generation:

```json
{
  "id": "jira-orchestrate",
  "kind": "preset",
  "version": "1",
  "label": "Jira Orchestrate",
  "description": "Build and execute an implementation workflow from a Jira issue.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {},
  "capabilities": {
    "apply": true,
    "submitTimeExpansion": true
  }
}
```

The API may expose camelCase to the frontend while storing snake_case internally, but the mapping must be lossless.

Required endpoints or equivalent service operations:

- list presets
- read preset details
- validate preset inputs
- expand preset for submission

## Security and Policy

Input schemas and UI schemas are untrusted catalog data. The frontend must treat them as data, not executable code.

Requirements:

- Widget names resolve only through a local allowlist registry.
- Markdown descriptions are sanitized before rendering.
- Binding expressions are evaluated by a safe deterministic interpreter.
- Secrets are never exposed through schema defaults.
- Sensitive input values are redacted from provenance unless explicitly classified as safe.
- Expanded steps pass through the same policy checks as manually created steps.

## Persistence

MoonMind should persist both the configured preset step and the expanded step provenance when useful.

Persisted records should support:

- draft reconstruction
- edit and re-run
- audit/debugging
- comparison between original preset inputs and edited generated steps
- future migrations when preset versions change

Preset versions are immutable once tasks have been created from them. Updating a preset creates a new version or equivalent revision identifier.

## Implementation Requirements

To fully realize this design, MoonMind needs:

1. A canonical preset manifest model with `input_schema`, optional `ui_schema`, defaults, expansion rules, and versioning.
2. Backend validation of preset inputs using the same schema returned to the frontend.
3. A generic schema-form renderer on the Create page.
4. A reusable widget registry for semantic widgets such as Jira issue picker.
5. Removal of preset-specific Create page branches.
6. A shared backend expansion service used by apply, reapply, submit-time auto-expansion, and API-created tasks.
7. Provenance on expanded steps.
8. Recursive expansion support with cycle detection.
9. Field-addressable validation errors.
10. Tests that prove new presets can add inputs without changing Create page logic.

## Testing Strategy

Tests should cover:

- catalog loading of a preset with `input_schema`
- frontend rendering of required and optional fields from schema
- Jira issue picker selection populating the configured preset input object
- validation failures for missing required fields
- submit-time auto-expansion for unexpanded preset steps
- nested preset input mapping
- cycle detection
- provenance on expanded steps
- adding a new preset with a new schema that uses existing widgets without modifying Create page preset logic

The key acceptance test is: a developer can add a new preset manifest with a supported schema and widget hints, and the Create page renders the required inputs automatically without any new preset-specific React code.

## Related Documents

- `docs/UI/CreatePage.md`
- `docs/Steps/StepTypes.md`
- `docs/Steps/SkillSystem.md`
- `docs/Steps/JiraIntegration.md`
- `docs/Tasks/TaskArchitecture.md`
- `docs/Tasks/TaskEditingSystem.md`
