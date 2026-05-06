# Feature Specification: Schema-Driven Capability Inputs

**Feature Branch**: `308-schema-driven-capability-inputs`
**Created**: 2026-05-06
**Status**: Draft
**Input**:

````text
# MM-593: Implement schema-driven preset and skill inputs on Create page

Source Jira issue: MM-593
Issue type: Story
Current Jira status: In Progress
Priority: Medium
Project: MM
Labels: None on Jira fields
Assignee: Nate Sticco
Reporter: Nate Sticco

## Jira Preset Brief Source

The trusted Jira issue response did not expose a separate normalized preset brief or recommended preset instructions field. The canonical MoonSpec input below is synthesized from the issue key, summary, Jira description, acceptance criteria, implementation notes, and task list present in the issue description. Preserve `MM-593` in generated MoonSpec artifacts and pull request text.

## Issue Description

Title: Implement schema-driven preset and skill inputs on Create page, including reusable Jira issue picker

Issue Type: Story
Priority: High
Labels: create-page, presets, skills, schema-driven-inputs, jira, agent-skills, workflow-authoring

### Story

As a MoonMind user, I want presets and skills to declare their required inputs through a schema so that the Create page can automatically render the correct fields, such as a Jira issue picker, without hard-coding each preset or skill into Create page logic.

### Background

MoonMind’s preset system is moving toward a declarative, schema-driven composition model. Presets should declare their expected inputs through input_schema, optionally provide ui_schema widget hints, and rely on backend-owned expansion for preview, apply, reapply, and submit-time expansion. The Create page should render fields dynamically from the selected capability’s schema and must not contain preset-specific branches like if presetId === "jira-orchestrate"【turn58file0†L1】.

The Create page design now explicitly requires schema-generated capability inputs for presets, skills, and other capability types. It should use a generic schema-form renderer, a reusable widget registry, and support submit-time expansion of configured but unexpanded preset steps【turn59file0†L1】.

The Step Types model defines tool, skill, and preset as the canonical normalized step types, with presets acting as authoring-time composition steps that expand into executable tool and/or skill steps before runtime execution【turn60file0†L1】.

Also reference the prepared design updates:

- SkillSystem.updated.md
- JiraIntegration.updated.md

### Goal

Implement the end-to-end foundation for schema-driven capability inputs so a new preset or skill can request inputs, including Jira issue inputs, without requiring Create page code changes for that specific preset or skill.

The initial proving case should be jira-orchestrate, which declares a jira_issue input and causes the Create page to render the reusable jira.issue-picker widget automatically.

---

## Scope

### Backend

Implement or update the preset/capability catalog so presets expose a normalized schema contract:

```
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
    "preview": true,
    "apply": true,
    "submitTimeExpansion": true
  }
}
```

Update jira-orchestrate to declare a Jira issue input:

```
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

Add backend validation for preset inputs using the same schema returned to the frontend.

Add field-addressable validation errors:

```
{
  "path": "steps[0].preset.inputs.jira_issue.key",
  "message": "A Jira issue is required.",
  "code": "required"
}
```

Create or consolidate one backend preset expansion path:

```
expandPreset(preset_id, inputs, context) -> expanded_steps
```

Use that same path for:

- preview
- apply
- reapply
- submit-time auto-expansion
- API-created tasks
- task edit/re-run reconstruction where applicable

Support explicit input binding during expansion:

```
steps:
  - type: skill
    skill_id: code.implementation
    inputs:
      repository: "{{ context.repository.full_name }}"
      branch: "{{ context.branch.name }}"
      jira_issue: "{{ inputs.jira_issue }}"
      instructions: "Implement {{ inputs.jira_issue.key }} and prepare a pull request."
```

Attach provenance to expanded steps:

```
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

Add recursive preset expansion support with cycle detection.

### Frontend / Create Page

Add a generic schema-form renderer for capability inputs.

The renderer should support at least:

- type
- title
- description
- default
- required
- properties
- items
- enum
- oneOf
- anyOf
- format
- uiSchema
- x-moonmind-* hints

Add a reusable widget registry.

Initial widgets:

- text
- textarea
- number
- checkbox
- select
- multi-select
- json
- jira.issue-picker
- github.branch-picker
- provider.profile-picker
- model-picker
- file-reference-picker

The Create page must render preset and skill inputs from inputSchema / uiSchema.

The Create page must not contain preset-specific logic such as:

```
if (preset.id === "jira-orchestrate") {
  return <JiraOrchestrateSpecialForm />
}
```

It may contain generic widget registration:

```
widgetRegistry["jira.issue-picker"] = JiraIssuePickerInput
```

Persist configured preset step state:

```
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

Support these preset states:

- preset selected but required inputs missing
- preset configured but not expanded
- preset preview generated
- preset applied into editable child steps
- preset submitted without manual expansion
- preset expansion failed with field-addressable errors

### Jira Issue Picker

Implement jira.issue-picker as a reusable schema widget.

It should support:

- issue search by key and summary
- manual key entry when allowed
- display of key, summary, status, assignee, and URL where available
- storage of at least { "key": "MOON-123" }
- optional enrichment with summary, description, URL, status, and assignee
- graceful handling when Jira integration is unavailable
- preservation of manually entered values after validation failures

The picker must not receive or expose Jira credentials. Jira credentials must remain resolved only inside trusted MoonMind-side Jira tool handlers.

### Skill Alignment

Normalize skill catalog metadata to support the same input contract shape as presets:

```
{
  "id": "code.implementation",
  "kind": "skill",
  "label": "Code Implementation",
  "description": "Implement a requested change in a repository and prepare reviewable output.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {},
  "runtimeCompatibility": ["codex_cli", "claude_code", "gemini_cli"]
}
```

Skill input schemas should align with Agent Skills-style manifests and use the same schema-form renderer as presets.

### Submit-Time Auto-Expansion

When the user clicks Create Task with unexpanded preset steps:

1. Validate normal task fields.
2. Validate each preset step’s inputs.
3. Expand all unexpanded presets through the backend expansion service.
4. Recursively expand nested presets.
5. Validate the final concrete step list.
6. Submit the executable workflow.

Runtime payloads should contain executable tool and/or skill steps by default, not unresolved preset steps.

---

## Acceptance Criteria

### Schema-driven rendering

Given a preset manifest declares an input_schema and ui_schema,
when the user selects that preset on the Create page,
then the Create page renders the required fields dynamically without preset-specific React code.

### Jira issue picker

Given jira-orchestrate declares jira_issue with widget: jira.issue-picker,
when the user selects jira-orchestrate,
then the Create page renders the reusable Jira issue picker.

### No hard-coded preset logic

Given a new preset is added with supported schema fields and widget hints,
when the user selects that preset,
then the Create page renders its inputs without any new preset-specific code.

### Required input validation

Given a selected preset requires jira_issue.key,
when the user submits without selecting or entering a Jira issue,
then Create Task is blocked and the error appears next to the Jira issue field.

### Manual Jira key entry

Given Jira integration lookup is unavailable and manual entry is allowed,
when the user enters MOON-123,
then the value is preserved and submitted as:

```
{
  "key": "MOON-123"
}
```

### Preview

Given a preset has valid inputs,
when the user clicks Preview,
then the frontend calls the backend expansion service and displays generated steps without mutating the draft step list.

### Apply

Given a preset preview succeeds,
when the user clicks Apply,
then generated child steps are inserted into the draft, remain editable, and retain preset provenance.

### Submit without manual expansion

Given a preset step is configured but not expanded,
when the user clicks Create Task,
then the backend validates and expands the preset before workflow creation.

### Provenance

Given a preset expands into Skill or Tool steps,
when those generated steps are shown or executed,
then each generated step includes source preset ID, version, and safe input snapshot metadata.

### Backend-owned expansion

Given preview, apply, reapply, and submit-time expansion are all supported,
when each path expands the same preset with the same inputs and context,
then they use the same backend expansion service and produce consistent output.

### Skill compatibility

Given a Skill exposes inputSchema / uiSchema,
when the Skill is selected directly or generated from a preset,
then its inputs are validated against the Skill schema before runtime launch.

### Jira credential safety

Given the Jira issue picker or Jira tools are used,
when Jira data is fetched or modified,
then raw Jira credentials are never exposed to the managed agent, workflow payloads, logs, artifacts, or Create page schema defaults.

---

## Implementation Tasks

### Backend

- Add/normalize capability catalog response shape for presets and skills.
- Add input_schema, ui_schema, and defaults support to preset manifests.
- Update jira-orchestrate manifest with jira_issue schema.
- Add JSON Schema-compatible validation for preset inputs.
- Add field-addressable validation error shape.
- Implement shared expandPreset service.
- Wire preview/apply/reapply/submit-time expansion to the shared service.
- Add safe binding-expression evaluation for preset inputs.
- Add recursive nested preset expansion with cycle detection.
- Add provenance metadata to generated steps.
- Ensure expanded Tool and Skill steps validate against their own schemas.
- Add or update Jira issue validation/enrichment through trusted Jira backend tooling.

### Frontend

- Add generic schema-form renderer.
- Add widget registry.
- Add jira.issue-picker widget.
- Add preset input draft state.
- Add frontend schema validation for immediate feedback.
- Add backend error-to-field mapping.
- Add preset preview/apply/reapply UX.
- Add submit-time handling for configured but unexpanded presets.
- Remove any preset-specific Create page branches.
- Add provenance display such as “Generated by Jira Orchestrate.”

### Tests

- Unit test preset catalog schema loading.
- Unit test schema validation and field-addressable errors.
- Unit test safe binding expression evaluation.
- Unit test nested preset cycle detection.
- Frontend test dynamic rendering from preset schema.
- Frontend test Jira picker selection from uiSchema, not preset ID.
- Frontend test manual Jira key entry.
- Frontend test missing required input blocks submit.
- Integration test preview expansion.
- Integration test apply expansion.
- Integration test submit-time auto-expansion.
- Regression test adding a new preset with supported widgets requires no Create page code changes.
- Security regression test Jira credentials never appear in payloads, logs, artifacts, or agent-visible output.

---

## Out of Scope

- Building a custom Create page form for every preset.
- Making Jira Orchestrate a special Create page mode.
- Exposing arbitrary Jira HTTP requests to agents.
- Injecting Jira credentials into managed agent environments.
- Introducing linked-preset runtime execution.
- Replacing existing Tool/Skill runtime execution semantics.

---

## Definition of Done

- jira-orchestrate declares its required Jira issue input in schema.
- The Create page renders the Jira issue picker from schema metadata.
- Preset inputs are stored in draft state and survive validation failures.
- Preview, apply, reapply, and submit-time expansion use the same backend expansion service.
- Unexpanded configured preset steps can be submitted and are expanded before workflow creation.
- Generated steps retain preset provenance.
- Skill and preset input schemas use the same generic schema-form infrastructure.
- Tests prove a new preset can add supported inputs without Create page-specific code changes.
- Jira credentials remain isolated to trusted backend/worker Jira tool handlers.
- Relevant docs are updated or linked:
    - docs/Tasks/TaskPresetsSystem.md
    - docs/UI/CreatePage.md
    - docs/Steps/StepTypes.md
    - docs/Steps/SkillSystem.md
    - docs/Steps/JiraIntegration.md

````

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Render Capability Inputs From Schema

**Summary**: As a Create-page task author, I want presets and skills to render their required inputs from capability schemas so that I can configure Jira-backed and other schema-declared capabilities without MoonMind adding one-off Create-page forms for each capability.

**Goal**: A task author can select a preset or skill that declares an input contract, see the correct fields generated from that contract, provide or preserve required values such as a Jira issue key, and receive field-specific validation feedback before preview, apply, or submission flows use those inputs.

**Independent Test**: Can be fully tested by registering or seeding a capability with an input schema and UI schema, selecting it on the Create page, verifying the generated form and Jira issue picker appear from metadata rather than capability ID, and confirming valid and invalid inputs are preserved and validated consistently.

**Acceptance Scenarios**:

1. **Given** a selectable preset exposes a supported input schema, UI schema, defaults, and validation metadata, **When** a task author selects that preset, **Then** the Create page renders the required and optional input fields from the schema without using a preset-specific form branch.
2. **Given** a selectable skill exposes the same shape of input contract, **When** a task author selects that skill directly or receives it from a preset expansion preview, **Then** the Create page can render and validate its inputs through the same schema-form behavior used for presets.
3. **Given** a capability field requests the `jira.issue-picker` widget through UI schema or a supported schema hint, **When** the task author configures the field, **Then** the page renders the reusable Jira issue picker, supports selecting or manually entering an issue key when allowed, and stores a durable issue value containing at least the key.
4. **Given** a required schema field is missing or invalid, **When** the task author previews, applies, or submits the capability, **Then** the system blocks the action that depends on the missing value, reports a field-addressable error next to the relevant input, and preserves the draft values already entered.
5. **Given** Jira lookup is unavailable but manual key entry is allowed, **When** the task author enters `MM-593` or another valid-looking issue key manually, **Then** the value remains in the draft and can be validated or enriched later through trusted Jira tooling without exposing credentials to the page or agent runtime.
6. **Given** a new capability is added with only supported schema constructs and already-registered widgets, **When** a task author selects it, **Then** its inputs render and validate without adding capability-ID-specific Create-page code.

### Edge Cases

- Unknown widgets must degrade to a safe standard input only when the schema permits a safe fallback; otherwise the affected field shows an actionable unsupported-widget error.
- Schema descriptions and defaults must be treated as untrusted data: descriptions are sanitized before display and defaults must never expose secret-like values.
- Nested object and array values must preserve user-entered data across validation failures, preview failures, and integration lookup outages.
- Optional Jira enrichment fields such as summary, URL, status, and assignee may be absent; the durable issue key remains sufficient for draft preservation and later validation.
- Backend validation remains authoritative even when frontend validation passes.
- This story must not require submit-time preset auto-expansion, recursive preset expansion, or generated-step provenance to be completed in the same implementation slice.

## Assumptions

- The initial implementation can focus on the schema concepts and widget types needed by the MM-593 Jira Orchestrate proving case while preserving an extensible shape for the remaining documented widgets.
- Existing manual preview/apply and submit-time expansion stories remain separate downstream or existing specs; this story owns capability input rendering and validation before those flows consume the configured values.
- Trusted Jira tools remain the only place where Jira credentials are resolved; schema-driven Jira inputs carry safe issue references and sanitized context only.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Steps/StepTypes.md` lines 98-131 and `docs/UI/CreatePage.md` lines 133-165. Selectable capabilities must expose a normalized input contract with schema, UI metadata, defaults, and validation metadata, and the Create page must consume it without custom forms per capability. Scope: in scope. Mapped to FR-001, FR-002, FR-003, FR-004, FR-010.
- **DESIGN-REQ-002**: Source `docs/Tasks/TaskPresetsSystem.md` lines 127-145 and `docs/UI/CreatePage.md` lines 151-165. The schema-form behavior must support the documented JSON Schema concepts and allow new fields through manifests rather than preset-specific frontend branches. Scope: in scope. Mapped to FR-003, FR-004, FR-005, FR-010.
- **DESIGN-REQ-003**: Source `docs/UI/CreatePage.md` lines 167-237 and `docs/Steps/StepTypes.md` lines 372-395. Widget selection must be driven by reusable widget metadata and an allowlisted widget registry, including `jira.issue-picker`, not by preset, skill, or tool IDs. Scope: in scope. Mapped to FR-006, FR-007, FR-008.
- **DESIGN-REQ-004**: Source `docs/Steps/JiraIntegration.md` lines 79-143 and lines 181-210. Jira issue inputs must be schema-driven, reusable across capabilities, support allowed manual entry, preserve entered values during outages, and report field-addressable validation errors. Scope: in scope. Mapped to FR-006, FR-007, FR-008, FR-009.
- **DESIGN-REQ-005**: Source `docs/Steps/JiraIntegration.md` lines 147-178 and lines 605-622. A Jira issue input stores at least a durable key, treats enrichment as optional sanitized context, and relies on trusted Jira tooling for exact issue validation or enrichment. Scope: in scope. Mapped to FR-009, FR-011, FR-012.
- **DESIGN-REQ-006**: Source `docs/Tasks/TaskPresetsSystem.md` lines 474-496 and `docs/UI/CreatePage.md` lines 488-501. Catalog responses and validation surfaces must expose enough metadata for schema-driven UI generation and field-addressable errors. Scope: in scope. Mapped to FR-001, FR-002, FR-004, FR-005, FR-008.
- **DESIGN-REQ-007**: Source `docs/Steps/JiraIntegration.md` lines 55-75 and lines 1051-1059. Raw Jira credentials, auth headers, tokens, and secret defaults must not be exposed to managed agents, workflow payloads, logs, artifacts, or schema defaults. Scope: in scope. Mapped to FR-011, FR-012.
- **DESIGN-REQ-008**: Source `docs/UI/CreatePage.md` lines 503-522 and `docs/Tasks/TaskPresetsSystem.md` lines 548-563. Validation must prove schema-rendered fields, Jira widget metadata selection, manual key preservation, missing required input errors, unsupported widget safety, and adding a new capability without preset-specific Create-page logic. Scope: in scope. Mapped to FR-013.
- **DESIGN-REQ-009**: Source MM-593 Jira preset brief. The end-to-end brief also includes preview/apply, recursive expansion, submit-time auto-expansion, provenance, pull-request workflow, and broader task coverage. Scope: out of scope for this selected story because those are independently testable stories already represented by existing specs or later breakdown candidates.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose selectable preset and skill input contracts in a normalized shape that includes schema, optional UI metadata, defaults, and validation metadata needed by the Create page.
- **FR-002**: The normalized capability input contract MUST preserve field names, required markers, defaults, and UI hints losslessly between the stored capability definition and the Create-page response.
- **FR-003**: The Create page MUST render capability input fields by reading the selected capability's schema and existing draft values.
- **FR-004**: The schema-form behavior MUST support object fields, string fields, numeric fields, boolean fields, arrays, enums, one-of or any-of choices when present, standard string formats, defaults, required markers, descriptions, and namespaced MoonMind hints.
- **FR-005**: Required and invalid input values MUST produce field-addressable validation errors that can be displayed next to the affected generated field.
- **FR-006**: The Create page MUST resolve widget names through a reusable allowlisted widget registry rather than through preset IDs, skill IDs, tool IDs, or workflow-specific form branches.
- **FR-007**: The `jira.issue-picker` widget MUST be selected when supported UI metadata or schema hints request it for a field.
- **FR-008**: The Jira issue picker MUST support displaying selected issue context, preserving manually entered keys when allowed, and storing a durable value containing at least an issue key.
- **FR-009**: Jira issue validation and enrichment MUST tolerate missing optional enrichment fields and MUST use trusted Jira tooling when exact issue verification is needed.
- **FR-010**: A capability added with supported schema constructs and already-registered widgets MUST render its configured inputs without new capability-specific Create-page code.
- **FR-011**: Schema defaults, draft values, validation errors, logs, artifacts, and agent-visible payloads MUST NOT include raw Jira credentials, auth headers, tokens, cookies, or resolved secret values.
- **FR-012**: Jira issue input values exposed outside trusted Jira handlers MUST contain only safe issue identifiers and sanitized optional context.
- **FR-013**: Test coverage MUST include schema-driven rendering, Jira widget selection by metadata, manual Jira key preservation, missing required input validation, unsupported-widget safety, and adding a new capability without capability-specific Create-page logic.

### Key Entities

- **Capability Input Contract**: The normalized input metadata for a selectable preset, skill, or tool, including schema, UI hints, defaults, validation metadata, and capability identity.
- **Schema-Generated Field**: A Create-page field produced from the selected capability input contract, with draft value, required state, validation state, and optional widget metadata.
- **Widget Registry Entry**: An allowlisted mapping from a semantic widget name to reusable field behavior such as text input, select input, JSON editor fallback, or Jira issue picker.
- **Jira Issue Input Value**: A safe draft value containing at least an issue key and optionally sanitized enrichment fields such as summary, URL, status, and assignee.
- **Field-Addressable Validation Error**: A validation result that identifies the exact generated field path, a user-facing message, and a stable error code.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A seeded preset with at least one required schema input renders that input on the Create page in 100% of covered selection tests without relying on preset-specific form code.
- **SC-002**: A seeded skill with the same supported input shape renders through the same schema-form behavior in at least one covered direct-skill selection test.
- **SC-003**: In 100% of covered Jira issue input tests, `jira.issue-picker` is selected from UI metadata or schema hints and not from the capability ID.
- **SC-004**: In 100% of covered missing-required-field tests, preview, apply, or submit actions that require the value are blocked and the error is associated with the generated field path.
- **SC-005**: In 100% of covered Jira outage or unavailable-lookup tests where manual entry is allowed, the manually entered issue key remains available in the draft after validation feedback.
- **SC-006**: At least one regression test proves a newly added capability using supported schema constructs and existing widgets renders without adding new capability-specific Create-page code.
- **SC-007**: Security regression coverage confirms raw Jira credentials and secret-like schema defaults are absent from generated field defaults, persisted safe input values, logs, artifacts, and agent-visible payloads in the covered flows.
