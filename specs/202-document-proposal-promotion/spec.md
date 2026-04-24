# Feature Specification: Proposal Promotion Preset Provenance

**Feature Branch**: `202-document-proposal-promotion`
**Created**: 2026-04-17
**Status**: Draft
**Input**:

```text
# MM-388 MoonSpec Orchestration Input

## Source

- Jira issue: MM-388
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document proposal promotion with preset provenance
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-388 from MM project
Summary: Document proposal promotion with preset provenance
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-388 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-388: Document proposal promotion with preset provenance

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
 - 6. docs/Tasks/TaskProposalSystem.md
 - 8. Cross-document invariants
- Coverage IDs:
 - DESIGN-REQ-023
 - DESIGN-REQ-015
 - DESIGN-REQ-019
 - DESIGN-REQ-025
 - DESIGN-REQ-026

User Story
As a proposal reviewer, I want task proposals to preserve reliable preset metadata when available while promoting the reviewed flat task payload without live re-expansion drift.

Acceptance Criteria
- TaskProposalSystem invariants state preset-derived metadata is advisory UX/reconstruction metadata, not a runtime dependency.
- Proposal promotion does not require live preset catalog lookup for correctness.
- Canonical proposal payload examples may include task.authoredPresets and per-step source provenance alongside execution-ready flat steps.
- Promotion preserves authoredPresets and per-step provenance by default while validating the flat task payload as usual.
- Promotion does not re-expand live presets by default and documents any future refresh-latest workflow as explicit, not default.
- Proposal generators may preserve reliable parent-run preset provenance but must not fabricate bindings for work not authored from a preset.
- Proposal detail can distinguish manual, preset-derived with preserved binding metadata, and preset-derived flattened-only work.

Requirements
- Document proposal payload support for optional authored preset metadata.
- Document promotion behavior that avoids drift between review and promotion.
- Document generator guidance for reliable versus fabricated provenance.
- Document UI/observability treatment of proposal provenance states.

Relevant Implementation Notes
- The canonical active documentation target is `docs/Tasks/TaskProposalSystem.md`.
- The issue references `docs/Tasks/PresetComposability.md`; preserve the reference as Jira traceability even if the source document is unavailable in the current checkout.
- Preserve desired-state documentation under canonical `docs/` files and keep volatile migration or implementation tracking under `local-only handoffs`.
- Preset-derived metadata in proposals is advisory UX and reconstruction metadata, not a runtime dependency.
- Proposal promotion must validate and submit the reviewed flat task payload without requiring live preset catalog lookup or live preset re-expansion.
- Canonical proposal payload examples may include `task.authoredPresets` and per-step `source` provenance alongside execution-ready flat steps.
- Promotion should preserve authored preset metadata and per-step provenance by default while treating any future refresh-latest behavior as an explicit workflow.
- Proposal generators may preserve reliable parent-run preset provenance but must not fabricate preset bindings for work that was not authored from a preset.
- Proposal detail and observability surfaces should distinguish manual work, preset-derived work with preserved binding metadata, and preset-derived flattened-only work.

Verification
- Confirm `docs/Tasks/TaskProposalSystem.md` states preset-derived metadata is advisory UX/reconstruction metadata and not a runtime dependency.
- Confirm proposal promotion documentation avoids live preset catalog lookup and live preset re-expansion for correctness by default.
- Confirm canonical proposal payload examples include execution-ready flat steps and may include optional `task.authoredPresets` plus per-step `source` provenance.
- Confirm promotion behavior preserves authored preset metadata and per-step provenance by default while validating the flat task payload as usual.
- Confirm any future refresh-latest workflow is documented as explicit, not default.
- Confirm proposal generator guidance allows preserving reliable parent-run preset provenance and forbids fabricated bindings for work not authored from a preset.
- Confirm proposal detail or observability documentation distinguishes manual work, preset-derived work with preserved binding metadata, and preset-derived flattened-only work.
- Preserve MM-388 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-389 blocks this issue.
- MM-387 is blocked by this issue.
```

## User Story - Proposal Promotion Preset Provenance

**Summary**: As a proposal reviewer, I want task proposals to preserve reliable preset metadata when available while promoting the reviewed flat task payload without live re-expansion drift.

**Goal**: Reviewers can approve proposal promotion knowing that optional preset provenance remains available for reconstruction and UX context, while the promoted execution still uses the already-reviewed flat task payload and never depends on live preset catalog correctness.

**Independent Test**: Can be tested by reviewing the Task Proposal System contract and validating that it defines preset provenance as advisory metadata, keeps proposal promotion on the reviewed flat payload, preserves optional authored preset and per-step source fields, forbids default live re-expansion, and distinguishes manual, preserved-binding, and flattened-only proposal states.

**Acceptance Scenarios**:

1. **Given** a stored proposal contains reliable authored preset metadata and per-step source provenance, **When** a reviewer promotes it without overriding those fields, **Then** promotion preserves the metadata while validating and submitting the flat task payload as the execution contract.
2. **Given** a stored proposal was generated from a flat payload with no reliable preset binding, **When** proposal generation and detail rendering describe it, **Then** they must not fabricate authored preset bindings and must present it as flattened-only or manual work as applicable.
3. **Given** a live preset catalog has changed since the proposal was created, **When** the proposal is promoted, **Then** promotion must not use live catalog lookup or re-expand presets by default.
4. **Given** a future operator wants refreshed preset contents, **When** documentation describes that flow, **Then** it must be explicit refresh-latest behavior rather than default promotion behavior.
5. **Given** downstream MoonSpec, implementation notes, verification, commit text, or pull request metadata are generated for this work, **When** traceability is reviewed, **Then** the Jira issue key MM-388 remains present.

### Edge Cases

- A source document named by the Jira brief, `docs/Tasks/PresetComposability.md`, is absent in the current checkout; the preserved MM-388 Jira brief and current `docs/Tasks/TaskProposalSystem.md` are the active sources for this story.
- A proposal may contain no preset provenance, preserved authored preset metadata, or only flattened per-step source metadata; each state must be distinguishable without changing execution semantics.
- A candidate generator may know the parent run used presets but lack reliable binding metadata for a proposed follow-up; it must avoid inventing bindings.
- Live preset definitions may be deleted, renamed, or changed after proposal creation; default promotion still uses the reviewed flat payload.

## Assumptions

- The selected runtime mode means `docs/Tasks/TaskProposalSystem.md` is treated as a runtime contract for proposal behavior, even though this story updates canonical documentation.
- `task.authoredPresets` and `steps[].source` are optional task payload metadata fields already introduced by adjacent preset-composability documentation and must remain non-executable provenance.
- This story does not require executable backend or UI code changes unless verification finds current implementation contradicts the runtime contract.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Task Proposal System MUST state that preset-derived proposal metadata is advisory UX and reconstruction metadata, not a runtime dependency.
- **FR-002**: Proposal promotion MUST validate and submit the reviewed flat task payload without requiring live preset catalog lookup for correctness.
- **FR-003**: Canonical proposal payload examples MAY include optional `task.authoredPresets` and per-step `source` provenance alongside execution-ready flat steps.
- **FR-004**: Promotion MUST preserve `task.authoredPresets` and per-step provenance by default when present, unless an operator intentionally overrides those fields through a validated promotion override.
- **FR-005**: Proposal promotion MUST NOT re-expand live presets by default.
- **FR-006**: Any future refresh-latest preset workflow MUST be documented as explicit operator-selected behavior, not default promotion behavior.
- **FR-007**: Proposal generators MAY preserve reliable parent-run preset provenance but MUST NOT fabricate authored preset bindings for work that was not authored from a preset or lacks reliable binding metadata.
- **FR-008**: Proposal detail and observability surfaces MUST distinguish manual work, preset-derived work with preserved binding metadata, and preset-derived flattened-only work.
- **FR-009**: Canonical documentation updates MUST remain desired-state documentation and keep volatile migration or implementation tracking out of canonical docs.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST retain Jira issue key `MM-388` and the original Jira preset brief.

### Key Entities

- **Authored Preset Metadata**: Optional task-level provenance describing preset bindings that were reliably authored into a task before flattening.
- **Step Source Provenance**: Optional per-step metadata that identifies manual, preset-derived, preset-include, or detached state with preset references, include path, and original step id when available, without becoming executable logic.
- **Flat Task Payload**: The execution-ready proposal payload that promotion validates and submits to create a new run.
- **Flattened-Only Proposal**: A proposal whose steps may have originated from previous preset expansion but no longer carry reliable authored preset bindings.
- **Refresh-Latest Workflow**: An explicit future workflow that would intentionally re-resolve current preset definitions before submission.

## Source Design Requirements

- **DESIGN-REQ-015**: Source "Cross-document invariants" requires compile-time-only composition and no live runtime preset expansion. Scope: in scope. Maps to FR-002, FR-005, FR-006.
- **DESIGN-REQ-019**: Source "Snapshot durability and reconstruction" requires authored preset metadata and per-step provenance to remain available with flat execution payloads. Scope: in scope. Maps to FR-003, FR-004.
- **DESIGN-REQ-023**: Source "TaskProposalSystem proposal promotion" requires proposals to preserve reliable preset metadata while promoting the reviewed flat task payload without live re-expansion drift. Scope: in scope. Maps to FR-001 through FR-008.
- **DESIGN-REQ-025**: Source "Snapshot durability and evidence" requires provenance and expansion metadata to remain reconstruction evidence, not runtime execution logic. Scope: in scope. Maps to FR-001, FR-002, FR-008.
- **DESIGN-REQ-026**: Source "Execution-plane boundary" requires user-facing labels to avoid implying nested runtime workflows for preset includes. Scope: in scope. Maps to FR-008.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Review of `docs/Tasks/TaskProposalSystem.md` finds an invariant that preset-derived metadata is advisory UX/reconstruction metadata and not a runtime dependency.
- **SC-002**: Review of promotion flow documentation finds no required live preset catalog lookup or live re-expansion for default promotion correctness.
- **SC-003**: Review of canonical proposal payload examples finds execution-ready flat steps may coexist with optional `task.authoredPresets` and per-step `source` provenance.
- **SC-004**: Review of promotion behavior finds authored preset metadata and per-step provenance are preserved by default when present.
- **SC-005**: Review of generator guidance finds reliable provenance may be preserved and fabricated bindings are forbidden.
- **SC-006**: Review of proposal detail or observability documentation finds manual, preserved-binding preset-derived, and flattened-only proposal states are distinguishable.
- **SC-007**: All five in-scope source design requirements map to at least one functional requirement, and MM-388 remains present in MoonSpec artifacts and verification evidence.
