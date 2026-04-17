# MM-384 MoonSpec Orchestration Input

## Source

- Jira issue: MM-384
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document Create page composed preset drafts
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-384 from MM project
Summary: Document Create page composed preset drafts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-384 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-384: Document Create page composed preset drafts

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
  - 2. docs/UI/CreatePage.md
  - 8. Cross-document invariants
- Coverage IDs:
  - DESIGN-REQ-011
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-015
  - DESIGN-REQ-016
  - DESIGN-REQ-010
  - DESIGN-REQ-025
  - DESIGN-REQ-026

User Story
As a Mission Control user, I want the Create page to preserve preset bindings, grouped preview, detachment, reapply, save-as-preset, and edit/rerun reconstruction so composed preset authoring remains understandable and durable.

Acceptance Criteria
- CreatePage describes presets as authoring objects that may include other presets while execution uses flattened resolved steps.
- Draft state includes AppliedPresetBinding and StepDraft.source fields sufficient to track bindings, include paths, blueprint slugs, detachment, and expansion digest.
- Docs use preset-bound terminology instead of template-bound terminology.
- Preset application is server-expanded and receives binding metadata, flat steps, and per-step provenance; selecting a preset alone does not mutate the draft.
- Reapply updates still-bound steps by default, leaves detached steps untouched, and discloses the exact effect to the user.
- Save-as-preset preserves intact composition by default and requires explicit advanced action to flatten before save.
- Edit/rerun reconstruction preserves binding state when possible and clearly warns when only flat reconstruction is available.
- Testing requirements cover preview, apply, error handling, detachment, reapply, save-as-preset, reconstruction, and degraded fallback.

Requirements
- Define browser-side draft bindings as the source of preset authoring truth.
- Define preset grouping and insertion behavior without making the flattened execution order ambiguous.
- Define reapply, detachment, save-as-preset, edit/rerun, and submission boundaries for composed presets.
- Specify UI tests for success, error, and degraded reconstruction paths.

Relevant Implementation Notes
- Keep Create page documentation focused on preset authoring behavior while execution remains based on flattened resolved steps.
- Preserve binding metadata, include paths, blueprint slugs, detachment state, and expansion digests in the documented draft model.
- Use preset-bound terminology consistently in Create page docs.
- Treat preset application as an explicit server-expanded action; selecting a preset alone must not mutate the draft.
- Preserve intact composition for save-as-preset by default, with flattening only as an explicit advanced action.
- Preserve binding state during edit/rerun reconstruction when possible and warn when reconstruction can only use flattened steps.

Verification
- Confirm `docs/UI/CreatePage.md` documents composed preset drafts, grouped preview, binding state, detachment, reapply, save-as-preset, edit/rerun reconstruction, and degraded fallback behavior.
- Confirm the Create page documentation remains consistent with `docs/Tasks/PresetComposability.md` cross-document invariants.
- Confirm coverage for DESIGN-REQ-010 through DESIGN-REQ-016, DESIGN-REQ-025, and DESIGN-REQ-026 from `docs/Tasks/PresetComposability.md`.
- Add or update UI tests only if executable Create page behavior changes as part of implementation.

Out of Scope
- Changing flattened runtime execution semantics for resolved task steps.
- Treating preset selection alone as a draft mutation.
- Flattening composed presets by default during save-as-preset.
