# Research: Create Page Composed Preset Drafts

## Input Classification

Decision: Treat MM-384 as a single-story runtime feature request.

Rationale: The Jira brief contains one actor, one outcome, one Create page surface, and one cohesive behavior set around composed preset draft authoring. Although the summary begins with "Document", the selected mode is runtime and the brief defines product behavior requirements rather than a broad source design.

Alternatives considered: Treating the input as a broad declarative design was rejected because the brief already selects the Create page story and enumerates concrete acceptance criteria. Treating it as docs-only was rejected because the user explicitly selected runtime mode.

## Missing Source Document

Decision: Use the preserved MM-384 Jira brief, `docs/UI/CreatePage.md`, and `docs/Tasks/TaskPresetsSystem.md` as the available source requirements because `docs/Tasks/PresetComposability.md` is absent from the current checkout.

Rationale: The Jira brief names `docs/Tasks/PresetComposability.md`, but that path is not present. The current repository contains preset composition requirements in `docs/Tasks/TaskPresetsSystem.md`, including preset includes, expansion trees, flattened plans, provenance, detachment, save-as-preset semantics, and expansion output. The Create page doc is the target surface.

Alternatives considered: Blocking on the missing source file was rejected because the Jira brief includes sufficient single-story acceptance criteria and the current checkout has equivalent preset composition source material. Inventing an unavailable source document was rejected.

## Implementation Scope

Decision: Update the Create page desired-state contract and MoonSpec evidence without changing executable UI code.

Rationale: Current `docs/UI/CreatePage.md` already describes many preset and attachment behaviors, but it still uses template-bound terminology and does not model composed preset bindings with `AppliedPresetBinding` and `StepDraft.source`. The request is to document the Create page composed draft contract. No executable code path was identified as required by MM-384 in this slice.

Alternatives considered: Adding frontend state implementation was rejected as hidden scope because MM-384 asks to document the contract, and no existing spec artifact for this issue requires code behavior changes.

## Validation Strategy

Decision: Use documentation-contract validation via targeted `rg` checks and final MoonSpec verification.

Rationale: The implementation target is canonical documentation. The highest-signal tests are checks that required terms and behaviors exist and legacy template-bound terminology is removed from the Create page contract. Full unit tests are not meaningful unless executable UI code changes.

Alternatives considered: Running the full unit suite was rejected as unnecessary for a docs-only implementation artifact, though it remains required if frontend code changes are introduced later.
