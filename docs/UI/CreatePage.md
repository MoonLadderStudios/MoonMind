# Create Page

## Status

Desired-state architecture.

This document defines the MoonMind Create page as the primary task/workflow composition surface. It describes the desired page structure, step authoring model, schema-driven capability inputs, preset expansion behavior, Jira integration, publishing controls, and task submission flow.

## Purpose

The Create page lets a user describe work, choose how MoonMind should execute it, configure one or more steps, and submit the resulting workflow without needing to understand every backend orchestration detail.

The page should support simple one-step tasks while also scaling to advanced workflows that use skills, scripts, managed agents, external agents, presets, Jira issue context, dependencies, publishing, and merge automation.

## Design Principles

- The Create page is a composition surface, not a catalog of hard-coded workflows.
- Steps are the primary unit of authoring.
- A step has a `type`. The selected type determines which capability selector and input schema are shown.
- Presets are selected as a step type and may expand into multiple steps.
- Skills, presets, scripts, and other capability types declare inputs through schemas.
- The Create page renders inputs from schemas on the fly using a generic schema-form renderer.
- The Create page must not contain preset-specific branches such as `if presetId === "jira-orchestrate"`.
- Reusable field widgets may be custom; individual presets should not require custom page code.
- Users may submit configured but unexpanded preset steps. The backend validates and expands them at submit time.
- Backend expansion and validation are authoritative. The frontend may preview expansion, but it does not own expansion semantics.
- Advanced options should be available without overwhelming the default path.

## Primary User Flows

### Fast Path: Simple Task

1. User enters task instructions.
2. User selects repository and branch context if needed.
3. User accepts the default single step or chooses a step type.
4. User clicks **Create Task**.
5. MoonMind validates inputs and starts the workflow.

### Preset Path

1. User adds or edits a step.
2. User selects `Preset` as the step type.
3. User selects a preset from the preset catalog.
4. The Create page fetches the preset metadata, including `input_schema` and optional `ui_schema`.
5. The generic schema-form renderer generates required and optional inputs.
6. User fills required inputs, such as selecting a Jira issue.
7. User may preview/apply the preset or leave it unexpanded.
8. User clicks **Create Task**.
9. The backend validates inputs, expands the preset, validates the final expanded workflow, and submits the task.

### Jira-Orchestrated Path

1. User selects a Jira-oriented preset such as `Jira Orchestrate`.
2. The preset declares a `jira_issue` input in its schema.
3. The Create page displays the reusable Jira issue picker widget because the schema requests `jira.issue-picker`.
4. User selects or enters an issue.
5. The selected issue key and any enriched fields are stored as preset inputs.
6. Preset expansion binds the issue fields into generated steps.

No Jira preset may require a custom Create page branch. Jira is represented as a reusable field widget and a reusable integration context.

## Page Structure

The desired Create page is organized into these regions:

1. **Task Overview**
   - title or generated title preview
   - task instructions
   - repository/project context
   - branch selector
   - dependency or starting point context

2. **Publishing**
   - publish mode selector
   - branch / pull request / no-publish behavior
   - PR with merge automation option
   - target branch and generated branch name preview

3. **Steps**
   - ordered list of steps
   - step type selector
   - capability selector for the selected type
   - schema-generated capability inputs
   - preview/apply controls for presets
   - validation state per step

4. **Context and Attachments**
   - Jira issue context where applicable
   - file/artifact references
   - additional notes or constraints

5. **Review and Submit**
   - final validation summary
   - generated/expanded step preview where needed
   - Create Task action

The page may visually group these regions differently, but the underlying information architecture should remain stable.

## Step Authoring Model

A task draft contains an ordered list of steps. Each step has a type and type-specific capability metadata.

Supported desired step types include:

| Step Type | Meaning |
| --- | --- |
| `Instructions` | Plain agent instructions with default runtime behavior. |
| `Skill` | Invoke a MoonMind/Agent Skills-style skill bundle. |
| `Script` | Invoke a hardcoded script/executable capability. |
| `Preset` | Insert a reusable step composition that may expand into one or more steps. |
| `External Agent` | Delegate work to an external provider/integration such as a cloud coding agent. |
| `Managed Agent` | Run a managed local/session runtime such as Codex CLI, Claude Code, or Gemini CLI. |

The exact labels may evolve, but the page should preserve the concept that a step's type controls the capability selector and input schema.

A draft preset step may look like:

```json
{
  "type": "preset",
  "presetId": "jira-orchestrate",
  "title": "Jira Orchestrate",
  "inputs": {
    "jira_issue": {
      "key": "MOON-123",
      "summary": "Add schema-driven preset inputs"
    }
  },
  "expansionState": "not_expanded"
}
```

## Schema-Driven Capability Inputs

The Create page must use a shared schema-form renderer for preset inputs, skill inputs, and other capability inputs where practical.

A capability selected by a step may expose:

```json
{
  "id": "jira-orchestrate",
  "kind": "preset",
  "label": "Jira Orchestrate",
  "description": "Build and execute a task workflow from a Jira issue.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {}
}
```

The frontend renders fields by reading `inputSchema`, optional `uiSchema`, and existing draft values. The page should support standard JSON Schema concepts:

- `type`
- `title`
- `description`
- `default`
- `required`
- `properties`
- `items`
- `enum`
- `oneOf` / `anyOf` where needed
- `format`
- reusable MoonMind extensions such as `x-moonmind-widget`

The Create page must not hard-code a form for each preset or skill. New presets should be able to add new fields by updating their manifest, provided the fields use standard schema constructs or already-registered widgets.

## UI Schema and Widget Hints

Validation belongs in `inputSchema`. Presentation belongs in `uiSchema` or namespaced schema hints.

Example:

```yaml
inputSchema:
  type: object
  required:
    - jira_issue
  properties:
    jira_issue:
      type: object
      title: Jira issue
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

uiSchema:
  jira_issue:
    widget: jira.issue-picker
    searchPlaceholder: Search Jira issues
    allowManualKeyEntry: true
```

Equivalent colocated widget hint when convenient:

```yaml
inputSchema:
  type: object
  properties:
    jira_issue:
      type: object
      title: Jira issue
      x-moonmind-widget: jira.issue-picker
```

The Create page may understand `jira.issue-picker`, but it must not understand `jira-orchestrate` as a special page-level case.

## Generic Widget Registry

The schema-form renderer uses a local widget registry. Widgets are reusable field components, not workflow-specific forms.

Initial desired widgets:

| Widget | Use |
| --- | --- |
| `text` | single-line string input |
| `textarea` | multi-line string input |
| `number` | numeric input |
| `checkbox` | boolean input |
| `select` | enum/one-of selector |
| `multi-select` | array of enum values |
| `json` | advanced object editor fallback |
| `jira.issue-picker` | Jira issue lookup and selection |
| `github.branch-picker` | branch lookup and selection |
| `provider.profile-picker` | provider profile selection |
| `model-picker` | model selection constrained by provider/runtime |
| `file-reference-picker` | uploaded file or artifact reference selection |

Unknown widgets should degrade safely to a standard input if the schema permits it, or show an actionable unsupported-widget error if no safe fallback exists.

## Jira Issue Picker Behavior

The Jira issue picker is a reusable widget selected by schema metadata.

It should support:

- searching issues by key, title, status, or assignee when the Jira integration is available
- manual issue key entry when allowed by the schema
- displaying key, summary, status, and URL when available
- storing a durable value containing at least `key`
- enriching optional fields such as `summary`, `description`, `url`, `status`, and `assignee`
- showing integration setup errors without losing entered values

The minimum durable value is:

```json
{
  "key": "MOON-123"
}
```

An enriched value may be:

```json
{
  "key": "MOON-123",
  "summary": "Add schema-driven preset inputs",
  "description": "Create page should render preset inputs from schema.",
  "url": "https://example.atlassian.net/browse/MOON-123",
  "status": "Ready for Dev",
  "assignee": "Nathaniel Sticco"
}
```

Backend validation and expansion should tolerate missing optional enrichment fields and fetch missing Jira details when possible.

## Preset Selection and Configuration

When a user selects `Preset` as a step type:

1. The capability selector lists available presets from the preset catalog.
2. Selecting a preset loads its metadata.
3. If the preset has an `inputSchema`, the schema-form renderer appears under the selector.
4. Required fields are marked and validated.
5. Optional fields are available without cluttering the default path.
6. Preset-specific preview/apply controls appear only after required inputs are valid enough to attempt expansion.

Preset configuration should be saved in the task draft even if the preset is not expanded.

## Preview, Apply, and Reapply

The Create page supports three preset-related actions:

### Preview

Preview calls the backend expansion service and shows the generated steps without replacing the preset step.

### Apply

Apply calls the backend expansion service and inserts the generated child steps into the draft. Applied steps remain editable. Provenance is retained so the user can understand which preset produced them.

### Reapply

Reapply regenerates steps from the saved preset ID, preset version, and current inputs. If the user edited generated child steps, the UI should make clear that reapplying may replace or update those generated steps.

## Submit-Time Preset Auto-Expansion

The user may click **Create Task** when preset steps are configured but not expanded.

Submit behavior:

1. Run frontend validation for immediate feedback.
2. Send the draft to the backend submission path.
3. Backend validates all preset inputs against their schemas.
4. Backend expands unexpanded preset steps.
5. Backend recursively expands nested presets.
6. Backend validates the final concrete step list.
7. Backend starts the task/workflow.

If validation fails, the Create page receives field-addressable errors and displays them next to the generated schema fields.

Example error:

```json
{
  "path": "steps[0].inputs.jira_issue.key",
  "message": "A Jira issue is required.",
  "code": "required"
}
```

The user's entered values must be preserved after validation failures.

## Validation UX

Validation should happen in layers:

- local schema validation for required fields and obvious type errors
- backend schema validation for authoritative correctness
- integration validation for references such as Jira issues or GitHub branches
- expansion validation for generated step completeness
- final task validation after all presets are expanded

The UI should show errors as close as possible to the field or step that caused them. A summary at the bottom may list blocking issues, but it should not be the only place errors appear.

## Publishing Controls

Publishing is configured at the task level unless a step explicitly needs to override it.

The desired publish modes are:

| Mode | Meaning |
| --- | --- |
| `none` | Do not publish code changes. |
| `branch` | Push changes to a branch. |
| `pull_request` | Create or update a pull request. |
| `pr_with_merge_automation` | Create or update a pull request and schedule/launch merge automation. |

The Create page should make publishing visible as an explicit phase rather than burying it inside instructions.

When publish mode requires a branch, the Branch field should use a GitHub-backed branch picker and sensible generated branch defaults.

When publish mode includes merge automation, the page should expose merge automation defaults without overwhelming the user. Advanced options can include review wait behavior, CI wait behavior, Jira status triggers, and post-merge issue completion.

## Branch Selection

The page should use `Branch`, not `Target Branch`, as the primary field label unless there is a specific source/target distinction in the local context.

Branch behavior:

- Default to the repository's configured default branch when no dependency or prior branch is selected.
- If the task depends on another task with a branch or PR, offer that branch as the inferred starting point.
- Use a GitHub-backed branch dropdown when repository context is available.
- Allow manual branch entry as an advanced fallback.
- Show generated publish branch names before submission when relevant.

## Jira Integration on Create

Jira can appear in multiple places on the Create page:

- as a schema-driven preset input
- as a standalone context attachment
- as a source for task instructions
- as a trigger/context source for follow-up workflows
- as metadata for publishing or merge automation

The preferred flow is schema-driven. For example, a Jira Orchestrate preset declares that it needs a Jira issue, and the Create page renders a Jira picker because the schema requests the Jira widget.

The page may also support browsing Jira issues by board/column and filling instructions from a selected issue, but this should feed into the same draft/input model rather than create a separate one-off Jira flow.

## Capability Catalog Loading

The Create page needs catalog data for each selectable capability type:

- presets
- skills
- scripts/executables
- managed agents
- external agents

Each catalog item should expose normalized metadata:

```json
{
  "id": "moonspec-breakdown",
  "kind": "skill",
  "label": "MoonSpec Breakdown",
  "description": "Break a design into implementation-ready Jira stories.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {},
  "capabilities": {
    "preview": false,
    "apply": false
  }
}
```

The schema renderer should operate on this normalized shape regardless of whether the selected capability is a skill or preset.

## Draft Persistence

The Create page should preserve draft state across navigation and validation failures.

Draft state should include:

- task overview fields
- repository and branch context
- publish mode
- step list
- selected step types
- selected capability IDs
- capability input values
- preset expansion state
- preview results where appropriate
- applied-step provenance
- attachments and contextual references

Configured preset inputs must survive preview/apply/reapply and submit validation failures.

## Generated Step Provenance

When a preset is previewed, applied, or auto-expanded at submit time, each generated step should retain provenance metadata.

Example:

```json
{
  "sourceType": "preset",
  "presetId": "jira-orchestrate",
  "presetVersion": "1",
  "inputSnapshot": {
    "jira_issue": {
      "key": "MOON-123"
    }
  }
}
```

The UI may show this as a small “Generated by Jira Orchestrate” annotation. Provenance should help with debugging, reapply behavior, re-run behavior, and auditability.

## Accessibility and Usability

Schema-generated forms must meet the same usability bar as hand-authored fields.

Requirements:

- every generated input has a label
- descriptions are visible or available through help text
- validation errors are associated with fields
- keyboard navigation works across generated fields
- custom widgets expose accessible names and roles
- advanced/optional fields are discoverable
- generated forms remain readable at narrow widths

## Security Requirements

The Create page treats schema and UI metadata as data, not executable code.

Requirements:

- widget names resolve through an allowlist registry
- markdown descriptions are sanitized
- unsupported widgets fail safely
- schema defaults must not expose secrets
- secret-like values use write-only secret reference widgets when needed
- backend validation is mandatory even when frontend validation passes
- expanded steps still pass through policy checks

## Implementation Requirements

To complete this design, MoonMind needs:

1. A normalized capability catalog response for presets and skills with `inputSchema`, `uiSchema`, and defaults.
2. A generic schema-form renderer in the Create page.
3. A reusable widget registry with Jira issue picker support.
4. Preset step draft state that stores `presetId`, `inputs`, and `expansionState`.
5. Backend validation endpoints or service calls for preset/skill inputs.
6. A shared backend preset expansion service for preview, apply, reapply, and submit-time expansion.
7. Field-addressable error handling from backend validation and expansion.
8. Submit-time auto-expansion of unexpanded preset steps.
9. Provenance display for generated/applied preset steps.
10. Tests proving new presets can add fields without adding preset-specific Create page logic.

## Testing Strategy

Tests should verify:

- selecting a preset fetches its schema and renders fields dynamically
- required preset inputs block submission when missing
- Jira issue picker is selected by widget metadata, not preset ID
- manually entered Jira issue keys are preserved
- preview uses backend expansion and does not mutate the draft
- apply inserts generated steps and preserves provenance
- Create Task expands unexpanded preset steps at submit time
- nested presets produce field-addressable errors
- unsupported widgets fail safely
- a newly added preset using existing widgets appears and renders without Create page code changes

## Acceptance Criteria

The key acceptance criterion is:

A developer can add a new preset manifest with an `input_schema` and optional `ui_schema`, and the Create page automatically renders the required inputs, validates them, and submits the preset for backend expansion without any new preset-specific React code.

For the Jira example:

- `jira-orchestrate` declares a `jira_issue` input.
- The schema requests the reusable `jira.issue-picker` widget.
- The Create page renders the Jira issue input automatically.
- The user can select or enter an issue.
- The user can click Create Task without manually expanding the preset.
- The backend expands the preset and binds the Jira issue into the generated steps.

## Related Documents

- `docs/Tasks/TaskPresetsSystem.md`
- `docs/Steps/StepTypes.md`
- `docs/Steps/SkillSystem.md`
- `docs/Steps/JiraIntegration.md`
- `docs/Tasks/TaskArchitecture.md`
- `docs/Tasks/TaskPublishing.md`
- `docs/Tasks/PrMergeAutomation.md`
- `docs/UI/MissionControlArchitecture.md`
- `docs/UI/MissionControlDesignSystem.md`
