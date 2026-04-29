# Story Breakdown: docs/Steps/StepTypes.md

- Source design: `docs/Steps/StepTypes.md`
- Original source reference path: `docs/Steps/StepTypes.md`
- Story extraction date: `2026-04-29T07:26:29Z`
- Requested output mode: `jira`

## Design Summary

The design defines MoonMind Step Type as the user-facing discriminator for task steps, with canonical values Tool, Skill, and Preset. Tool and Skill are executable authoring paths for deterministic typed operations and agentic work respectively, while Preset is normally an authoring-time placeholder that expands deterministically into executable steps before submission. The contract spans UI controls, validation, explicit API shapes, runtime plan compilation, proposal promotion, migration boundaries, naming policy, and non-goals that keep arbitrary shell, hidden preset work, and Temporal Activity terminology out of ordinary user authoring.

## Coverage Points

### DESIGN-REQ-001: Expose a single Step Type authoring discriminator

- Type: `requirement`
- Source section: 1. Purpose; 2. Desired-State Summary; 4. Core Invariants
- Explanation: Each authored step has exactly one user-facing Step Type with canonical values tool, skill, and preset, and changing the type controls the form, validation, and materialization path.

### DESIGN-REQ-002: Use consistent product terminology

- Type: `constraint`
- Source section: 3. Terminology; 10. Naming Policy
- Explanation: UI, API, docs, validation, proposal promotion, and preset expansion must use Step Type, Tool, Skill, and Preset consistently, while avoiding Capability, Activity, Command, Script, or Invocation as the primary user-facing discriminator.

### DESIGN-REQ-003: Define governed Tool steps

- Type: `requirement`
- Source section: 5.1 `tool`; 8.2 Tool validation; 9. Jira Example
- Explanation: Tool steps represent typed, schema-backed, policy-checked, deterministic operations with declared contracts, required authorization, worker capabilities, retry policy, binding, validation, and error model.

### DESIGN-REQ-004: Reject arbitrary shell as a Step Type

- Type: `security`
- Source section: 4. Core Invariants; 8.2 Tool validation; 10.1 Keep `Tool`; 15. Non-Goals
- Explanation: Arbitrary scripts or shell snippets must not become a first-class Step Type, except through an explicitly approved typed command tool with bounded inputs and policy.

### DESIGN-REQ-005: Define agentic Skill steps

- Type: `requirement`
- Source section: 5.2 `skill`; 8.3 Skill validation; 9. Jira Example
- Explanation: Skill steps represent agent-facing reusable behavior for interpretation, planning, implementation, synthesis, and open-ended work, with selectors, instructions, context, runtime preferences, tool permissions, and autonomy controls.

### DESIGN-REQ-006: Define Preset authoring placeholders

- Type: `state-model`
- Source section: 5.3 `preset`; 6.5 Preset picker; 6.6 Preset preview and apply; 8.4 Preset validation
- Explanation: Preset steps are reusable parameterized authoring templates for known multi-step workflows, selectable from the step editor, previewable, and applicable into concrete steps.

### DESIGN-REQ-007: Expand presets deterministically before execution

- Type: `integration`
- Source section: 2. Desired-State Summary; 4. Core Invariants; 7.1 Authoring payload; 7.2 Runtime plan mapping; 8.4 Preset validation
- Explanation: Preset expansion must be deterministic, validated before execution, produce executable Tool and/or Skill steps, and normally leave no unresolved preset invocations in durable execution payloads.

### DESIGN-REQ-008: Preserve preset provenance without runtime dependence

- Type: `artifact`
- Source section: 5.3 `preset`; 6.6 Preset preview and apply; 7.1 Authoring payload; 13. Proposal and Promotion Semantics
- Explanation: Expanded preset-derived steps preserve source metadata for audit, UI grouping, reconstruction, review, and proposals, but provenance must not be required for runtime correctness or hidden work.

### DESIGN-REQ-009: Render Step Type-specific editor experiences

- Type: `requirement`
- Source section: 6. User Experience Contract
- Explanation: The step editor must present a Step Type picker with helper text, type-specific controls, searchable/grouped Tool and Skill pickers, a Preset picker in the same authoring surface, and guarded behavior when changing type discards data.

### DESIGN-REQ-010: Separate preset management from preset use

- Type: `constraint`
- Source section: 6.5 Preset picker; 12. Preset Management vs Preset Use
- Explanation: Preset management belongs in the Presets section while using a preset belongs inside step authoring; there should not be a separate Presets section for applying a preset to the current task.

### DESIGN-REQ-011: Support preview, apply, undo, detach, compare, and explicit update controls for presets

- Type: `requirement`
- Source section: 6.6 Preset preview and apply
- Explanation: Users should see generated steps before application, apply the preset into ordinary editable steps, undo expansion, inspect origin, detach provenance, compare against the source preset, and update to newer versions only explicitly.

### DESIGN-REQ-012: Normalize explicit discriminated API shapes

- Type: `integration`
- Source section: 7. Runtime and Payload Contract; 11. API Shape
- Explanation: Drafts and APIs should use explicit discriminated shapes for ToolStep, SkillStep, and PresetStep, while executable submissions normally accept only ToolStep or SkillStep.

### DESIGN-REQ-013: Compile executable steps into runtime plan nodes

- Type: `integration`
- Source section: 7.2 Runtime plan mapping
- Explanation: Executable Tool and Skill steps compile into canonical runtime plan materialization, with Temporal activities, child workflows, or managed sessions hidden behind implementation boundaries.

### DESIGN-REQ-014: Maintain bounded legacy read compatibility during migration

- Type: `migration`
- Source section: 7.3 Backward compatibility; 14. Migration Guidance; 15. Non-Goals
- Explanation: Migration may keep readers for legacy shapes such as step.skill, step.tool, skillId, template metadata, and existing tool.type values, but new authoring surfaces must converge on the Step Type model and must not reintroduce ambiguous terminology.

### DESIGN-REQ-015: Validate common and type-specific step requirements before submission

- Type: `requirement`
- Source section: 8. Validation Rules
- Explanation: Every step must have stable identity, display label, Step Type, type-specific payload, and surfaced validation errors; Tool, Skill, and Preset each add their own existence, schema, authorization, compatibility, policy, and expansion checks.

### DESIGN-REQ-016: Preserve executable proposal promotion semantics

- Type: `state-model`
- Source section: 13. Proposal and Promotion Semantics
- Explanation: Stored promotable task payloads should already be flattened and executable, promotion validates reviewed flat payloads, and refresh from a preset catalog must be explicit with preview and validation.

### DESIGN-REQ-017: Constrain linked presets to explicit future mode

- Type: `non-goal`
- Source section: 16. Open Design Decisions / Q1
- Explanation: Ordinary preset application should not create linked runtime work; any future linked-preset mode must be visibly different and define separate rules for pinning, drift, refresh, validation, and audit.

### DESIGN-REQ-018: Keep Temporal Activity terminology implementation-only

- Type: `constraint`
- Source section: 3. Terminology; 10.3 Keep `Activity` Temporal-specific; 15. Non-Goals
- Explanation: Activity remains a Temporal implementation concept and must not be exposed as the Step Type label or treated as a replacement for the Step Type model.

### DESIGN-REQ-019: Support phased migration without making migration the canonical story

- Type: `migration`
- Source section: 14. Migration Guidance
- Explanation: Implementation may proceed through UI terminology, draft model normalization, preset expansion normalization, and runtime contract convergence while preserving desired-state documentation semantics.

## Ordered Story Candidates

### STORY-001: Present Step Type authoring in the task step editor

- Short name: `step-type-editor`
- Source reference: `docs/Steps/StepTypes.md`
- Source sections: 1. Purpose, 2. Desired-State Summary, 3. Terminology, 4. Core Invariants, 6. User Experience Contract, 10. Naming Policy
- Description: As a task author, I can choose Tool, Skill, or Preset from a single Step Type control so the editor shows the right fields without requiring internal runtime vocabulary.
- Why: This establishes the product-facing discriminator and removes ambiguous capability/activity/script language from ordinary task authoring.
- Independent test: A UI test creates or edits a step, switches among Tool, Skill, and Preset, verifies the displayed form and helper copy, verifies data-loss confirmation behavior, and checks that Activity/Capability/Script are not used as the primary selector label.
- Dependencies: None
- Needs clarification: None

Acceptance criteria:

- The step editor exposes exactly one user-facing Step Type selector for ordinary step authoring.
- The selector offers Tool, Skill, and Preset using the documented helper text or equivalent concise copy.
- Changing Step Type changes the type-specific controls below the selector.
- Meaningful incompatible data is not silently lost when the user changes Step Type.
- Temporal Activity and capability terminology remain absent from the primary user-facing Step Type selector.

Requirements:

- Every authored step in the editor has one selected Step Type.
- The selected Step Type controls available sub-options and validation surface.
- UI copy consistently uses Step Type, Tool, Skill, and Preset for the authoring model.

Owned source design coverage:

- `DESIGN-REQ-001`: Owns the single Step Type authoring discriminator and canonical values.
- `DESIGN-REQ-002`: Owns product terminology in the authoring UI.
- `DESIGN-REQ-009`: Owns the Step Type picker and type-specific editor experience.
- `DESIGN-REQ-018`: Ensures Activity remains implementation terminology rather than a UI Step Type label.

### STORY-002: Author and validate governed Tool steps

- Short name: `tool-step-contract`
- Source reference: `docs/Steps/StepTypes.md`
- Source sections: 5.1 `tool`, 6.3 Tool picker, 8.2 Tool validation, 9. Jira Example, 10.1 Keep `Tool`, 15. Non-Goals
- Description: As a task author, I can configure a Tool step as a typed governed operation with schema-backed inputs and policy validation, while arbitrary shell remains excluded from Step Type authoring.
- Why: Tool steps must stay deterministic, bounded, and safe so users can run direct integrations without turning the step editor into an ad hoc command surface.
- Independent test: API and UI validation tests configure a Jira transition Tool step, verify schema and authorization errors are surfaced, and verify an arbitrary shell payload is rejected unless an approved typed command tool contract is selected.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:

- A valid Tool step can be authored with tool id, version or resolvable version, and schema-valid inputs.
- Invalid Tool steps fail before submission with actionable validation errors.
- Tool forms are driven by the selected tool contract rather than free-form script fields.
- Arbitrary shell snippets cannot be submitted as a Step Type.
- Tool terminology remains the user-facing label for typed executable operations.

Requirements:

- Tool definitions declare name/version, schemas, authorization, worker capabilities, retry policy, binding, validation, and error model.
- Tool validation rejects missing tools, invalid inputs, missing authorization, unavailable capabilities, forbidden fields, and unknown retry or side-effect policy.
- Tool steps represent deterministic bounded work such as Jira transitions or GitHub reviewer requests.

Owned source design coverage:

- `DESIGN-REQ-003`: Owns governed Tool step authoring and contract validation.
- `DESIGN-REQ-004`: Owns rejection of arbitrary shell as a first-class Step Type.
- `DESIGN-REQ-015`: Owns common and Tool-specific validation requirements.

### STORY-003: Author and validate agentic Skill steps

- Short name: `skill-step-contract`
- Source reference: `docs/Steps/StepTypes.md`
- Source sections: 5.2 `skill`, 6.4 Skill picker, 8.3 Skill validation, 9. Jira Example
- Description: As a task author, I can configure a Skill step for agentic work with a skill selector, instructions, context, runtime preferences, permissions, and autonomy controls validated before execution.
- Why: Skill steps separate interpretation and implementation work from deterministic Tool operations while preserving a clear user-facing agentic boundary.
- Independent test: A UI/API test authors an implementation Skill step, verifies required instructions and context validation, checks compatibility hints, and confirms the submitted payload carries a Skill sub-payload rather than a Tool operation.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:

- A Skill step can be authored with a selected skill and validated inputs.
- Missing required instructions, context, permissions, or runtime compatibility blocks submission.
- Skill configuration visibly communicates that the work is agentic.
- Skill steps may reference allowed tools internally without being represented as Tool steps.

Requirements:

- Skill steps are used for interpretation, planning, implementation, synthesis, and other open-ended reasoning.
- Skill validation covers existence or auto resolution, contract inputs, runtime compatibility, required context, permissions, and autonomy controls.

Owned source design coverage:

- `DESIGN-REQ-005`: Owns Skill step authoring and the agentic boundary.
- `DESIGN-REQ-015`: Owns common and Skill-specific validation requirements.

### STORY-004: Preview and apply Preset steps into executable steps

- Short name: `preset-expansion`
- Source reference: `docs/Steps/StepTypes.md`
- Source sections: 5.3 `preset`, 6.5 Preset picker, 6.6 Preset preview and apply, 7.1 Authoring payload, 7.2 Runtime plan mapping, 8.4 Preset validation, 12. Preset Management vs Preset Use, 16. Open Design Decisions / Q1
- Description: As a task author, I can choose a Preset from the step editor, configure its inputs, preview deterministic expansion, and apply it into editable executable Tool and Skill steps.
- Why: Preset use should be an authoring convenience that produces concrete executable work, not hidden runtime behavior or a separate application flow.
- Independent test: A UI/API integration test selects a Jira implementation preset, enters inputs, previews the generated steps, applies the preset, verifies the draft now contains only Tool/Skill steps with valid payloads, verifies undo/detach/update controls, and confirms unresolved Preset submission is rejected.
- Dependencies: STORY-001, STORY-002, STORY-003
- Needs clarification: None

Acceptance criteria:

- Preset use is available from the step editor, not only from the Presets management area.
- Preset preview lists the generated steps before application.
- Applying a preset replaces the Preset placeholder with editable Tool and Skill steps.
- Generated steps validate under their own Tool or Skill rules before executable submission.
- Submission rejects unresolved Preset steps by default.
- Updating to a newer preset version is explicit and previewed.

Requirements:

- Preset steps are authoring-time placeholders by default.
- Preset expansion is deterministic and validated before execution.
- Preset management and preset use remain separate experiences.
- Future linked presets are not part of ordinary preset application unless explicitly introduced with separate semantics.

Owned source design coverage:

- `DESIGN-REQ-006`: Owns Preset authoring placeholders and same-surface selection.
- `DESIGN-REQ-007`: Owns deterministic pre-execution expansion into executable steps.
- `DESIGN-REQ-010`: Owns separation between preset management and preset use.
- `DESIGN-REQ-011`: Owns preview, apply, undo, origin, detach, compare, and explicit update controls.
- `DESIGN-REQ-017`: Owns default exclusion of linked-preset runtime behavior.

### STORY-005: Normalize Step Type API and executable submission payloads

- Short name: `step-type-api`
- Source reference: `docs/Steps/StepTypes.md`
- Source sections: 7. Runtime and Payload Contract, 8. Validation Rules, 11. API Shape, 14. Migration Guidance
- Description: As an API consumer, I can submit explicit discriminated Step Type payloads where drafts may contain Preset steps but executable submissions normally contain only Tool and Skill steps.
- Why: A typed payload contract gives the UI, validation, proposal promotion, and runtime compiler one consistent representation while preserving controlled migration reads.
- Independent test: API contract tests post valid ToolStep, SkillStep, and draft PresetStep payloads; verify executable submission rejects unresolved Preset and mixed-type payloads; and verify legacy readable shapes normalize without being emitted by new authoring endpoints.
- Dependencies: STORY-002, STORY-003, STORY-004
- Needs clarification: None

Acceptance criteria:

- Draft APIs can represent ToolStep, SkillStep, and PresetStep as explicit discriminated shapes.
- Executable submission normally accepts only ToolStep and SkillStep.
- Invalid mixed payloads fail fast with validation errors.
- Legacy shapes remain readable only where migration requires them.
- New API outputs and docs converge on Step Type terminology and shapes.

Requirements:

- Step payloads include stable local identity, optional/generated title, type discriminator, and matching type-specific payload.
- Compatibility readers do not reintroduce ambiguous UI or docs terminology.
- Migration can proceed in phases while preserving desired-state API direction.

Owned source design coverage:

- `DESIGN-REQ-012`: Owns discriminated API shapes and executable submission type constraints.
- `DESIGN-REQ-014`: Owns bounded legacy read compatibility during migration.
- `DESIGN-REQ-015`: Owns common validation and invalid mixed-step rejection.
- `DESIGN-REQ-019`: Owns phased migration support for draft and API normalization.

### STORY-006: Compile Step Type payloads into runtime plans and promotable proposals

- Short name: `runtime-promotion`
- Source reference: `docs/Steps/StepTypes.md`
- Source sections: 7.1 Authoring payload, 7.2 Runtime plan mapping, 13. Proposal and Promotion Semantics, 14. Migration Guidance, 15. Non-Goals
- Description: As an operator, I can trust executable Step Type payloads to compile into runtime plan nodes and proposals without live preset lookup, hidden preset work, or user-facing Temporal terminology.
- Why: Runtime convergence and proposal promotion are the operational boundary where authoring-time Step Types must become durable, executable, auditable work.
- Independent test: Workflow or service-boundary tests compile a draft with preset-derived Tool/Skill steps into a runtime plan, verify provenance metadata is retained but not required for execution, promote a proposal without live preset lookup, and verify explicit refresh requires preview and validation.
- Dependencies: STORY-005
- Needs clarification: None

Acceptance criteria:

- Executable Tool and Skill steps compile into canonical runtime plan materialization.
- Preset provenance is retained as audit metadata but runtime execution succeeds from the flat executable payload.
- Stored promotable proposals are executable by default and do not silently re-expand live presets.
- Promotion validates the reviewed flat payload.
- Refreshing from a preset catalog is an explicit user action with preview and validation.
- Activity remains an implementation detail in runtime code and docs, not a user-facing Step Type.

Requirements:

- Tool and Skill step runtime translations are implementation concerns hidden behind Step Type authoring.
- Preset-derived metadata supports audit and reconstruction but does not affect correctness.
- Runtime contract convergence aligns proposal promotion, task editing, and execution reconstruction with Step Type semantics.

Owned source design coverage:

- `DESIGN-REQ-008`: Owns preset provenance as metadata rather than runtime dependency.
- `DESIGN-REQ-013`: Owns runtime plan compilation of executable steps.
- `DESIGN-REQ-016`: Owns executable proposal promotion semantics.
- `DESIGN-REQ-018`: Owns Temporal Activity terminology confinement at runtime boundaries.
- `DESIGN-REQ-019`: Owns final runtime contract convergence phase.

## Coverage Matrix

- `DESIGN-REQ-001` -> `STORY-001`
- `DESIGN-REQ-002` -> `STORY-001`
- `DESIGN-REQ-003` -> `STORY-002`
- `DESIGN-REQ-004` -> `STORY-002`
- `DESIGN-REQ-005` -> `STORY-003`
- `DESIGN-REQ-006` -> `STORY-004`
- `DESIGN-REQ-007` -> `STORY-004`
- `DESIGN-REQ-008` -> `STORY-006`
- `DESIGN-REQ-009` -> `STORY-001`
- `DESIGN-REQ-010` -> `STORY-004`
- `DESIGN-REQ-011` -> `STORY-004`
- `DESIGN-REQ-012` -> `STORY-005`
- `DESIGN-REQ-013` -> `STORY-006`
- `DESIGN-REQ-014` -> `STORY-005`
- `DESIGN-REQ-015` -> `STORY-002`, `STORY-003`, `STORY-005`
- `DESIGN-REQ-016` -> `STORY-006`
- `DESIGN-REQ-017` -> `STORY-004`
- `DESIGN-REQ-018` -> `STORY-001`, `STORY-006`
- `DESIGN-REQ-019` -> `STORY-005`, `STORY-006`

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: `STORY-001`
- `STORY-003` depends on: `STORY-001`
- `STORY-004` depends on: `STORY-001`, `STORY-002`, `STORY-003`
- `STORY-005` depends on: `STORY-002`, `STORY-003`, `STORY-004`
- `STORY-006` depends on: `STORY-005`

## Out Of Scope

- Creating or modifying Moon Spec spec.md files during breakdown.
- Creating directories under specs/ during breakdown.
- Implementing a future linked-preset runtime mode.
- Replacing the plan executor or redefining Temporal Activity semantics.
- Introducing arbitrary shell scripts as a first-class Step Type.
- Removing all legacy compatibility readers immediately.

## Coverage Gate

PASS - every major design point is owned by at least one story.
