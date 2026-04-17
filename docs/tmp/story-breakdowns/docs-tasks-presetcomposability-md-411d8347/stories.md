# Preset Composability Story Breakdown

Source design: `docs/Tasks/PresetComposability.md`
Original source reference path: `docs/Tasks/PresetComposability.md`
Story extraction date: 2026-04-17T02:24:46Z
Requested output mode: jira

## Design Summary

The design makes preset composability a first-class authoring capability while preserving a flat execution model. Preset includes are resolved recursively in the control plane into deterministic PlanDefinition artifacts with provenance, cycle/limit validation, and durable snapshots. Create, Mission Control, task architecture, plan contracts, proposal promotion, and overview documentation must all preserve the same boundary: composed authoring metadata improves edit, rerun, observability, and save-as-preset workflows, but runtime workers execute only resolved flat steps and never depend on live preset catalog lookup.

## Coverage Points

### DESIGN-REQ-001 - Preset composition posture

- Type: `requirement`
- Source section: Design posture throughout
- Explanation: Presets are authoring-time control-plane objects that may contain concrete steps and includes; execution remains flat and runtime behavior is not recursively preset-aware.

### DESIGN-REQ-002 - TaskPresets purpose and terminology

- Type: `requirement`
- Source section: 1.1-1.3
- Explanation: Task preset documentation must define includes, expansion trees, flattened plans, provenance, detachment, goals, and non-goals for composability.

### DESIGN-REQ-003 - Preset entries union

- Type: `state-model`
- Source section: 1.4
- Explanation: Preset versions keep a top-level steps list but redefine it as an ordered union of kind: step and kind: include entries with pinned include versions, aliases, input mapping, and no v1 child overrides.

### DESIGN-REQ-004 - Include visibility and failure rules

- Type: `security`
- Source section: 1.5
- Explanation: GLOBAL and PERSONAL presets have constrained include visibility, and expansion fails fast for unreadable, missing, inactive, or incompatible includes.

### DESIGN-REQ-005 - Composable expansion pipeline

- Type: `integration`
- Source section: 1.6
- Explanation: Expansion resolves root inputs, recursively builds a tree, validates limits, renders steps, flattens plans, assigns deterministic IDs, attaches provenance, infers ordering edges, and stores plan plus summary artifacts.

### DESIGN-REQ-006 - Cycle detection and expansion limits

- Type: `constraint`
- Source section: 1.7
- Explanation: Direct and indirect cycles are invalid, include paths must be reported, and depth, step count, and rendered instruction byte limits must be enforced.

### DESIGN-REQ-007 - Deterministic resolved step IDs

- Type: `artifact`
- Source section: 1.8
- Explanation: Resolved step IDs are derived from root preset slug/version, include alias path, local step identity, and canonical input hash instead of transient indexes or database ordering.

### DESIGN-REQ-008 - Flattened step provenance

- Type: `artifact`
- Source section: 1.9
- Explanation: Each resolved step may carry source provenance for edit, rerun, and debugging while manual steps remain manual and runtime semantics do not change.

### DESIGN-REQ-009 - Expand API tree and flat response

- Type: `integration`
- Source section: 1.10
- Explanation: The expand API can return composition metadata and flat PlanDefinition views, with deterministic output for pinned versions and inputs.

### DESIGN-REQ-010 - Save-as-preset composition preservation

- Type: `state-model`
- Source section: 1.11 and 2.7
- Explanation: Saving current steps as a preset preserves intact includes only when exact provenance remains; detached or custom steps serialize as concrete steps and flattening must be explicit.

### DESIGN-REQ-011 - Create page composed draft model

- Type: `state-model`
- Source section: 2.1-2.3
- Explanation: Create page drafts preserve AppliedPresetBinding state and per-step source provenance, use preset-bound terminology, and support partial detachment without deleting the root binding.

### DESIGN-REQ-012 - Create page group rendering and apply flow

- Type: `ui`
- Source section: 2.4-2.5
- Explanation: The UI may render applied presets as groups, supports manual steps around groups, applies presets through server-side recursive expansion, and previews both tree and flat views before apply.

### DESIGN-REQ-013 - Reapply and detachment UX

- Type: `state-model`
- Source section: 2.6
- Explanation: Root input changes mark one binding dirty, reapply updates still-bound steps by default, detached steps remain untouched, and user copy must disclose the effect.

### DESIGN-REQ-014 - Edit/rerun reconstruction and degradation

- Type: `durability`
- Source section: 2.8
- Explanation: Edit and rerun reconstruct bindings, state, and flattened provenance when available, degrade explicitly when metadata is missing, and must not imply reapply support without usable metadata.

### DESIGN-REQ-015 - Create submission boundary

- Type: `integration`
- Source section: 2.9
- Explanation: Create submits flat resolved steps for execution while preserving authored bindings and provenance in the request or authoritative snapshot for UX and observability.

### DESIGN-REQ-016 - Create page test coverage

- Type: `test`
- Source section: 2.10
- Explanation: Tests must cover composed preview, apply, cycle/missing errors, detachment, reapply, save-as-preset preservation, edit/rerun reconstruction, and degraded reconstruction.

### DESIGN-REQ-017 - TaskArchitecture control-plane compilation

- Type: `integration`
- Source section: 3.1-3.3
- Explanation: Task architecture must add preset compilation as a control-plane phase and normalize manual and preset-derived steps into one fully resolved execution contract.

### DESIGN-REQ-018 - Task payload authored preset snapshot

- Type: `state-model`
- Source section: 3.4-3.5
- Explanation: TaskPayload gains optional authoredPresets and per-step source metadata, and authoritative snapshots preserve bindings, include summaries, provenance, detachment, and submitted order.

### DESIGN-REQ-019 - TaskArchitecture execution invariants

- Type: `constraint`
- Source section: 3.6-3.7
- Explanation: The execution plane receives already resolved steps, does not expand presets, and submitted runs remain executable and reconstructible without live preset lookup.

### DESIGN-REQ-020 - SkillAndPlan flattened plan contract

- Type: `integration`
- Source section: 4.1-4.2
- Explanation: SkillAndPlanContracts must define preset composition as outside execution; PlanDefinition artifacts contain only concrete nodes and edges and reject unresolved includes.

### DESIGN-REQ-021 - Plan node provenance validation

- Type: `state-model`
- Source section: 4.3-4.6
- Explanation: Plan nodes may include optional source provenance for UI, observability, and reconstruction, but runtime scheduling and behavior depend only on nodes, edges, policies, artifacts, and tool contracts.

### DESIGN-REQ-022 - Mission Control provenance rendering

- Type: `ui`
- Source section: 5.1-5.5
- Explanation: Mission Control supports preview, edit, detail provenance, chips, secondary expansion evidence, flat list posture, submit boundary, and vocabulary that avoids subtask/sub-plan language.

### DESIGN-REQ-023 - TaskProposal preset metadata preservation

- Type: `integration`
- Source section: 6.1-6.5
- Explanation: TaskProposalSystem may carry authored preset metadata and step provenance, promotes flat task payloads without re-expanding live presets by default, and never fabricates unreliable bindings.

### DESIGN-REQ-024 - Plans overview alignment

- Type: `documentation`
- Source section: 7
- Explanation: The plans overview must clarify that preset composition is resolved before PlanDefinition creation and link authoring semantics to TaskPresetsSystem and runtime semantics to SkillAndPlanContracts.

### DESIGN-REQ-025 - Cross-document invariant alignment

- Type: `constraint`
- Source section: 8
- Explanation: All updated documents must consistently state authoring/execution, determinism, UX durability, no v1 child override inheritance, and provenance-as-metadata boundaries.

### DESIGN-REQ-026 - Recommended implementation order

- Type: `migration`
- Source section: 9
- Explanation: The docs imply an implementation order: TaskPresetsSystem, CreatePage, TaskArchitecture, SkillAndPlanContracts, MissionControlArchitecture, TaskProposalSystem, then plans overview.

## Ordered Story Candidates

### STORY-001 - Document composable preset expansion contracts

- Short name: `preset-expansion`
- Source reference: `docs/Tasks/PresetComposability.md`
- Source sections: Design posture throughout, 1. docs/Tasks/TaskPresetsSystem.md, 8. Cross-document invariants
- Dependencies: None

As a task platform engineer, I want TaskPresetsSystem to define composable preset entries and deterministic expansion so presets can reuse other presets without changing runtime execution semantics.

**Independent test**

Review the updated TaskPresetsSystem documentation and a representative expansion contract to confirm a composed preset with one include expands deterministically into flat steps with provenance, while invalid include visibility and cycles fail before plan creation.

**Acceptance criteria**

- TaskPresetsSystem defines Preset Include, Expansion Tree, Flattened Plan, Preset Provenance, and Detachment.
- Preset version steps are documented as a union of kind: step and kind: include entries with required pinned include versions and distinct aliases for repeated child includes.
- Scope rules prevent GLOBAL presets from including PERSONAL presets and reject unreadable, missing, inactive, or incompatible includes.
- The composable expansion pipeline documents recursive resolution, cycle detection, limit enforcement, deterministic ID assignment, provenance attachment, flattening, and artifact/audit storage.
- Cycle and limit failures include enough detail to identify the include path that caused rejection.
- Save-as-preset semantics preserve intact includes only when exact provenance remains and serialize detached or custom steps as concrete steps.
- The executor boundary explicitly states that nested preset semantics are resolved before PlanDefinition storage.

**Requirements**

- Define preset composition as compile-time control-plane behavior only.
- Document kind: include storage semantics with pinned version, alias, input mapping, and no v1 child override behavior.
- Document deterministic resolved step ID inputs and per-step provenance shape.
- Document expand API output that can return composition and flat plan views.
- Document exact-match preservation semantics for save-as-preset.

**Owned coverage**

- `DESIGN-REQ-001`: Owns the base posture that presets compose at authoring time and execution remains flat.
- `DESIGN-REQ-002`: Owns TaskPresetsSystem purpose, terminology, goals, and non-goals.
- `DESIGN-REQ-003`: Owns the preset entries union and include semantics.
- `DESIGN-REQ-004`: Owns include scope, visibility, and fail-fast behavior.
- `DESIGN-REQ-005`: Owns the recursive expansion pipeline.
- `DESIGN-REQ-006`: Owns cycle detection and expansion limits.
- `DESIGN-REQ-007`: Owns deterministic ID rules.
- `DESIGN-REQ-008`: Owns flattened step provenance shape.
- `DESIGN-REQ-009`: Owns expand API tree and flat response semantics.
- `DESIGN-REQ-010`: Owns save-as-preset composition preservation at the preset contract level.
- `DESIGN-REQ-025`: Owns the cross-document invariants as the primary contract story.
- `DESIGN-REQ-026`: Appears first in the implementation order and enables the following stories.

**Assumptions**

- This story is documentation and contract oriented; implementation tasks are generated later by specify/plan/tasks.

**Needs clarification**

- None

### STORY-002 - Document Create page composed preset drafts

- Short name: `create-preset-drafts`
- Source reference: `docs/Tasks/PresetComposability.md`
- Source sections: 2. docs/UI/CreatePage.md, 8. Cross-document invariants
- Dependencies: STORY-001

As a Mission Control user, I want the Create page to preserve preset bindings, grouped preview, detachment, reapply, save-as-preset, and edit/rerun reconstruction so composed preset authoring remains understandable and durable.

**Independent test**

Use Create page documentation and UI contract tests to validate that applying a composed preset creates one root binding plus multiple sourced steps, editing one step only detaches that step, and edit/rerun either reconstructs bindings or shows explicit degraded-copy fallback.

**Acceptance criteria**

- CreatePage describes presets as authoring objects that may include other presets while execution uses flattened resolved steps.
- Draft state includes AppliedPresetBinding and StepDraft.source fields sufficient to track bindings, include paths, blueprint slugs, detachment, and expansion digest.
- Docs use preset-bound terminology instead of template-bound terminology.
- Preset application is server-expanded and receives binding metadata, flat steps, and per-step provenance; selecting a preset alone does not mutate the draft.
- Reapply updates still-bound steps by default, leaves detached steps untouched, and discloses the exact effect to the user.
- Save-as-preset preserves intact composition by default and requires explicit advanced action to flatten before save.
- Edit/rerun reconstruction preserves binding state when possible and clearly warns when only flat reconstruction is available.
- Testing requirements cover preview, apply, error handling, detachment, reapply, save-as-preset, reconstruction, and degraded fallback.

**Requirements**

- Define browser-side draft bindings as the source of preset authoring truth.
- Define preset grouping and insertion behavior without making the flattened execution order ambiguous.
- Define reapply, detachment, save-as-preset, edit/rerun, and submission boundaries for composed presets.
- Specify UI tests for success, error, and degraded reconstruction paths.

**Owned coverage**

- `DESIGN-REQ-011`: Owns the composed draft model and preset-bound terminology.
- `DESIGN-REQ-012`: Owns group rendering, insertion behavior, server-side apply, and preview modes.
- `DESIGN-REQ-013`: Owns reapply and detachment UX.
- `DESIGN-REQ-014`: Owns edit/rerun reconstruction and degraded-copy behavior.
- `DESIGN-REQ-015`: Owns Create submission boundary and snapshot preservation at the UI edge.
- `DESIGN-REQ-016`: Owns the Create page test list.
- `DESIGN-REQ-010`: Owns the UI-facing save-as-preset behavior.
- `DESIGN-REQ-025`: Reinforces UX durability and provenance-as-metadata invariants.
- `DESIGN-REQ-026`: Follows TaskPresetsSystem in the recommended implementation order.

**Assumptions**

- Create page implementation will call the preset expansion API rather than resolving includes in the browser.

**Needs clarification**

- None

### STORY-003 - Document task snapshot and compilation boundary

- Short name: `task-snapshot`
- Source reference: `docs/Tasks/PresetComposability.md`
- Source sections: 3. docs/Tasks/TaskArchitecture.md, 8. Cross-document invariants
- Dependencies: STORY-001

As a control-plane maintainer, I want TaskArchitecture to treat preset compilation as a control-plane phase and preserve authored preset metadata alongside flat steps so submitted work remains executable and reconstructible without live preset lookup.

**Independent test**

Review the TaskArchitecture contract and validation tests to confirm a task payload can carry authoredPresets plus flat steps, and that the execution-ready payload is valid without reading the preset catalog.

**Acceptance criteria**

- TaskArchitecture system snapshot says presets are recursively composable authoring objects resolved entirely in the control plane.
- A Preset compilation subsection defines recursive resolution, tree validation, flattening, and provenance preservation before execution contract finalization.
- Task contract normalization preserves authored preset binding metadata, flattened step provenance, manual and preset-derived step order, and fully resolved execution payloads.
- TaskPayload includes optional authoredPresets and steps[].source metadata with documented runtime semantics.
- Snapshot durability requirements preserve pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- Execution-plane boundary language states that workers do not expand presets or depend on live preset catalog correctness.

**Requirements**

- Document preset compilation as a control-plane phase.
- Document task payload snapshot fields for authored presets and step source provenance.
- Document durability rules that keep submitted runs executable after catalog changes.
- Add invariants for compile-time-only composition and no live preset lookup dependency.

**Owned coverage**

- `DESIGN-REQ-017`: Owns control-plane compilation and normalization responsibilities.
- `DESIGN-REQ-018`: Owns authoredPresets and per-step source metadata in TaskPayload.
- `DESIGN-REQ-019`: Owns execution-plane boundary and invariants in TaskArchitecture.
- `DESIGN-REQ-015`: Owns the architecture side of preserving UI-submitted authored metadata plus flat steps.
- `DESIGN-REQ-025`: Reinforces determinism, UX durability, and provenance-as-metadata invariants.
- `DESIGN-REQ-026`: Follows CreatePage and precedes plan-contract hardening in the recommended order.

**Assumptions**

- Stored snapshot shape is documented before concrete persistence implementation is specified.

**Needs clarification**

- None

### STORY-004 - Document flattened plan execution contract

- Short name: `flat-plan-contract`
- Source reference: `docs/Tasks/PresetComposability.md`
- Source sections: 4. docs/Tasks/SkillAndPlanContracts.md, 8. Cross-document invariants
- Dependencies: STORY-001, STORY-003

As a runtime contract owner, I want SkillAndPlanContracts to reject unresolved preset includes and treat provenance as optional metadata so the executor remains a flat graph executor regardless of authoring origin.

**Independent test**

Validate the plan contract documentation and plan validation tests by checking that concrete nodes with optional valid source provenance pass and unresolved preset include objects fail before execution.

**Acceptance criteria**

- SkillAndPlanContracts states preset composition is an authoring concern and defines only the flattened execution contract after expansion.
- PlanDefinition production rules state nodes are executable plan nodes only and include objects are invalid in stored plan artifacts.
- Plan node examples include optional source metadata with binding_id, include_path, blueprint_step_slug, and detached fields.
- Plan validation rejects unresolved preset include entries and structurally invalid claimed preset provenance while allowing absent provenance.
- DAG semantics clarify that manual authoring, preset expansion, and other plan-producing tools all produce the same flattened node-and-edge graph.
- Execution invariants state nested preset semantics do not exist at runtime and provenance is never executable logic.

**Requirements**

- Document the preset expansion boundary in SkillAndPlanContracts.
- Document optional source provenance on plan nodes.
- Document validation rules for absent, valid, and invalid provenance.
- Document that runtime behavior depends only on nodes, edges, policies, artifacts, and tool contracts.

**Owned coverage**

- `DESIGN-REQ-020`: Owns the flattened PlanDefinition contract boundary.
- `DESIGN-REQ-021`: Owns optional plan node provenance and validation semantics.
- `DESIGN-REQ-001`: Reinforces that runtime is not recursively preset-aware.
- `DESIGN-REQ-019`: Owns the plan-contract side of execution no-live-lookup behavior.
- `DESIGN-REQ-025`: Reinforces authoring/execution and provenance-as-metadata invariants.
- `DESIGN-REQ-026`: Follows TaskArchitecture in the recommended implementation order.

**Assumptions**

- Plan validation can treat claimed invalid provenance as an error without requiring all plans to carry provenance.

**Needs clarification**

- None

### STORY-005 - Document Mission Control preset provenance surfaces

- Short name: `mission-provenance`
- Source reference: `docs/Tasks/PresetComposability.md`
- Source sections: 5. docs/UI/MissionControlArchitecture.md, 8. Cross-document invariants
- Dependencies: STORY-002, STORY-003, STORY-004

As a Mission Control operator, I want task lists, detail pages, and create/edit flows to explain preset-derived work without implying nested runtime behavior.

**Independent test**

Review Mission Control architecture and UI expectations to confirm detail pages can show preset provenance chips and expansion evidence while list pages and execution ordering remain flat and high-signal.

**Acceptance criteria**

- MissionControlArchitecture includes preset-composition scope for preview, edit, and detail rendering without making composition a runtime concept.
- Task detail behavior may show provenance summaries and chips for Manual, Preset, and Preset path.
- Steps remain execution-first; preset grouping is explanatory metadata, not the primary ordering model.
- Submit integration allows `/tasks/new` to preview composed presets but forbids unresolved preset includes as runtime work.
- Expansion tree artifacts or summaries are secondary evidence; flat steps, logs, diagnostics, and output artifacts remain canonical execution evidence.
- Vocabulary distinguishes user-facing preset from internal preset binding/provenance and forbids subtask, sub-plan, or separate workflow-run labels for includes.

**Requirements**

- Document Mission Control preview, detail, edit, and submit behavior for preset-derived work.
- Document detail-page provenance affordances and execution-first ordering.
- Document artifact/evidence hierarchy for expansion summaries versus execution evidence.
- Document compatibility vocabulary for preset includes.

**Owned coverage**

- `DESIGN-REQ-022`: Owns Mission Control provenance rendering, submit boundary, evidence posture, and vocabulary.
- `DESIGN-REQ-014`: Owns the UI architecture side of reopening composed drafts from preserved metadata.
- `DESIGN-REQ-015`: Owns the Mission Control submit boundary that sends flat resolved intent.
- `DESIGN-REQ-025`: Reinforces provenance as explanatory metadata only.
- `DESIGN-REQ-026`: Follows SkillAndPlanContracts in the recommended implementation order.

**Assumptions**

- Detail-page rendering can initially be compact and metadata-focused rather than a full nested editor.

**Needs clarification**

- None

### STORY-006 - Document proposal promotion with preset provenance

- Short name: `proposal-provenance`
- Source reference: `docs/Tasks/PresetComposability.md`
- Source sections: 6. docs/Tasks/TaskProposalSystem.md, 8. Cross-document invariants
- Dependencies: STORY-003, STORY-004

As a proposal reviewer, I want task proposals to preserve reliable preset metadata when available while promoting the reviewed flat task payload without live re-expansion drift.

**Independent test**

Review TaskProposalSystem documentation and representative proposal payloads to confirm authoredPresets and source provenance can be preserved, promotion validates flat steps, and no live preset re-expansion is required by default.

**Acceptance criteria**

- TaskProposalSystem invariants state preset-derived metadata is advisory UX/reconstruction metadata, not a runtime dependency.
- Proposal promotion does not require live preset catalog lookup for correctness.
- Canonical proposal payload examples may include task.authoredPresets and per-step source provenance alongside execution-ready flat steps.
- Promotion preserves authoredPresets and per-step provenance by default while validating the flat task payload as usual.
- Promotion does not re-expand live presets by default and documents any future refresh-latest workflow as explicit, not default.
- Proposal generators may preserve reliable parent-run preset provenance but must not fabricate bindings for work not authored from a preset.
- Proposal detail can distinguish manual, preset-derived with preserved binding metadata, and preset-derived flattened-only work.

**Requirements**

- Document proposal payload support for optional authored preset metadata.
- Document promotion behavior that avoids drift between review and promotion.
- Document generator guidance for reliable versus fabricated provenance.
- Document UI/observability treatment of proposal provenance states.

**Owned coverage**

- `DESIGN-REQ-023`: Owns TaskProposalSystem invariants, payload, promotion, generator, and UI/observability changes.
- `DESIGN-REQ-015`: Reinforces flat task payload as the execution-ready promotion input.
- `DESIGN-REQ-019`: Reinforces no live lookup for correctness.
- `DESIGN-REQ-025`: Reinforces determinism and provenance-as-metadata invariants.
- `DESIGN-REQ-026`: Follows MissionControlArchitecture in the recommended implementation order.

**Assumptions**

- Proposal payloads can remain valid when authoredPresets is absent.

**Needs clarification**

- None

### STORY-007 - Document plans overview preset boundary

- Short name: `plans-overview`
- Source reference: `docs/Tasks/PresetComposability.md`
- Source sections: 7. docs/Temporal/101-PlansOverview.md, 8. Cross-document invariants
- Dependencies: STORY-001, STORY-004

As a documentation reader, I want the plans overview to link authoring-time preset composition to TaskPresetsSystem and runtime plan semantics to SkillAndPlanContracts so the boundary is discoverable.

**Independent test**

Inspect the equivalent plans overview/index documentation and confirm it contains a concise paragraph stating that preset composition is resolved before PlanDefinition creation and links to TaskPresetsSystem and SkillAndPlanContracts.

**Acceptance criteria**

- The plans overview or equivalent index includes the requested alignment paragraph near plan overview content.
- The paragraph states preset composition belongs to the control plane and is resolved before PlanDefinition creation.
- The paragraph states plans remain flattened execution graphs of concrete nodes and edges.
- The paragraph links authoring-time composition semantics to TaskPresetsSystem and runtime plan semantics to SkillAndPlanContracts.
- No additional migration checklist is added to canonical docs beyond the requested concise boundary clarification.

**Requirements**

- Add or update cross-links in the plans overview so the authoring/runtime boundary is obvious.
- Keep the update intentionally minimal.

**Owned coverage**

- `DESIGN-REQ-024`: Owns the overview/index alignment paragraph.
- `DESIGN-REQ-001`: Reinforces control-plane composition and flat runtime posture.
- `DESIGN-REQ-020`: Reinforces that runtime plan semantics belong in SkillAndPlanContracts.
- `DESIGN-REQ-025`: Reinforces cross-document invariant alignment.
- `DESIGN-REQ-026`: Represents the final recommended documentation alignment step.

**Assumptions**

- If docs/Temporal/101-PlansOverview.md does not exist, the equivalent plans overview/index document should receive the paragraph.

**Needs clarification**

- None

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001, STORY-004, STORY-007
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-001
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-001
- `DESIGN-REQ-007` -> STORY-001
- `DESIGN-REQ-008` -> STORY-001
- `DESIGN-REQ-009` -> STORY-001
- `DESIGN-REQ-010` -> STORY-001, STORY-002
- `DESIGN-REQ-011` -> STORY-002
- `DESIGN-REQ-012` -> STORY-002
- `DESIGN-REQ-013` -> STORY-002
- `DESIGN-REQ-014` -> STORY-002, STORY-005
- `DESIGN-REQ-015` -> STORY-002, STORY-003, STORY-005, STORY-006
- `DESIGN-REQ-016` -> STORY-002
- `DESIGN-REQ-017` -> STORY-003
- `DESIGN-REQ-018` -> STORY-003
- `DESIGN-REQ-019` -> STORY-003, STORY-004, STORY-006
- `DESIGN-REQ-020` -> STORY-004, STORY-007
- `DESIGN-REQ-021` -> STORY-004
- `DESIGN-REQ-022` -> STORY-005
- `DESIGN-REQ-023` -> STORY-006
- `DESIGN-REQ-024` -> STORY-007
- `DESIGN-REQ-025` -> STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007
- `DESIGN-REQ-026` -> STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001
- `STORY-004` depends on: STORY-001, STORY-003
- `STORY-005` depends on: STORY-002, STORY-003, STORY-004
- `STORY-006` depends on: STORY-003, STORY-004
- `STORY-007` depends on: STORY-001, STORY-004

## Out Of Scope

- Creating or modifying any `spec.md` files; this breakdown only creates story candidates.
- Creating directories under `specs/`; specification directories are created only during specify.
- Implementing preset composability code, tests, or migrations during breakdown.
- Creating Jira issues; the output mode is `jira`, but issue creation is a later workflow.

## Coverage Gate

PASS - every major design point is owned by at least one story.
