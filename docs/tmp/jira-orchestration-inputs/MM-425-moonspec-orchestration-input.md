# MM-425 MoonSpec Orchestration Input

## Source

- Jira issue: MM-425
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Implement surface hierarchy and liquidGL fallback contract
- Labels: `moonmind-workflow-mm-d6451203-b4c9-4ae4-92b2-2b618c958888`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-425 from MM project
Summary: Implement surface hierarchy and liquidGL fallback contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-425 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-425: Implement surface hierarchy and liquidGL fallback contract

Source Reference
- Source Document: docs/UI/MissionControlDesignSystem.md
- Source Title: Mission Control Design System
- Source Sections:
  - 3.2 Matte for content, glass for controls
  - 3.3 One hero effect per page
  - 4. Surface hierarchy
  - 8. Glass system
  - 13.5 liquidGL enhancement contract
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-005
  - DESIGN-REQ-007
  - DESIGN-REQ-008
  - DESIGN-REQ-015
  - DESIGN-REQ-018
  - DESIGN-REQ-027

User Story
As an operator, I want content surfaces, control surfaces, and premium liquid-glass surfaces to have distinct roles and reliable fallbacks so dense work stays readable while elevated controls feel premium.

Acceptance Criteria
- Surface classes or modifiers clearly distinguish matte data slabs, satin form surfaces, glass controls, liquidGL hero targets, and accent/live surfaces.
- Default glass surfaces have token-driven translucent fill, 1px luminous border, controlled shadow separation, supported backdrop-filter blur/saturation, and coherent near-opaque fallback.
- liquidGL target components remain fully laid out, bordered, padded, shadowed, and legible with JavaScript/WebGL disabled.
- liquidGL is not applied to dense tables, large cards, long forms, large scrolling containers, large textareas, or default panel/card classes.
- A page has no more than one liquidGL hero surface by default; any second usage must be explicit and non-competing.
- Nested dense cards and panels use quieter, more opaque weights rather than repeating the same glass effect.

Requirements
- Implement strict surface hierarchy.
- Provide CSS glass as the default glass foundation.
- Treat liquidGL as bounded enhancement with graceful fallback.
- Preserve matte readability for dense content and editing surfaces.

Implementation Notes
- Preserve MM-425 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/MissionControlDesignSystem.md` as the source design reference for the surface hierarchy, glass system, and liquidGL fallback contract.
- Keep dense work surfaces readable; do not apply liquidGL to dense tables, large cards, long forms, large scrolling containers, large textareas, or default panel/card classes.
- Ensure CSS glass is the default foundation and liquidGL is only a bounded enhancement for explicit hero/control targets with complete non-JavaScript and non-WebGL fallbacks.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-425 is blocked by MM-426, whose embedded status is Backlog.
- Trusted Jira link metadata also shows MM-425 blocks MM-424, which is not a blocker for MM-425 and is ignored for dependency gating.
