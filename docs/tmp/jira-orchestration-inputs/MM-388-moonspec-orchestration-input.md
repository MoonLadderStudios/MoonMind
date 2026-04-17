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
- Preserve desired-state documentation under canonical `docs/` files and keep volatile migration or implementation tracking under `docs/tmp/`.
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
