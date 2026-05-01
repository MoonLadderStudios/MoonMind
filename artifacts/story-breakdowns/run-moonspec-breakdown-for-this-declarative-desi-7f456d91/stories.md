# Step Types Story Breakdown

Source design: `docs/Steps/StepTypes.md`
Original source document reference path: `docs/Steps/StepTypes.md`
Story extraction date: 2026-05-01T00:53:27Z
Requested output mode: jira

## Design Summary

The design defines MoonMind's desired-state Step Type model for task authoring. It establishes Tool, Skill, and Preset as the canonical user-facing step types, separates deterministic typed operations from agentic skill behavior, treats presets as authoring-time templates that expand into executable steps, and keeps Temporal Activity and worker capability concepts out of ordinary task authoring. It also defines validation, API shape, runtime plan mapping, provenance, proposal promotion, migration guardrails, non-goals, and open design decisions.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **Canonical Step Types** — MoonMind tasks are composed from steps with exactly one user-facing Step Type, and the canonical values are tool, skill, and preset. Source: 1. Purpose.
- `DESIGN-REQ-002` (requirement): **Step Type Controls Authoring** — The selected Step Type determines what the step represents, displayed fields, validation, expansion behavior, and runtime mapping. Source: 1. Purpose; 2. Desired-State Summary.
- `DESIGN-REQ-003` (constraint): **Product Terminology Boundary** — Users should see Step Type, Tool, Skill, and Preset rather than internal terms such as capability, activity, invocation, command, or script. Source: 1. Purpose; 3. Terminology; 10. Naming Policy.
- `DESIGN-REQ-004` (state-model): **Executable And Placeholder Semantics** — Tool and Skill steps are executable; Preset steps are authoring-time placeholders by default and expand before execution. Source: 2. Desired-State Summary; 4. Core Invariants.
- `DESIGN-REQ-005` (requirement): **Terminology Definitions** — Task, Step, Step Type, Tool, Skill, Preset, Expansion, Plan, and Activity have explicit desired meanings, with capability reserved for worker/security contexts. Source: 3. Terminology.
- `DESIGN-REQ-006` (constraint): **Core Invariants** — Every authored step has one Step Type, type controls sub-options, preset expansion is deterministic and validated, runtime does not require live preset lookup, and terminology stays consistent across surfaces. Source: 4. Core Invariants.
- `DESIGN-REQ-007` (integration): **Tool Step Contract** — Tool steps run explicit typed operations with declared name/version, schemas, authorization, worker capabilities, retry policy, execution binding, validation, and error model. Source: 5.1 tool.
- `DESIGN-REQ-008` (requirement): **Tool Step User Experience** — Tool steps are presented as direct deterministic work, with searchable/grouped tool selection, schema-driven forms, and dynamic option providers. Source: 5.1 tool; 6.3 Tool picker.
- `DESIGN-REQ-009` (integration): **Skill Step Contract** — Skill steps invoke reusable agent-facing behavior for interpretation, planning, implementation, synthesis, and other open-ended reasoning. Source: 5.2 skill.
- `DESIGN-REQ-010` (requirement): **Skill Step User Experience** — Skill steps expose selector, instructions, repository/project context, runtime/model preferences, allowed tools, approvals, descriptions, and compatibility hints. Source: 5.2 skill; 6.4 Skill picker.
- `DESIGN-REQ-011` (artifact): **Preset Step Contract** — Preset steps select reusable parameterized templates and configure inputs before deterministic expansion into Tool and/or Skill steps. Source: 5.3 preset.
- `DESIGN-REQ-012` (requirement): **Preset Preview And Apply** — Preset use belongs in the step editor, with preview, apply, undo expansion, origin display, detach, compare, and explicit update-to-new-version behavior. Source: 6.5 Preset picker; 6.6 Preset preview and apply.
- `DESIGN-REQ-013` (constraint): **Preset Management Boundary** — The Presets section is for management only; choosing and applying a preset belongs inside step authoring. Source: 6.5 Preset picker; 12. Preset Management vs Preset Use.
- `DESIGN-REQ-014` (requirement): **Step Editor Change Behavior** — Changing Step Type changes the form and must preserve compatible fields, clearly discard incompatible fields, or require confirmation when meaningful data would be lost. Source: 6.1 Step editor; 6.2 Step type picker.
- `DESIGN-REQ-015` (state-model): **Authoring Payload Shape** — Drafts may contain preset steps temporarily, while executable task submissions should normally contain only Tool and Skill steps. Source: 7.1 Authoring payload.
- `DESIGN-REQ-016` (artifact): **Preset Provenance Metadata** — Expanded preset-derived steps preserve metadata such as preset ID/version/include path/original step ID for audit, grouping, reconstruction, and review, not runtime correctness. Source: 5.3 preset; 7.1 Authoring payload.
- `DESIGN-REQ-017` (integration): **Runtime Plan Mapping** — Executable steps compile into runtime plan nodes; Tool steps invoke typed tools, Skill steps invoke agent-facing skill behavior, and Preset has no runtime node by default. Source: 7.2 Runtime plan mapping.
- `DESIGN-REQ-018` (migration): **Legacy Read Compatibility** — During migration MoonMind may read legacy shapes, but new authoring surfaces normalize into the desired Step Type model and do not revive ambiguous terminology. Source: 7.3 Backward compatibility.
- `DESIGN-REQ-019` (requirement): **Common And Type-Specific Validation** — All steps require identity, display label, Step Type, type payload, and pre-submission errors; Tool, Skill, and Preset each have their own validity rules. Source: 8. Validation Rules.
- `DESIGN-REQ-020` (security): **Arbitrary Shell Exclusion** — Arbitrary shell snippets are not a Step Type and must be rejected unless represented by an approved typed command tool with bounded inputs and policy. Source: 4. Core Invariants; 8.2 Tool validation; 15. Non-Goals.
- `DESIGN-REQ-021` (integration): **Jira Step Type Example** — Jira deterministic operations should be Tool steps, Jira triage/implementation should be Skill steps, and reusable Jira workflows should be Presets that expand into a mixed executable sequence. Source: 9. Jira Example.
- `DESIGN-REQ-022` (integration): **Discriminated API Shape** — The desired API uses step.type discriminated unions for ToolStep, SkillStep, PresetStep, and an executable submission type of ToolStep or SkillStep. Source: 11. API Shape.
- `DESIGN-REQ-023` (artifact): **Proposal Promotion Semantics** — Stored proposals should preserve executable intent as flattened Tool/Skill payloads, may carry preset provenance, and must not silently re-expand live preset catalog entries. Source: 13. Proposal and Promotion Semantics.
- `DESIGN-REQ-024` (migration): **Migration Phases** — Migration may proceed through UI terminology, draft model normalization, preset expansion normalization, and runtime contract convergence. Source: 14. Migration Guidance.
- `DESIGN-REQ-025` (non-goal): **Explicit Non-Goals** — The design excludes redefining Temporal Activity semantics, replacing the plan executor, making presets hidden runtime work, first-class arbitrary shell scripts, immediate legacy-reader removal, worker-placement complexity for users, and collapsing Tool and Skill together. Source: 15. Non-Goals.
- `DESIGN-REQ-026` (constraint): **Open Decisions Guardrails** — Linked presets are out by default unless explicit; Step Type remains the UI term; step.type is preferred; tool should not be renamed to script or executable. Source: 16. Open Design Decisions.

## Ordered Story Candidates

### STORY-001: Define the Step Type authoring model

- Jira issue type: Story
- Short name: `step-type-authoring`
- Source reference: `docs/Steps/StepTypes.md` sections: 1. Purpose, 2. Desired-State Summary, 3. Terminology, 4. Core Invariants, 6.1 Step editor, 6.2 Step type picker, 10. Naming Policy
- Why: This establishes the user-facing discriminator and prevents internal runtime concepts from leaking into ordinary task authoring.
- Description: As a task author, I can choose exactly one Step Type for each step so the editor shows the right fields and uses consistent product terminology.
- Independent test: Create or edit draft steps of each Step Type and assert the UI/API draft state has one discriminator, renders the matching sub-form, and avoids banned umbrella terms in authoring copy.
- Dependencies: None
- Scope:
  - Expose Step Type with Tool, Skill, and Preset choices in the step editor.
  - Render type-specific configuration beneath the selected Step Type.
  - Apply terminology rules across UI copy and validation messages.
  - Handle Step Type changes by preserving compatible fields, clearly discarding incompatible ones, or requiring confirmation when meaningful data would be lost.
- Out of scope:
  - Implementing every concrete Tool, Skill, or Preset catalog item.
  - Changing Temporal Activity semantics.
- Acceptance criteria:
  - Every authored step has exactly one Step Type with canonical values tool, skill, or preset.
  - Changing Step Type updates the visible configuration controls and handles incompatible data explicitly.
  - The product-facing label is Step Type, not capability, activity, invocation, command, or script.
  - Task, Step, Tool, Skill, Preset, Expansion, Plan, and Activity terminology follows the design definitions.
- Owned coverage:
  - `DESIGN-REQ-001`: Owns the canonical Step Type set.
  - `DESIGN-REQ-002`: Owns form switching and authoring semantics.
  - `DESIGN-REQ-003`: Owns user-facing terminology.
  - `DESIGN-REQ-005`: Owns terminology definitions in user-facing surfaces.
  - `DESIGN-REQ-006`: Owns the one-Step-Type invariant and consistency rules.
  - `DESIGN-REQ-014`: Owns type-change behavior in the editor.

### STORY-002: Author governed Tool steps

- Jira issue type: Story
- Short name: `tool-step-contract`
- Source reference: `docs/Steps/StepTypes.md` sections: 5.1 tool, 6.3 Tool picker, 8.2 Tool validation, 10.1 Keep Tool, 15. Non-Goals
- Why: Tool steps are the deterministic execution path and must stay governed rather than becoming ad hoc shell execution.
- Description: As a task author, I can configure a Tool step as a typed governed operation so deterministic integrations run with schema, authorization, capability, retry, and error contracts.
- Independent test: Author representative Tool steps, including a Jira transition and an invalid arbitrary shell snippet, then verify schema validation, capability/auth errors, and rejection behavior before submission.
- Dependencies: STORY-001
- Scope:
  - Provide Tool selection by integration/domain with search support.
  - Render schema-driven inputs and dynamic option providers for supported tools.
  - Validate tool existence/version, inputs, authorization, worker capabilities, forbidden fields, retry policy, side-effect policy, and error model before submission.
  - Reject arbitrary shell snippets unless represented by an approved typed command tool.
- Out of scope:
  - Agentic interpretation or implementation work, which belongs to Skill steps.
  - Creating a first-class Script Step Type.
- Acceptance criteria:
  - Tool steps require a typed tool id, resolvable or pinned version, and schema-valid inputs.
  - The Tool picker supports integration/domain grouping and search.
  - Dynamic option providers can populate fields such as Jira target statuses.
  - Arbitrary shell input is rejected unless it is an approved typed command tool with bounded inputs and policy.
- Owned coverage:
  - `DESIGN-REQ-007`: Owns the typed Tool contract.
  - `DESIGN-REQ-008`: Owns Tool picker and form behavior.
  - `DESIGN-REQ-019`: Owns Tool-specific validation rules.
  - `DESIGN-REQ-020`: Owns shell-snippet exclusion for Tool authoring.

### STORY-003: Author agentic Skill steps

- Jira issue type: Story
- Short name: `skill-step-contract`
- Source reference: `docs/Steps/StepTypes.md` sections: 5.2 skill, 6.4 Skill picker, 8.3 Skill validation
- Why: Skill steps keep open-ended agent work distinct from deterministic Tool operations while preserving enough structure for validation and runtime routing.
- Description: As a task author, I can configure a Skill step for agentic work so interpretation, implementation, planning, and synthesis use reusable skill behavior with clear runtime boundaries.
- Independent test: Author valid and invalid Skill steps and verify the form, payload, compatibility hints, missing-context errors, and approval/autonomy validation independently of Tool and Preset stories.
- Dependencies: STORY-001
- Scope:
  - Provide Skill selection with search, descriptions, and compatibility hints.
  - Expose instructions, repository/project context, runtime/model preferences, allowed tools/capabilities, and approval/autonomy controls where applicable.
  - Validate skill existence or documented auto resolution, inputs, runtime compatibility, required context, allowed tools/permissions, and approval/autonomy constraints.
- Out of scope:
  - Executing deterministic integration actions as Skill steps when a typed Tool is available.
  - Changing agent skill snapshot resolution rules outside the Step Type payload contract.
- Acceptance criteria:
  - Skill steps clearly communicate the agentic boundary to users.
  - Skill configuration includes selector, instructions, context, runtime/model preferences, permissions, and approvals when supported.
  - Invalid or unresolved skill selections fail before submission with actionable validation errors.
  - Users can distinguish deterministic Tool work from agentic Skill work in authoring and review.
- Owned coverage:
  - `DESIGN-REQ-009`: Owns the agent-facing Skill contract.
  - `DESIGN-REQ-010`: Owns Skill picker and configuration fields.
  - `DESIGN-REQ-019`: Owns Skill-specific validation rules.

### STORY-004: Preview and apply Preset steps

- Jira issue type: Story
- Short name: `preset-preview-apply`
- Source reference: `docs/Steps/StepTypes.md` sections: 5.3 preset, 6.5 Preset picker, 6.6 Preset preview and apply, 8.4 Preset validation, 12. Preset Management vs Preset Use
- Why: Preset use should be a normal step-authoring workflow, while preset catalog management remains a separate management experience.
- Description: As a task author, I can select a Preset inside the step editor, configure inputs, preview generated steps, and apply the preset into ordinary executable steps.
- Independent test: Use a sample preset to preview, apply, undo, and reapply expansion, asserting that generated steps validate and that preset management actions remain outside the authoring flow.
- Dependencies: STORY-001, STORY-002, STORY-003
- [NEEDS CLARIFICATION] Which existing preset fixture should be treated as the canonical smoke-test preset if multiple candidates exist?
- Scope:
  - Offer Preset as a Step Type in the same editor as Tool and Skill.
  - Select active or previewable preset versions and configure preset inputs.
  - Preview the expanded step list before application.
  - Apply a preset by replacing the temporary Preset step with generated Tool and/or Skill steps.
  - Support undo expansion, origin display, detach from provenance, compare with source preset, and explicit update-to-new-version behavior where possible.
- Out of scope:
  - Creating or editing preset catalog definitions from the task authoring screen.
  - Submitting unresolved preset steps for ordinary execution.
- Acceptance criteria:
  - Preset use lives in the step editor, not a separate Presets section.
  - Preset preview lists generated steps before application.
  - Applying a preset replaces the temporary Preset step with concrete Tool and/or Skill steps.
  - Preset validation covers existence, version, input schema, deterministic expansion, generated-step validity, step limits, policy limits, and visible warnings.
- Owned coverage:
  - `DESIGN-REQ-004`: Owns Preset placeholder behavior from the authoring perspective.
  - `DESIGN-REQ-011`: Owns Preset input and template contract.
  - `DESIGN-REQ-012`: Owns preview/apply/undo/origin/update interactions.
  - `DESIGN-REQ-013`: Owns the management-versus-use boundary.
  - `DESIGN-REQ-019`: Owns Preset-specific validation rules.

### STORY-005: Submit flattened executable steps with provenance

- Jira issue type: Story
- Short name: `flattened-step-submission`
- Source reference: `docs/Steps/StepTypes.md` sections: 5.3 preset, 7.1 Authoring payload, 13. Proposal and Promotion Semantics
- Why: Flattened submission makes execution deterministic and reviewable, while provenance keeps preset-derived work explainable without making live catalog state part of runtime correctness.
- Description: As an operator, I can submit tasks that contain only executable Tool and Skill steps by default, while preset-derived steps retain provenance for audit and reconstruction without runtime lookup.
- Independent test: Submit a preset-derived draft and verify the execution payload and promotable proposal payload contain flattened Tool/Skill steps with provenance, and that promotion does not perform live preset lookup.
- Dependencies: STORY-004
- Scope:
  - Allow draft authoring to temporarily contain Preset steps.
  - Require ordinary executable submissions and stored promotable task payloads to contain flattened Tool and Skill steps.
  - Preserve preset provenance metadata on expanded steps.
  - Ensure promotion validates reviewed flat payloads and never silently re-expands from the live preset catalog.
  - Make refresh to a newer preset version an explicit previewed action.
- Out of scope:
  - Future linked-preset execution mode.
  - Making preset provenance required for runtime correctness.
- Acceptance criteria:
  - Executable submission contains only Tool and Skill steps by default.
  - Preset-derived steps carry source.kind, presetId, presetVersion, includePath when applicable, and originalStepId metadata.
  - Runtime correctness does not depend on preset provenance metadata or live catalog lookup.
  - Promotion validates the stored flat payload and only refreshes from catalog after explicit user action with preview.
- Owned coverage:
  - `DESIGN-REQ-004`: Owns executable-versus-placeholder semantics at submission.
  - `DESIGN-REQ-006`: Owns deterministic expansion and no-runtime-lookup invariants.
  - `DESIGN-REQ-015`: Owns authoring and executable payload distinction.
  - `DESIGN-REQ-016`: Owns provenance metadata requirements.
  - `DESIGN-REQ-023`: Owns proposal and promotion semantics.

### STORY-006: Compile Step Types into runtime plan contracts

- Jira issue type: Story
- Short name: `runtime-plan-contract`
- Source reference: `docs/Steps/StepTypes.md` sections: 7.2 Runtime plan mapping, 7.3 Backward compatibility, 10.3 Keep Activity Temporal-specific, 11. API Shape, 15. Non-Goals
- Why: The runtime needs an explicit discriminated contract while preserving the boundary between product authoring language and Temporal implementation mechanics.
- Description: As the execution system, I can compile validated Tool and Skill steps into runtime plan nodes while keeping Temporal Activity and adapter details out of the user-facing Step Type model.
- Independent test: Feed validated Tool, Skill, flattened preset-derived, and legacy-shaped payloads through the compile/normalization boundary and assert the resulting plan nodes and API discriminators match the desired contract.
- Dependencies: STORY-002, STORY-003, STORY-005
- Scope:
  - Model ToolStep, SkillStep, PresetStep, and ExecutableStep as discriminated API shapes.
  - Compile Tool steps into typed tool plan nodes.
  - Compile Skill steps into agent-facing skill plan nodes or execution requests.
  - Ensure Preset has no runtime node by default after ordinary submission.
  - Read documented legacy shapes during migration while normalizing new authoring to Step Type.
- Out of scope:
  - Replacing the existing plan executor.
  - Redefining Temporal Activity semantics.
  - Collapsing Tool and Skill into a single runtime-visible kind merely because both become plan nodes.
- Acceptance criteria:
  - The API exposes an explicit Step Type discriminator with separate Tool, Skill, and Preset sub-payloads.
  - ExecutableStep accepts ToolStep or SkillStep for ordinary submission.
  - Runtime mapping treats Temporal Activities, child workflows, and managed sessions as implementation concerns.
  - Legacy readers do not reintroduce ambiguous umbrella terminology in new UI or docs.
- Owned coverage:
  - `DESIGN-REQ-017`: Owns runtime plan mapping.
  - `DESIGN-REQ-018`: Owns legacy read compatibility constraints.
  - `DESIGN-REQ-022`: Owns discriminated API shape.
  - `DESIGN-REQ-025`: Owns non-goals involving plan executor and Temporal semantics.

### STORY-007: Validate Jira Step Type workflows end to end

- Jira issue type: Story
- Short name: `jira-step-type-flow`
- Source reference: `docs/Steps/StepTypes.md` sections: 9. Jira Example
- Why: The Jira example is the concrete product proof that Step Types help users distinguish direct integrations from agentic work and reusable workflow templates.
- Description: As a Jira workflow user, I can see deterministic Jira operations as Tool steps, agentic Jira work as Skill steps, and reusable Jira implementation flows as Presets that expand into mixed executable steps.
- Independent test: Run hermetic fixture-based tests or storybook-style UI tests showing the Jira Tool, Skill, and Preset examples and asserting the expanded sequence contains the expected typed steps.
- Dependencies: STORY-002, STORY-003, STORY-004, STORY-005
- Scope:
  - Provide or update representative Jira fixtures/examples for Tool, Skill, and Preset Step Types.
  - Validate a Jira transition as a deterministic Tool step.
  - Validate Jira triage or implementation as an agentic Skill step.
  - Validate a Jira implementation preset expansion into fetch, transition, implementation, tests, pull request, comment, and final transition steps where supported.
- Out of scope:
  - Changing Jira provider credentials or live Jira workflow configuration.
  - Requiring provider-verification tests for ordinary PR validation.
- Acceptance criteria:
  - Jira state changes can be authored as deterministic Tool steps.
  - Jira triage or implementation can be authored as Skill steps.
  - A reusable Jira implementation flow can be authored as a Preset and expanded into the documented mixed sequence.
  - The example reinforces, rather than bypasses, the validation and flattening rules from earlier stories.
- Owned coverage:
  - `DESIGN-REQ-021`: Owns the concrete Jira example and expected mixed-step behavior.

### STORY-008: Govern migration and open-decision guardrails

- Jira issue type: Story
- Short name: `migration-guardrails`
- Source reference: `docs/Steps/StepTypes.md` sections: 14. Migration Guidance, 15. Non-Goals, 16. Open Design Decisions
- Why: The design allows phased implementation but still needs enforceable boundaries around linked presets, naming, Temporal semantics, shell scripts, and legacy compatibility.
- Description: As a maintainer, I can migrate Step Type behavior in phases while preserving explicit non-goals and open-decision guardrails so desired-state docs do not drift into ambiguous implementation behavior.
- Independent test: Add migration/contract tests or documentation checks that assert new authoring surfaces use Step Type terminology, ordinary preset submission is flattened, and linked-preset or script-like behavior is absent unless explicitly specified.
- Dependencies: STORY-001, STORY-005, STORY-006
- Scope:
  - Track migration through UI terminology, draft model normalization, preset expansion normalization, and runtime contract convergence.
  - Keep linked-preset execution out of ordinary behavior unless separately specified as explicit and visibly different.
  - Prefer step.type for desired-state payloads unless implementation constraints justify an internal alternative while retaining UI Step Type wording.
  - Prevent renaming Tool to Script or Executable in user-facing Step Type labels.
  - Document and test that non-goals remain non-goals as migration proceeds.
- Out of scope:
  - Implementing linked presets.
  - Immediate removal of all legacy compatibility readers.
  - Making worker capability placement part of ordinary task authoring.
- Acceptance criteria:
  - Migration phases have traceable completion evidence without moving volatile checklists into canonical docs.
  - Linked presets remain disabled by default and require a separate explicit design before implementation.
  - Tool remains the user-facing label for typed executable operations.
  - Temporal Activity remains an implementation concept, not a Step Type label.
  - Non-goals are represented in tests, docs checks, or acceptance criteria for the implementing stories.
- Owned coverage:
  - `DESIGN-REQ-024`: Owns phased migration guidance.
  - `DESIGN-REQ-025`: Owns explicit non-goals not already owned by runtime-specific stories.
  - `DESIGN-REQ-026`: Owns open-decision guardrails.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-004, STORY-005
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-001, STORY-005
- `DESIGN-REQ-007` -> STORY-002
- `DESIGN-REQ-008` -> STORY-002
- `DESIGN-REQ-009` -> STORY-003
- `DESIGN-REQ-010` -> STORY-003
- `DESIGN-REQ-011` -> STORY-004
- `DESIGN-REQ-012` -> STORY-004
- `DESIGN-REQ-013` -> STORY-004
- `DESIGN-REQ-014` -> STORY-001
- `DESIGN-REQ-015` -> STORY-005
- `DESIGN-REQ-016` -> STORY-005
- `DESIGN-REQ-017` -> STORY-006
- `DESIGN-REQ-018` -> STORY-006
- `DESIGN-REQ-019` -> STORY-002, STORY-003, STORY-004
- `DESIGN-REQ-020` -> STORY-002
- `DESIGN-REQ-021` -> STORY-007
- `DESIGN-REQ-022` -> STORY-006
- `DESIGN-REQ-023` -> STORY-005
- `DESIGN-REQ-024` -> STORY-008
- `DESIGN-REQ-025` -> STORY-006, STORY-008
- `DESIGN-REQ-026` -> STORY-008

## Dependencies

- STORY-001 depends on: None
- STORY-002 depends on: STORY-001
- STORY-003 depends on: STORY-001
- STORY-004 depends on: STORY-001, STORY-002, STORY-003
- STORY-005 depends on: STORY-004
- STORY-006 depends on: STORY-002, STORY-003, STORY-005
- STORY-007 depends on: STORY-002, STORY-003, STORY-004, STORY-005
- STORY-008 depends on: STORY-001, STORY-005, STORY-006

## Out-of-Scope Items and Rationale

- Redefining Temporal Activity semantics: the design explicitly keeps Activity as a Temporal implementation concept.
- Replacing the plan executor: Step Types should compile into the existing runtime plan model rather than replace execution architecture.
- Hidden runtime preset work: ordinary presets expand before submission so runtime execution is deterministic.
- Arbitrary shell scripts as a Step Type: shell-like work must be represented only by approved typed tools with bounded inputs and policy.
- Immediate removal of all legacy readers: migration may read legacy shapes while new authoring converges on Step Type.
- Worker capability placement in ordinary authoring: capability remains a security/worker context, not a product Step Type umbrella.
- Collapsing Tool and Skill: the stories preserve deterministic and agentic boundaries even if both materialize as plan nodes.

## Coverage Gate

PASS - every major design point is owned by at least one story.
