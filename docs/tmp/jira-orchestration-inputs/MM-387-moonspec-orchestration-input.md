# MM-387 MoonSpec Orchestration Input

## Source

- Jira issue: MM-387
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document Mission Control preset provenance surfaces
- Labels: `moonmind-workflow-mm-22746271-d34b-494d-bdf8-5c9daefbbdd4`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-387 from MM project
Summary: Document Mission Control preset provenance surfaces
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-387 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-387: Document Mission Control preset provenance surfaces

Source Reference
- Source Document: docs/Tasks/PresetComposability.md
- Source Title: Preset Composability
- Source Sections:
  - 5. docs/UI/MissionControlArchitecture.md
  - 8. Cross-document invariants
- Coverage IDs:
  - DESIGN-REQ-022
  - DESIGN-REQ-014
  - DESIGN-REQ-015
  - DESIGN-REQ-025
  - DESIGN-REQ-026

User Story
As a Mission Control operator, I want task lists, detail pages, and create/edit flows to explain preset-derived work without implying nested runtime behavior.

Acceptance Criteria
- MissionControlArchitecture includes preset-composition scope for preview, edit, and detail rendering without making composition a runtime concept.
- Task detail behavior may show provenance summaries and chips for Manual, Preset, and Preset path.
- Steps remain execution-first; preset grouping is explanatory metadata, not the primary ordering model.
- Submit integration allows `/tasks/new` to preview composed presets but forbids unresolved preset includes as runtime work.
- Expansion tree artifacts or summaries are secondary evidence; flat steps, logs, diagnostics, and output artifacts remain canonical execution evidence.
- Vocabulary distinguishes user-facing preset from internal preset binding/provenance and forbids subtask, sub-plan, or separate workflow-run labels for includes.

Requirements
- Document Mission Control preview, detail, edit, and submit behavior for preset-derived work.
- Document detail-page provenance affordances and execution-first ordering.
- Document artifact/evidence hierarchy for expansion summaries versus execution evidence.
- Document compatibility vocabulary for preset includes.

Relevant Implementation Notes
- The canonical active documentation target is `docs/UI/MissionControlArchitecture.md`.
- The issue references `docs/Tasks/PresetComposability.md`; preserve the reference as Jira traceability even if the source document is unavailable in the current checkout.
- Preserve desired-state documentation under canonical `docs/` files and keep volatile migration or implementation tracking under `docs/tmp/`.
- Mission Control may explain preset-derived work in previews, task lists, task details, and create/edit flows, but preset composition must not become a runtime execution concept.
- Task detail provenance summaries and chips may expose Manual, Preset, and Preset path metadata as explanatory context while keeping flat steps as the execution-first ordering model.
- Submit integration for `/tasks/new` may preview composed presets, but runtime work must not include unresolved preset includes.
- Expansion tree artifacts or summaries are secondary evidence; flat steps, logs, diagnostics, and output artifacts remain canonical execution evidence.
- User-facing vocabulary should say preset for operator concepts and binding/provenance for internal metadata, while avoiding subtask, sub-plan, or separate workflow-run labels for preset includes.

Verification
- Confirm `docs/UI/MissionControlArchitecture.md` documents preset-composition scope for preview, edit, task list, and task detail rendering without making composition a runtime concept.
- Confirm task detail documentation allows provenance summaries and chips for Manual, Preset, and Preset path while preserving flat execution-first step ordering.
- Confirm submit integration documentation for `/tasks/new` allows composed preset previews and forbids unresolved preset includes as runtime work.
- Confirm artifact and evidence hierarchy treats expansion summaries as secondary evidence behind flat steps, logs, diagnostics, and output artifacts.
- Confirm vocabulary distinguishes user-facing preset concepts from internal binding/provenance metadata and avoids subtask, sub-plan, or separate workflow-run labels for preset includes.
- Preserve MM-387 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- MM-388 blocks this issue.
- MM-386 is blocked by this issue.
