# Story Breakdown: Step Types

- Source design: `docs/Steps/StepTypes.md`
- Source reference path for every story: `docs/Steps/StepTypes.md`
- Extracted: `2026-04-28T21:58:44Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines MoonMind Step Types as the user-facing discriminator for task steps: Tool, Skill, and Preset. Tool and Skill are executable step types, while Preset is an authoring-time template that previews and expands into concrete executable steps. The document establishes UI terminology, discriminated API payloads, validation rules, preset provenance semantics, proposal promotion behavior, migration expectations, and explicit non-goals around Temporal Activities, arbitrary scripts, and hidden runtime preset work.

## Coverage Points

- `DESIGN-REQ-001` **Canonical Step Type model** (requirement, 1. Purpose; 2. Desired-State Summary; 3. Terminology): Every task step is authored with exactly one user-facing Step Type whose canonical values are tool, skill, and preset, and users should not need internal registry, Temporal, or adapter terms.
- `DESIGN-REQ-002` **Step Type controlled authoring form** (requirement, 2. Desired-State Summary; 6. User Experience Contract): Changing Step Type changes the editor fields, preserves compatible data, discards incompatible data clearly, or confirms destructive changes.
- `DESIGN-REQ-003` **Tool step semantics and contract** (requirement, 5.1 `tool`; 8.2 Tool validation): Tool steps represent typed, schema-backed, policy-checked deterministic operations with declared versions, schemas, authorization, capabilities, retry policy, binding, validation, and error model.
- `DESIGN-REQ-004` **Tool is not arbitrary script execution** (security, 4. Core Invariants; 5.1 `tool`; 8.2 Tool validation; 15. Non-Goals): Arbitrary shell snippets are not a Step Type; command execution is only allowed through explicitly approved typed command tools with bounded inputs and policy.
- `DESIGN-REQ-005` **Skill step semantics and contract** (requirement, 5.2 `skill`; 8.3 Skill validation): Skill steps invoke agent-facing reusable behavior for interpretation, planning, implementation, synthesis, or open-ended reasoning, with selector, instructions, context, runtime preferences, permissions, and autonomy controls.
- `DESIGN-REQ-006` **Preset authoring placeholder semantics** (state-model, 5.3 `preset`; 7.1 Authoring payload; 8.4 Preset validation): Preset steps are temporary authoring placeholders used to select templates, configure inputs, preview expansion, and apply generated steps; submitted tasks should not contain unresolved presets by default.
- `DESIGN-REQ-007` **Deterministic preset expansion** (requirement, 4. Core Invariants; 5.3 `preset`; 6.6 Preset preview and apply; 8.4 Preset validation): Preset expansion must deterministically generate concrete Tool and/or Skill steps, validate generated steps, enforce limits, expose warnings, and support preview before apply.
- `DESIGN-REQ-008` **Preset provenance metadata** (artifact, 5.3 `preset`; 7.1 Authoring payload; 13. Proposal and Promotion Semantics): Expanded preset-derived steps preserve source metadata for audit, grouping, proposal reconstruction, and review, but runtime correctness must not depend on this metadata.
- `DESIGN-REQ-009` **Preset management is separate from preset use** (requirement, 6.5 Preset picker; 12. Preset Management vs Preset Use): Preset use belongs in the step editor, while the Presets section is only for catalog management, lifecycle, governance, save-from-task, audit, and testing expansion.
- `DESIGN-REQ-010` **Preset preview and editing controls** (requirement, 6.6 Preset preview and apply): Users can preview generated steps, apply a preset, undo expansion, see origin, detach provenance, compare with source preset, and update to newer versions only explicitly.
- `DESIGN-REQ-011` **Runtime plan mapping** (integration, 7.2 Runtime plan mapping): Executable Tool and Skill steps compile into runtime plan nodes, while Preset has no runtime node by default and expands before submission; Temporal Activities remain an implementation detail.
- `DESIGN-REQ-012` **Discriminated API payload shape** (integration, 11. API Shape): The desired API uses a discriminated Step union with type values tool, skill, and preset, distinct sub-payloads, and executable submission normally accepting only ToolStep or SkillStep.
- `DESIGN-REQ-013` **Common validation requirements** (requirement, 8.1 Common validation): Every step requires stable identity, display title or label, Step Type, type-specific payload, and surfaced validation errors before submission.
- `DESIGN-REQ-014` **Backward-compatible reading with forward convergence** (migration, 7.3 Backward compatibility; 14. Migration Guidance): Migration may read legacy shapes such as skill, tool, skillId, template metadata, and legacy plan node values while new authoring surfaces converge on the Step Type model.
- `DESIGN-REQ-015` **Consistent product terminology** (constraint, 3. Terminology; 10. Naming Policy; 14. Phase 1): The UI label is Step Type; Tool remains the label for typed executable operations; avoid Capability, Activity, Invocation, Command, Script, Script as Tool label, and Executable as the main UI label.
- `DESIGN-REQ-016` **Temporal Activity remains implementation detail** (constraint, 3. Terminology; 7.2 Runtime plan mapping; 10.3 Keep `Activity` Temporal-specific; 15. Non-Goals): Activity means Temporal Activity and must not be exposed as a Step Type label or product discriminator.
- `DESIGN-REQ-017` **Jira workflow illustrates mixed deterministic and agentic work** (integration, 9. Jira Example): Jira state changes are Tool steps, Jira triage or implementation is a Skill step, and reusable Jira flows are Preset steps that expand into Tool and Skill steps.
- `DESIGN-REQ-018` **Proposal promotion preserves executable intent** (artifact, 13. Proposal and Promotion Semantics): Stored promotable task payloads should already be flattened executable Tool and Skill steps, promotion validates the reviewed flat payload, and refresh from catalog is explicit rather than automatic.
- `DESIGN-REQ-019` **Open design decisions are bounded** (non-goal, 16. Open Design Decisions): Linked presets are out of default scope, step.type is the preferred desired-state payload though internal nesting may remain possible, and tool should not be renamed to script or executable.

## Ordered Story Candidates

### STORY-001: Unify Step Type authoring controls

- Short name: `step-type-authoring`
- Source reference: `docs/Steps/StepTypes.md` (1. Purpose; 2. Desired-State Summary; 3. Terminology; 4. Core Invariants; 6.1 Step editor; 6.2 Step type picker; 10. Naming Policy)
- Why: This establishes the product-facing authoring model and terminology that every later contract depends on.
- Independent test: Create or edit a draft step in the authoring UI, switch among Tool, Skill, and Preset, and verify the visible fields, helper text, and data-loss behavior match the selected Step Type.
- Dependencies: None
- Needs clarification: None
- Scope:
  - Render a single Step Type selector with Tool, Skill, and Preset options.
  - Change type-specific configuration fields when Step Type changes.
  - Preserve compatible values and require clear discard or confirmation for incompatible values.
  - Use Step Type terminology consistently in ordinary authoring surfaces.
- Out of scope:
  - Implementing runtime execution for Tool or Skill steps.
  - Building preset expansion internals beyond surfacing the Preset type option.
- Acceptance criteria:
  - The step editor exposes exactly one user-facing Step Type control for Tool, Skill, and Preset.
  - Selecting each Step Type renders only the relevant type-specific configuration area.
  - Changing Step Type preserves compatible fields or clearly handles incompatible fields before loss.
  - Primary UI copy uses Step Type and avoids Capability, Activity, Invocation, Command, and Script as the step discriminator.
- Owned coverage:
  - `DESIGN-REQ-001`: Owns the canonical product-facing model and exact Step Type values.
  - `DESIGN-REQ-002`: Owns editor behavior and type-specific form switching.
  - `DESIGN-REQ-015`: Owns user-facing terminology in authoring surfaces.

### STORY-002: Validate Tool and Skill executable steps

- Short name: `executable-step-validation`
- Source reference: `docs/Steps/StepTypes.md` (5.1 `tool`; 5.2 `skill`; 8.1 Common validation; 8.2 Tool validation; 8.3 Skill validation; 9. Jira Example; 15. Non-Goals)
- Why: Tool and Skill steps are both executable, but they carry different safety, schema, authorization, and agent-boundary expectations.
- Independent test: Submit draft Tool and Skill steps through validation with valid examples, missing contract fields, mixed payloads, unauthorized tool inputs, and an arbitrary shell snippet, then confirm only valid governed steps pass.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Model Tool steps as typed operations with tool id, version, inputs, schemas, authorization, capabilities, retry policy, binding, validation, and error model.
  - Model Skill steps as agent-facing behavior with selector, instructions, context, runtime preferences, allowed tools, permissions, and approval or autonomy controls.
  - Reject invalid mixed-type executable steps and arbitrary shell snippets unless represented by an approved typed command tool.
  - Surface validation errors before submission.
- Out of scope:
  - Preset preview or expansion.
  - Changing the plan executor implementation.
- Acceptance criteria:
  - Tool validation requires an existing resolvable tool, schema-valid inputs, required authorization, worker capabilities, known side-effect policy, and no forbidden fields.
  - Skill validation requires a resolvable skill or documented auto semantics, contract-valid inputs, known runtime compatibility, required context, allowed permissions, and enforceable autonomy controls.
  - Common validation requires stable local identity, title or generated display label, Step Type, type-specific payload, and visible errors before submission.
  - Arbitrary shell snippets are rejected unless the selected Tool is an approved typed command tool with bounded inputs and policy.
  - Jira transition examples validate as Tool steps while Jira triage or implementation examples validate as Skill steps.
- Owned coverage:
  - `DESIGN-REQ-003`: Owns Tool contract fields and validation.
  - `DESIGN-REQ-004`: Owns the shell-script prohibition and typed command exception.
  - `DESIGN-REQ-005`: Owns Skill contract fields and validation.
  - `DESIGN-REQ-013`: Owns shared validation required for every step.
  - `DESIGN-REQ-017`: Owns Jira deterministic versus agentic example behavior for executable steps.

### STORY-003: Preview and apply Preset steps

- Short name: `preset-preview-apply`
- Source reference: `docs/Steps/StepTypes.md` (5.3 `preset`; 6.5 Preset picker; 6.6 Preset preview and apply; 8.4 Preset validation; 9. Jira Example; 12. Preset Management vs Preset Use; 16. Open Design Decisions)
- Why: Preset authoring must produce reusable workflow value without making presets hidden runtime work or a separate authoring experience.
- Independent test: Configure a Jira implementation flow Preset step, preview the generated mixed Tool and Skill steps, apply it, undo it, and verify the submitted draft contains no unresolved Preset step by default.
- Dependencies: STORY-001, STORY-002
- Needs clarification: Which preset comparison metadata is guaranteed to exist for older saved presets?
- Scope:
  - Allow Preset selection from the same step editor as Tool and Skill.
  - Validate preset existence, previewable version, input schema, deterministic expansion, generated-step validity, limits, and warnings.
  - Show preview of generated steps before apply.
  - Replace the temporary Preset step with generated Tool and Skill steps on apply.
  - Support undo expansion, origin display, provenance detach, comparison with source preset when possible, and explicit update to newer preset versions.
- Out of scope:
  - Managing the preset catalog outside of authoring.
  - Default linked-preset runtime execution.
  - Automatic refresh to newer preset versions.
- Acceptance criteria:
  - Preset use is available inside the step editor and there is no separate Presets section for choosing and applying a preset to the current task.
  - Preview lists the generated steps before apply and exposes expansion warnings.
  - Apply replaces the temporary Preset step with concrete Tool and/or Skill steps that pass their own validation.
  - Undo, show origin, detach provenance, compare, and explicit update actions are available where supported by source data.
  - Unresolved Preset steps cannot be submitted unless a future linked-preset mode is explicitly introduced and visibly different.
- Owned coverage:
  - `DESIGN-REQ-006`: Owns Preset as temporary authoring state and default no-unresolved-submission rule.
  - `DESIGN-REQ-007`: Owns deterministic expansion and generated-step validation.
  - `DESIGN-REQ-009`: Owns the separation between preset use and preset management.
  - `DESIGN-REQ-010`: Owns preview, apply, undo, origin, detach, compare, and explicit update controls.
  - `DESIGN-REQ-017`: Owns the Jira preset flow expansion example.
  - `DESIGN-REQ-019`: Owns linked-preset default exclusion for this story surface.

### STORY-004: Submit discriminated executable payloads

- Short name: `step-payload-runtime`
- Source reference: `docs/Steps/StepTypes.md` (7.1 Authoring payload; 7.2 Runtime plan mapping; 10.3 Keep `Activity` Temporal-specific; 11. API Shape; 15. Non-Goals; 16. Open Design Decisions)
- Why: A clear payload and runtime mapping keeps authoring, validation, plan compilation, and Temporal execution boundaries aligned.
- Independent test: Submit a draft containing Tool and Skill steps plus preset-derived provenance metadata, inspect the accepted API payload and generated runtime plan, and verify no Preset node or Activity Step Type appears by default.
- Dependencies: STORY-002, STORY-003
- Needs clarification: If implementation constraints require step.action.kind internally, which adapter boundary owns translation back to the desired step.type contract?
- Scope:
  - Represent steps as a discriminated union keyed by type with separate tool, skill, and preset sub-payloads.
  - Accept executable submission payloads that normally contain only ToolStep and SkillStep.
  - Preserve preset-derived source metadata on generated steps for audit, UI grouping, proposal reconstruction, and review.
  - Compile Tool and Skill steps into runtime plan nodes while keeping Preset out of runtime nodes by default.
  - Keep Temporal Activity terminology out of Step Type APIs and UI labels.
- Out of scope:
  - Replacing the plan executor.
  - Defining a default linked-preset runtime mode.
  - Renaming tool to script or executable.
- Acceptance criteria:
  - The API shape exposes StepType values tool, skill, and preset with distinct sub-payloads.
  - Executable submission normally accepts only ToolStep or SkillStep.
  - Preset-derived source metadata can be present but is not required for runtime correctness.
  - Runtime materialization maps Tool and Skill into plan nodes and does not materialize Preset as a runtime node by default.
  - Temporal Activity remains an implementation detail and is not used as a Step Type label.
- Owned coverage:
  - `DESIGN-REQ-008`: Owns preservation and non-runtime use of preset provenance metadata.
  - `DESIGN-REQ-011`: Owns runtime plan mapping and no Preset runtime node by default.
  - `DESIGN-REQ-012`: Owns discriminated API union and executable submission shape.
  - `DESIGN-REQ-016`: Owns Temporal Activity as implementation detail in payload and runtime terminology.
  - `DESIGN-REQ-019`: Owns preferred step.type shape and no tool rename in API-facing contracts.

### STORY-005: Promote proposals without live preset drift

- Short name: `proposal-promotion-guardrails`
- Source reference: `docs/Steps/StepTypes.md` (7.3 Backward compatibility; 13. Proposal and Promotion Semantics; 14. Migration Guidance; 15. Non-Goals; 16. Open Design Decisions)
- Why: Proposal promotion and migration are high-risk boundaries where hidden catalog drift or legacy ambiguity could change executable work after review.
- Independent test: Create a proposal from preset-derived steps, mutate the source preset catalog entry, promote the proposal, and verify promotion uses the reviewed flattened payload unless the user explicitly requests refresh with preview and validation.
- Dependencies: STORY-004
- Needs clarification: None
- Scope:
  - Store promotable proposal payloads as flattened executable Tool and Skill steps by default.
  - Preserve preset provenance as metadata when proposals originate from preset-derived work.
  - Validate the reviewed flat payload during promotion.
  - Prevent live preset lookup from being required for promotion correctness.
  - Require explicit preview and validation when refreshing a draft or proposal to a newer preset version.
  - Continue reading documented legacy shapes during migration while new authoring converges on Step Type.
- Out of scope:
  - Immediate removal of all legacy readers.
  - Automatic proposal refresh from catalog state.
  - Making presets hidden runtime work.
- Acceptance criteria:
  - Stored proposals contain executable Tool and Skill steps by default.
  - Preset provenance can be preserved without causing live catalog lookup during promotion.
  - Promotion validates the reviewed flat payload.
  - Refreshing from the preset catalog is an explicit action with preview and validation.
  - Legacy payload readers do not reintroduce ambiguous umbrella terminology into new UI or docs.
- Owned coverage:
  - `DESIGN-REQ-014`: Owns migration compatibility readers with forward convergence.
  - `DESIGN-REQ-018`: Owns proposal promotion semantics and no silent live re-expansion.
  - `DESIGN-REQ-019`: Owns bounded treatment of linked presets and step.type preference during promotion migration.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-002
- `DESIGN-REQ-004` -> STORY-002
- `DESIGN-REQ-005` -> STORY-002
- `DESIGN-REQ-006` -> STORY-003
- `DESIGN-REQ-007` -> STORY-003
- `DESIGN-REQ-008` -> STORY-004
- `DESIGN-REQ-009` -> STORY-003
- `DESIGN-REQ-010` -> STORY-003
- `DESIGN-REQ-011` -> STORY-004
- `DESIGN-REQ-012` -> STORY-004
- `DESIGN-REQ-013` -> STORY-002
- `DESIGN-REQ-014` -> STORY-005
- `DESIGN-REQ-015` -> STORY-001
- `DESIGN-REQ-016` -> STORY-004
- `DESIGN-REQ-017` -> STORY-002, STORY-003
- `DESIGN-REQ-018` -> STORY-005
- `DESIGN-REQ-019` -> STORY-003, STORY-004, STORY-005

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: STORY-002, STORY-003
- `STORY-005` depends on: STORY-004

## Out Of Scope Items

- Creating or modifying spec.md files during breakdown: Breakdown only produces story candidates; specify creates specs later.
- Creating directories under specs/: Feature directories are created only during specify.
- Implementing Step Type behavior: This run only decomposes the declarative design into future stories.
- Default linked-preset runtime execution: The design makes linked presets a future explicit mode, not default behavior.

## Coverage Gate

PASS - every major design point is owned by at least one story.
