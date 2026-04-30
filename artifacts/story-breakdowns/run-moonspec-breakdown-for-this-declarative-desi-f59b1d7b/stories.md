# Step Types Story Breakdown

Source design: `docs/Steps/StepTypes.md`
Original source reference path: `docs/Steps/StepTypes.md`
Story extraction date: 2026-04-30T18:07:04Z
Requested output mode: jira

## Design Summary

The design defines Step Type as the user-facing discriminator for MoonMind task steps, with canonical values tool, skill, and preset. Tool and skill steps are executable, while preset steps are authoring-time placeholders that deterministically expand into validated executable steps with provenance metadata. The model spans UI authoring, typed contracts, validation, API payloads, runtime plan mapping, proposal promotion, naming, migration compatibility, and explicit non-goals around arbitrary scripts, hidden runtime preset work, and exposing Temporal implementation details to users.

## Coverage Points

- `DESIGN-REQ-001` (requirement): Single Step Type discriminator - Every authored step has exactly one user-facing Step Type that drives configuration, validation, execution eligibility, and runtime mapping. Source: 1. Purpose; 4. Core Invariants.
- `DESIGN-REQ-002` (state-model): Canonical taxonomy - The only canonical Step Types are tool, skill, and preset, with tool and skill executable and preset authoring-only by default. Source: 2. Desired-State Summary; 5. Step Type Taxonomy.
- `DESIGN-REQ-003` (integration): Tool typed operation contract - Tool steps represent explicit bounded operations with declared schemas, authorization, worker capabilities, retry policy, execution binding, validation, and error model. Source: 5.1 tool.
- `DESIGN-REQ-004` (integration): Skill agentic work contract - Skill steps represent agent-facing reusable behavior for reasoning, implementation, synthesis, or other open-ended work, including runtime context and controls. Source: 5.2 skill.
- `DESIGN-REQ-005` (requirement): Preset authoring placeholder - Preset steps are temporary authoring states used to select and configure reusable templates before expansion. Source: 5.3 preset; 7.1 Authoring payload.
- `DESIGN-REQ-006` (state-model): Deterministic preset expansion - Applying a preset deterministically replaces the temporary preset step with concrete tool and/or skill steps. Source: 4. Core Invariants; 5.3 preset; 6.6 Preset preview and apply.
- `DESIGN-REQ-007` (artifact): Preset provenance metadata - Expanded preset-derived steps preserve source metadata for audit, grouping, review, and reconstruction, but provenance is not required for runtime correctness. Source: 5.3 preset; 7.1 Authoring payload; 13. Proposal and Promotion Semantics.
- `DESIGN-REQ-008` (requirement): Step editor UX - The editor renders controls from the selected Step Type and handles type changes by preserving compatible fields, discarding incompatible fields visibly, or requiring confirmation. Source: 6.1 Step editor; 6.2 Step type picker.
- `DESIGN-REQ-009` (requirement): Tool picker UX - Tool selection supports search, domain grouping, schema-driven forms, and dynamic option providers such as Jira transition choices. Source: 6.3 Tool picker.
- `DESIGN-REQ-010` (requirement): Skill picker UX - Skill selection supports search, descriptions, compatibility hints, and clear distinction between deterministic tools and agentic skills. Source: 6.4 Skill picker.
- `DESIGN-REQ-011` (requirement): Preset picker and preview UX - Preset use lives inside step authoring, supports preview/apply/undo/origin/detach/compare/update flows, and is separate from preset management. Source: 6.5 Preset picker; 6.6 Preset preview and apply.
- `DESIGN-REQ-012` (state-model): Executable submission payload - Executable submissions normally contain only tool and skill steps and compile to the runtime plan; preset has no runtime node by default. Source: 7.1 Authoring payload; 7.2 Runtime plan mapping; 11. API Shape.
- `DESIGN-REQ-013` (migration): Migration compatibility readers - During migration MoonMind may read legacy step shapes while new authoring surfaces normalize to the Step Type model and avoid reintroducing ambiguous terminology. Source: 7.3 Backward compatibility; 14. Migration Guidance.
- `DESIGN-REQ-014` (requirement): Common validation rules - Every step must have stable local identity, title or display label, Step Type, type-specific payload, and pre-submission validation errors. Source: 8.1 Common validation.
- `DESIGN-REQ-015` (security): Type-specific validation rules - Tool, skill, and preset validation enforce existence, version/schema compatibility, authorization, runtime or worker capabilities, policy limits, expansion warnings, and rejection of unresolved presets or arbitrary shell snippets. Source: 8.2 Tool validation; 8.3 Skill validation; 8.4 Preset validation.
- `DESIGN-REQ-016` (integration): Jira workflow classification - Jira deterministic operations are tools, Jira triage or implementation is a skill, and reusable Jira flows are presets that expand into mixed executable steps. Source: 9. Jira Example.
- `DESIGN-REQ-017` (constraint): Naming policy - The UI should use Step Type and Tool, avoid Capability/Activity/Invocation/Command/Script as primary labels, and reserve Activity for Temporal implementation details. Source: 10. Naming Policy.
- `DESIGN-REQ-018` (artifact): Explicit API shape - The desired API is an explicit discriminated union over type with tool, skill, and preset sub-payloads, plus executable submissions limited to ToolStep or SkillStep. Source: 11. API Shape.
- `DESIGN-REQ-019` (requirement): Preset management separation - Preset management is a catalog/governance experience, while applying a preset to a task belongs inside step authoring. Source: 12. Preset Management vs Preset Use.
- `DESIGN-REQ-020` (artifact): Proposal promotion semantics - Stored proposals preserve executable flattened intent, validate reviewed payloads, and never silently re-expand live catalog entries. Source: 13. Proposal and Promotion Semantics.
- `DESIGN-REQ-021` (migration): Migration phases - Migration proceeds through UI terminology, draft normalization, preset expansion normalization, and runtime convergence while preserving necessary compatibility readers. Source: 14. Migration Guidance.
- `DESIGN-REQ-022` (non-goal): Non-goals - The design excludes redefining Temporal Activity semantics, replacing the plan executor, hidden runtime presets, arbitrary shell scripts as a Step Type, immediate removal of legacy readers, forcing worker placement concepts on users, and collapsing Tool and Skill together. Source: 15. Non-Goals.
- `DESIGN-REQ-023` (constraint): Open design defaults - The desired defaults are no linked presets, step.type as the preferred payload discriminator, and tool rather than script or executable as the Step Type name. Source: 16. Open Design Decisions.

## Ordered Story Candidates

### STORY-001 - Add Step Type authoring controls

Short name: `step-type-controls`
Source reference: `docs/Steps/StepTypes.md`; sections: 1. Purpose, 2. Desired-State Summary, 4. Core Invariants, 6.1 Step editor, 6.2 Step type picker, 10. Naming Policy
Description: As a task author, I can choose Tool, Skill, or Preset from one Step Type control so the editor shows only the configuration appropriate for that step.

Scope:
- Expose Step Type as the product-facing selector
- Render type-specific configuration surfaces
- Handle type switching with preserve, visible discard, or confirmation behavior
- Use approved labels and helper copy

Out of scope:
- Implementing runtime plan execution
- Building preset management screens

Independent test: A UI test creates a draft step, switches among Tool, Skill, and Preset, verifies the appropriate controls and helper text, and verifies data-loss confirmation when incompatible meaningful fields would be discarded.

Acceptance criteria:
- The editor shows exactly one Step Type selector with Tool, Skill, and Preset options.
- Changing Step Type changes the visible type-specific form.
- Compatible fields are preserved across changes where possible.
- Incompatible meaningful data is either visibly discarded or requires confirmation before removal.
- Primary UI labels use Step Type and avoid Capability, Activity, Invocation, Command, or Script as the umbrella label.

Requirements:
- Every authored step must have one selected Step Type.
- The selected Step Type must drive available sub-options.
- The product-facing picker must use Tool, Skill, and Preset helper text from the design.

Dependencies: None

Assumptions:
- Existing Create page draft state can represent the selected Step Type before backend convergence is complete.

Owned design coverage:
- `DESIGN-REQ-001`: Owns the single user-facing discriminator in the editor.
- `DESIGN-REQ-002`: Owns the canonical three-option selector.
- `DESIGN-REQ-008`: Owns editor rendering and type-change behavior.
- `DESIGN-REQ-017`: Owns user-facing naming policy in the authoring UI.

### STORY-002 - Model explicit Step Type payloads and validation

Short name: `step-type-payloads`
Source reference: `docs/Steps/StepTypes.md`; sections: 7.1 Authoring payload, 8. Validation Rules, 11. API Shape, 14. Migration Guidance
Description: As a platform maintainer, I can validate draft and submitted steps as an explicit discriminated model so invalid mixed-type payloads fail before execution.

Scope:
- Introduce or normalize explicit type-discriminated draft payloads
- Validate common fields for every step
- Validate tool, skill, and preset sub-payloads separately
- Reject invalid mixed-type steps and unresolved preset submissions by default

Out of scope:
- Changing Temporal Activity semantics
- Removing all legacy compatibility readers immediately

Independent test: Unit and route-level tests submit valid Tool, Skill, and Preset draft payloads plus invalid mixed, missing, arbitrary shell, and unresolved-preset executable payloads, then assert deterministic validation errors.

Acceptance criteria:
- A step payload has stable local identity, display label or title, type, and exactly the matching type-specific sub-payload.
- Tool validation checks tool existence/version, schema inputs, authorization, worker capability, forbidden fields, retry policy, and side-effect policy where those services are available.
- Skill validation checks skill resolution, contract inputs, runtime compatibility, required context, allowed tools or permissions, and autonomy constraints.
- Preset validation checks preset/version, input schema, deterministic expansion, generated-step validation, policy limits, and visible warnings.
- Executable submission rejects unresolved Preset steps unless a separately supported linked-preset mode is explicitly enabled.

Requirements:
- Use an explicit Step discriminated union or equivalent normalized internal shape.
- Keep legacy readers during migration while preventing new authoring surfaces from emitting ambiguous shapes.
- Surface validation errors before submission.

Dependencies: STORY-001

Assumptions:
- Backend validation can be introduced before every picker catalog is fully redesigned by using existing registry services.

Needs clarification:
- [NEEDS CLARIFICATION] If implementation constraints prefer step.action.kind internally, define the adapter boundary that still exposes Step Type and step.type externally.

Owned design coverage:
- `DESIGN-REQ-012`: Owns executable submission constraints in validation.
- `DESIGN-REQ-013`: Owns migration reader behavior without new ambiguous writes.
- `DESIGN-REQ-014`: Owns common validation fields.
- `DESIGN-REQ-015`: Owns type-specific validation gates.
- `DESIGN-REQ-018`: Owns the explicit discriminated API shape.
- `DESIGN-REQ-021`: Owns draft model normalization migration phase.

### STORY-003 - Implement typed Tool step authoring

Short name: `typed-tool-steps`
Source reference: `docs/Steps/StepTypes.md`; sections: 5.1 tool, 6.3 Tool picker, 8.2 Tool validation, 9. Jira Example, 10.1 Keep Tool
Description: As a task author, I can select a typed Tool operation and configure schema-backed inputs so deterministic integration work is governed and executable.

Scope:
- Tool catalog search and grouping
- Schema-driven tool forms
- Dynamic option provider support
- Governed typed command handling where explicitly approved

Out of scope:
- General arbitrary shell snippets as a Step Type
- Agentic interpretation or implementation flows

Independent test: A UI/API integration test configures Jira transition as a Tool step with dynamic status options and verifies that arbitrary shell-like payloads are rejected unless represented by an approved typed command tool.

Acceptance criteria:
- Tool picker supports search and grouping by integration or domain.
- Tool forms are generated from typed input schemas.
- Dynamic options can be loaded from option providers such as Jira transitions.
- Tool steps serialize with type tool and a tool sub-payload containing id, optional version, and inputs.
- Arbitrary scripts are rejected as Tool steps unless backed by an approved typed command tool contract.

Requirements:
- Tool definitions must declare name/version, input schema, output schema, authorization, worker capability, retry policy, execution binding, validation, and error model.
- Deterministic Jira operations such as fetching or transitioning issues are represented as Tool steps.

Dependencies: STORY-002

Assumptions:
- Existing tool registries can provide enough metadata for initial picker rendering and validation.

Owned design coverage:
- `DESIGN-REQ-003`: Owns typed Tool contract and operation semantics.
- `DESIGN-REQ-009`: Owns Tool picker and schema-driven form behavior.
- `DESIGN-REQ-015`: Owns Tool-specific validation within the broader validation model.
- `DESIGN-REQ-016`: Owns deterministic Jira operation classification.
- `DESIGN-REQ-022`: Owns the non-goal that arbitrary shell scripts are not a first-class Step Type.

### STORY-004 - Implement Skill step authoring

Short name: `agentic-skill-steps`
Source reference: `docs/Steps/StepTypes.md`; sections: 5.2 skill, 6.4 Skill picker, 8.3 Skill validation, 9. Jira Example
Description: As a task author, I can select an agent-facing Skill with instructions and runtime context so open-ended work is clearly separated from deterministic tools.

Scope:
- Skill selector with search and descriptions
- Skill inputs for instructions and relevant repository/project context
- Runtime/model preferences where applicable
- Allowed tool and approval/autonomy controls where applicable

Out of scope:
- Collapsing Skill and Tool into a common umbrella type
- Executing deterministic integration work through a Skill when a Tool contract exists

Independent test: A UI/API integration test creates a Skill step for Jira triage or code implementation, verifies compatibility hints and required context validation, and confirms the serialized payload remains type skill with a skill sub-payload.

Acceptance criteria:
- Skill picker supports search, descriptions, and compatibility hints.
- Skill configuration makes the agentic boundary clear to users.
- Skill steps serialize with type skill and a skill sub-payload containing id, optional version, and inputs.
- Required context, runtime compatibility, allowed tools or permissions, and approval constraints are validated before submission.
- Jira triage and implementation examples are represented as Skill steps rather than Tool steps.

Requirements:
- Use Skill for interpretation, planning, implementation, synthesis, or other open-ended reasoning.
- A Skill may use tools internally without changing the user-authored Step Type.

Dependencies: STORY-002

Assumptions:
- Skill contracts expose enough metadata for compatibility hints and required context checks.

Owned design coverage:
- `DESIGN-REQ-004`: Owns Skill contract and agentic semantics.
- `DESIGN-REQ-010`: Owns Skill picker behavior.
- `DESIGN-REQ-015`: Owns Skill-specific validation within the broader validation model.
- `DESIGN-REQ-016`: Owns agentic Jira work classification.
- `DESIGN-REQ-022`: Owns the non-goal of treating Tool and Skill as the same thing.

### STORY-005 - Preview and apply Preset steps

Short name: `preset-preview-apply`
Source reference: `docs/Steps/StepTypes.md`; sections: 5.3 preset, 6.5 Preset picker, 6.6 Preset preview and apply, 8.4 Preset validation, 12. Preset Management vs Preset Use, 16. Open Design Decisions
Description: As a task author, I can configure a Preset inside the step editor, preview the generated steps, and apply it into concrete executable Tool and Skill steps.

Scope:
- Preset selection inside the step editor
- Input schema configuration
- Deterministic preview of generated steps
- Apply replacement into executable steps
- Undo, origin display, detach, compare, and explicit version update affordances where supported

Out of scope:
- Making presets hidden runtime work
- Using Presets management screens as the place to apply a preset to a task

Independent test: A UI/API integration test configures a Jira implementation preset, previews the generated step list, applies it, verifies the draft now contains Tool and Skill steps with source provenance, and verifies undo restores the temporary Preset step.

Acceptance criteria:
- Preset use is available from the same step-authoring surface as Tool and Skill.
- The preview lists the concrete steps that will be inserted before application.
- Applying a preset replaces the temporary Preset step with expanded Tool and Skill steps.
- Generated steps validate under their own Tool or Skill rules before submission.
- Preset-derived steps preserve provenance metadata for audit and reconstruction.
- Refreshing to a newer preset version is explicit, visible, and validated.

Requirements:
- Preset expansion must be deterministic and validated before execution.
- Preset provenance must not be required for runtime correctness.
- Preset management remains separate from preset use.

Dependencies: STORY-002, STORY-003, STORY-004

Assumptions:
- Initial implementation can omit linked preset execution and treat linked presets as a future explicit mode.

Needs clarification:
- [NEEDS CLARIFICATION] Define whether any linked-preset mode is in scope now; the design default says no and requires a visibly different mode if introduced later.

Owned design coverage:
- `DESIGN-REQ-005`: Owns Preset as an authoring placeholder.
- `DESIGN-REQ-006`: Owns deterministic expansion behavior.
- `DESIGN-REQ-007`: Owns provenance metadata on expanded steps.
- `DESIGN-REQ-011`: Owns Preset picker, preview, apply, undo, origin, detach, compare, and update UX.
- `DESIGN-REQ-015`: Owns Preset-specific validation.
- `DESIGN-REQ-019`: Owns separation of preset use from preset management.
- `DESIGN-REQ-023`: Owns the default of no linked presets for ordinary preset application.

### STORY-006 - Compile executable steps into runtime plans

Short name: `runtime-plan-mapping`
Source reference: `docs/Steps/StepTypes.md`; sections: 7.1 Authoring payload, 7.2 Runtime plan mapping, 11. API Shape, 13. Proposal and Promotion Semantics, 14. Migration Guidance, 15. Non-Goals
Description: As an operator, submitted tasks execute from flattened Tool and Skill steps so runtime correctness does not depend on unresolved presets or live catalog re-expansion.

Scope:
- Compile Tool steps to typed tool plan nodes
- Compile Skill steps to agent execution requests or runtime-specific plan nodes
- Reject unresolved Preset nodes on normal execution paths
- Retain provenance only as audit/reconstruction metadata
- Keep proposal promotion based on reviewed flattened payloads

Out of scope:
- Replacing the plan executor
- Redefining Temporal Activity semantics
- Silently re-expanding live preset catalog entries during promotion or execution

Independent test: A workflow or service-boundary test submits a preset-derived flattened task and verifies the runtime plan contains only Tool and Skill execution nodes, preserves provenance metadata, and fails fast for unresolved Preset steps on the normal submission path.

Acceptance criteria:
- Executable submissions normally accept only Tool and Skill steps.
- Tool steps map to typed tool plan nodes.
- Skill steps map to plan nodes, child workflows, activities, or managed sessions without changing the Step Type UI contract.
- Preset steps produce no runtime node by default.
- Preset provenance is retained for audit and reconstruction but is not required to execute.
- Promotion validates the reviewed flat payload and never silently re-expands a live preset.

Requirements:
- Durable execution payloads should contain expanded executable steps by default.
- Preset-derived execution must not depend on live catalog lookup at runtime.
- Proposal promotion must preserve executable intent.

Dependencies: STORY-002, STORY-005

Assumptions:
- Existing plan compiler has an extension point for preserving source metadata without using it for execution correctness.

Owned design coverage:
- `DESIGN-REQ-006`: Owns expansion-before-execution requirement at runtime boundary.
- `DESIGN-REQ-007`: Owns provenance as non-runtime metadata.
- `DESIGN-REQ-012`: Owns runtime plan mapping and executable submission contract.
- `DESIGN-REQ-018`: Owns executable step subset of the API shape.
- `DESIGN-REQ-020`: Owns proposal promotion semantics.
- `DESIGN-REQ-021`: Owns runtime contract convergence migration phase.
- `DESIGN-REQ-022`: Owns non-goals around plan executor replacement and Temporal Activity semantics.

### STORY-007 - Align terminology and migration guardrails

Short name: `terminology-migration-guardrails`
Source reference: `docs/Steps/StepTypes.md`; sections: 3. Terminology, 7.3 Backward compatibility, 10. Naming Policy, 12. Preset Management vs Preset Use, 13. Proposal and Promotion Semantics, 14. Migration Guidance, 15. Non-Goals, 16. Open Design Decisions
Description: As a maintainer, I can migrate docs, UI copy, APIs, and proposal flows toward the Step Type model without reintroducing ambiguous naming or unsafe compatibility shortcuts.

Scope:
- Update product copy and docs to use Step Type consistently
- Keep Activity Temporal-specific
- Keep Capability only for worker/security affordance contexts
- Track migration phases across UI, draft model, preset expansion, and runtime contract convergence
- Document and enforce open decision defaults

Out of scope:
- Removing all legacy readers immediately
- Requiring users to understand worker capability placement
- Renaming Tool to Script or Executable

Independent test: A docs/copy lint or targeted regression test verifies Step Type terminology in authoring surfaces and checks that proposal promotion and preset use flows do not require live preset lookup or user-facing Activity/Capability terminology.

Acceptance criteria:
- User-facing authoring surfaces use Step Type for the discriminator.
- Tool remains the user-facing label for typed executable operations.
- Activity remains Temporal-specific and is not presented as a Step Type.
- Compatibility readers do not cause new UI, docs, or payload writes to reintroduce ambiguous umbrella terminology.
- Preset management remains catalog/governance only while preset use remains in step authoring.
- Migration tracking preserves compatibility readers where necessary without treating them as the desired model.

Requirements:
- Use Tool, Skill, and Preset terminology consistently across UI, API, docs, validation, proposal promotion, and preset expansion.
- Prefer step.type as the desired-state payload discriminator unless a documented internal alternative is chosen.
- Keep linked presets out of the ordinary path unless explicitly introduced with versioning, drift, refresh, validation, and audit rules.

Dependencies: STORY-001, STORY-002

Assumptions:
- Terminology alignment can be delivered incrementally while runtime convergence is implemented in separate stories.

Needs clarification:
- [NEEDS CLARIFICATION] Confirm whether step.type is the final public API field or whether step.action.kind will be used internally with an external Step Type mapping.

Owned design coverage:
- `DESIGN-REQ-013`: Owns compatibility-reader terminology guardrails.
- `DESIGN-REQ-017`: Owns naming policy across surfaces.
- `DESIGN-REQ-019`: Owns management-vs-use separation as a product rule.
- `DESIGN-REQ-020`: Owns promotion copy and behavior guardrails.
- `DESIGN-REQ-021`: Owns migration phase tracking.
- `DESIGN-REQ-022`: Owns explicit non-goals as guardrails.
- `DESIGN-REQ-023`: Owns open design defaults and unresolved decision tracking.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-003
- `DESIGN-REQ-004` -> STORY-004
- `DESIGN-REQ-005` -> STORY-005
- `DESIGN-REQ-006` -> STORY-005, STORY-006
- `DESIGN-REQ-007` -> STORY-005, STORY-006
- `DESIGN-REQ-008` -> STORY-001
- `DESIGN-REQ-009` -> STORY-003
- `DESIGN-REQ-010` -> STORY-004
- `DESIGN-REQ-011` -> STORY-005
- `DESIGN-REQ-012` -> STORY-002, STORY-006
- `DESIGN-REQ-013` -> STORY-002, STORY-007
- `DESIGN-REQ-014` -> STORY-002
- `DESIGN-REQ-015` -> STORY-002, STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-016` -> STORY-003, STORY-004
- `DESIGN-REQ-017` -> STORY-001, STORY-007
- `DESIGN-REQ-018` -> STORY-002, STORY-006
- `DESIGN-REQ-019` -> STORY-005, STORY-007
- `DESIGN-REQ-020` -> STORY-006, STORY-007
- `DESIGN-REQ-021` -> STORY-002, STORY-006, STORY-007
- `DESIGN-REQ-022` -> STORY-003, STORY-004, STORY-006, STORY-007
- `DESIGN-REQ-023` -> STORY-005, STORY-007

## Dependencies

- `STORY-001` depends on no prior stories.
- `STORY-002` depends on STORY-001.
- `STORY-003` depends on STORY-002.
- `STORY-004` depends on STORY-002.
- `STORY-005` depends on STORY-002, STORY-003, STORY-004.
- `STORY-006` depends on STORY-002, STORY-005.
- `STORY-007` depends on STORY-001, STORY-002.

## Out-of-Scope Items and Rationale

- Implementing runtime plan execution: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Building preset management screens: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Changing Temporal Activity semantics: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Removing all legacy compatibility readers immediately: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- General arbitrary shell snippets as a Step Type: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Agentic interpretation or implementation flows: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Collapsing Skill and Tool into a common umbrella type: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Executing deterministic integration work through a Skill when a Tool contract exists: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Making presets hidden runtime work: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Using Presets management screens as the place to apply a preset to a task: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Replacing the plan executor: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Redefining Temporal Activity semantics: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Silently re-expanding live preset catalog entries during promotion or execution: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Removing all legacy readers immediately: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Requiring users to understand worker capability placement: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.
- Renaming Tool to Script or Executable: excluded from the owning story to preserve independent validation and match the declarative design's non-goals or story boundary.

## Coverage Gate

PASS - every major design point is owned by at least one story.
