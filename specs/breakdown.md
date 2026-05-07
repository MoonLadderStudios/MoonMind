# MoonSpec Breakdown Summary

## MM-593 - 2026-05-06

**Source**: Jira preset brief for MM-593
**Created**: 2026-05-06
**Gate Result**: PASS - every major design point is owned by at least one story.

## Design Summary

MM-593 is a broad Create-page, preset, skill, Jira, and submission-flow design. It must be split into isolated one-story specs and processed in dependency order while reusing valid existing specs instead of regenerating them.

## Coverage Points

- **DESIGN-REQ-001 - Normalized capability input contracts** (requirement): Presets and skills expose schema, UI hints, defaults, validation metadata, and safe input contracts.
- **DESIGN-REQ-002 - Generic schema-form rendering** (ui): Create page renders fields from schema constructs rather than capability-specific forms.
- **DESIGN-REQ-003 - Reusable widget registry** (ui): Widgets such as Jira, branch, provider, model, file, JSON, and scalar inputs are reusable field components.
- **DESIGN-REQ-004 - Jira issue picker** (integration): Jira issue inputs support search, manual entry, display, durable safe values, enrichment, and outage behavior.
- **DESIGN-REQ-005 - Jira credential isolation** (security): Raw Jira credentials remain isolated to trusted backend/worker Jira tool handlers.
- **DESIGN-REQ-006 - Field-addressable validation** (requirement): Missing or invalid capability inputs return stable field paths and messages.
- **DESIGN-REQ-007 - No capability-specific Create-page branches** (constraint): Adding a supported new preset or skill must not require preset- or skill-ID-specific React code.
- **DESIGN-REQ-008 - Schema rendering tests** (testing): Coverage proves schema rendering, Jira widget selection, manual key entry, validation, and new-capability behavior.
- **DESIGN-REQ-009 - Step type authoring surface** (state-model): Tool, Skill, and Preset are canonical authoring step types.
- **DESIGN-REQ-010 - Skill input alignment** (requirement): Skills align with the same input contract shape and renderer expectations.
- **DESIGN-REQ-011 - Shared preset expansion path** (integration): Preview, apply, reapply, submit-time expansion, API-created tasks, and reconstruction use one expansion path.
- **DESIGN-REQ-012 - Recursive expansion and cycle detection** (state-model): Nested preset expansion is recursive, deterministic, and rejects cycles.
- **DESIGN-REQ-013 - Preview behavior** (workflow): Preview shows generated steps without mutating the draft.
- **DESIGN-REQ-014 - Apply behavior** (workflow): Apply inserts editable child steps and preserves provenance.
- **DESIGN-REQ-015 - Executable final payload** (contract): Runtime payloads contain executable Tool/Skill steps rather than unresolved Preset steps.
- **DESIGN-REQ-016 - Preset provenance** (artifact): Generated steps include source preset ID, version, and safe input snapshot metadata.
- **DESIGN-REQ-017 - Submit-time unresolved preset expansion** (workflow): Create/Update/Rerun expands configured but unexpanded preset steps after explicit submit.
- **DESIGN-REQ-018 - Submit failure safety** (failure-handling): Failed expansion or ambiguous retargeting blocks submission without side effects and preserves the draft.
- **DESIGN-REQ-019 - End-to-end test evidence** (testing): Unit and integration coverage proves catalog loading, rendering, validation, expansion, provenance, and submit behavior.

## Dependency-Ordered Stories

1. **Schema-Driven Capability Inputs** - `specs/308-schema-driven-capability-inputs/spec.md` (generated_this_run)
   - Independent test: Select a capability with schema/UI metadata and verify generated fields, Jira issue picker behavior, field-addressable validation, and absence of capability-specific Create-page branches.
   - Dependency: none
   - Coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008
2. **Step Type Authoring Controls** - `specs/276-step-type-authoring-controls/spec.md` (existing_complete)
   - Independent test: Verify one Step Type control exposes Tool, Skill, and Preset configuration areas without stale incompatible fields.
   - Dependency: none; already complete and supports the authoring surface used by story 1
   - Coverage: DESIGN-REQ-009
3. **Agentic Skill Step Authoring** - `specs/283-agentic-skill-steps/spec.md` (existing_complete)
   - Independent test: Verify Skill steps are selected and submitted as explicit agentic Skill steps.
   - Dependency: none; already complete and contributes skill-selection behavior used by story 1
   - Coverage: DESIGN-REQ-010
4. **Composable Preset Expansion** - `specs/195-composable-preset-expansion/spec.md` (existing_complete)
   - Independent test: Expand parent and child presets and verify deterministic flattened output, include rejection, cycle detection, and provenance.
   - Dependency: after capability input contracts when nested preset inputs are involved; current artifact already complete for composition rules
   - Coverage: DESIGN-REQ-011, DESIGN-REQ-012
5. **Preset Preview and Apply** - `specs/291-preview-apply-preset-steps/spec.md` (existing_complete)
   - Independent test: Preview generated steps without mutating the draft, then apply them as editable executable steps with visible provenance.
   - Dependency: after schema-driven inputs because preview/apply needs configured preset values
   - Coverage: DESIGN-REQ-013, DESIGN-REQ-014
6. **Submit Flattened Executable Steps with Provenance** - `specs/292-submit-flattened-executable-steps-with-provenance/spec.md` (existing_complete)
   - Independent test: Submit a task and verify unresolved authoring-only preset state is absent while executable steps retain safe provenance.
   - Dependency: after preview/apply and composition stories because final submitted steps rely on flattened executable output and provenance
   - Coverage: DESIGN-REQ-015, DESIGN-REQ-016
7. **Submit Preset Auto-Expansion** - `specs/295-submit-preset-auto-expansion/spec.md` (existing_incomplete_verification_remaining)
   - Independent test: Submit a draft with unresolved Preset steps and verify the final task contains only executable steps or no side effect on expansion failure.
   - Dependency: after schema-driven inputs and executable submission semantics; resume existing tasks T029, T033, and T034 if selected
   - Coverage: DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019

## Coverage Matrix

| Design Point | Owner Specs |
| --- | --- |
| DESIGN-REQ-001 | `specs/308-schema-driven-capability-inputs/spec.md` |
| DESIGN-REQ-002 | `specs/308-schema-driven-capability-inputs/spec.md` |
| DESIGN-REQ-003 | `specs/308-schema-driven-capability-inputs/spec.md` |
| DESIGN-REQ-004 | `specs/308-schema-driven-capability-inputs/spec.md` |
| DESIGN-REQ-005 | `specs/308-schema-driven-capability-inputs/spec.md` |
| DESIGN-REQ-006 | `specs/308-schema-driven-capability-inputs/spec.md` |
| DESIGN-REQ-007 | `specs/308-schema-driven-capability-inputs/spec.md` |
| DESIGN-REQ-008 | `specs/308-schema-driven-capability-inputs/spec.md` |
| DESIGN-REQ-009 | `specs/276-step-type-authoring-controls/spec.md` |
| DESIGN-REQ-010 | `specs/283-agentic-skill-steps/spec.md` |
| DESIGN-REQ-011 | `specs/195-composable-preset-expansion/spec.md` |
| DESIGN-REQ-012 | `specs/195-composable-preset-expansion/spec.md` |
| DESIGN-REQ-013 | `specs/291-preview-apply-preset-steps/spec.md` |
| DESIGN-REQ-014 | `specs/291-preview-apply-preset-steps/spec.md` |
| DESIGN-REQ-015 | `specs/292-submit-flattened-executable-steps-with-provenance/spec.md` |
| DESIGN-REQ-016 | `specs/292-submit-flattened-executable-steps-with-provenance/spec.md` |
| DESIGN-REQ-017 | `specs/295-submit-preset-auto-expansion/spec.md` |
| DESIGN-REQ-018 | `specs/295-submit-preset-auto-expansion/spec.md` |
| DESIGN-REQ-019 | `specs/295-submit-preset-auto-expansion/spec.md` |

## Out Of Scope / Reuse Decisions

- No duplicate specs were generated for stories already covered by completed MoonSpec artifacts.
- `specs/295-submit-preset-auto-expansion` is reused as an incomplete existing story; if selected, resume its remaining verification tasks instead of regenerating its spec, plan, or tasks.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- `/speckit.verify` should be run after implementation to compare behavior against the original MM-593 brief preserved in the generated spec.
