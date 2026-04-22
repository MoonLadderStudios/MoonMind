# MM-427 MoonSpec Orchestration Input

## Source

- Jira issue: MM-427
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Align Mission Control components with shared interaction language
- Labels: `moonmind-workflow-mm-d6451203-b4c9-4ae4-92b2-2b618c958888`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-427 from MM project
Summary: Align Mission Control components with shared interaction language
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-427 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-427: Align Mission Control components with shared interaction language

Source Reference
- Source Document: docs/UI/MissionControlDesignSystem.md
- Source Title: Mission Control Design System
- Source Sections:
  - 9. Interaction and motion
  - 10. Component system
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-016
  - DESIGN-REQ-017
  - DESIGN-REQ-018
  - DESIGN-REQ-022
  - DESIGN-REQ-023

User Story
As an operator, I want navigation, buttons, inputs, filters, chips, overlays, and transient UI to respond consistently so controls feel precise, accessible, and operationally legible.

Acceptance Criteria
- Hover states generally increase brightness, border light, or glow rather than darkening.
- Buttons use subtle hover scale-up, active scale-down, crisp high-contrast focus-visible, disabled de-emphasis, and semantic variant colors.
- Inputs/selects/comboboxes have designed shells, grounded wells, readable labels, visible focus, intentional icons, and clear click targets.
- Active filter chips are visible, removable, and paired with an intentional reset/clear affordance.
- Status chips are translucent, bordered, compact, semantically mapped, and keep finished states stable.
- Executing/live effects are low-amplitude and removed or significantly softened under reduced motion.
- Overlays and rails use glass only where elevation improves clarity, with readable grounded inner content.

Requirements
- Implement shared interaction timing and motion model.
- Align component variants and semantic states.
- Provide accessible focus and reduced-motion behavior.
- Keep elevated/transient surfaces readable and layered.

Implementation Notes
- Preserve MM-427 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/MissionControlDesignSystem.md` as the source design reference for Mission Control interaction, motion, focus, reduced-motion, component-state, chip, overlay, and transient-surface patterns.
- Scope implementation to shared Mission Control component interaction language. Do not change backend contracts, runtime orchestration, Jira Orchestrate preset behavior, or task submission payload semantics unless directly required by existing UI component integration.
- Keep hover, active, focus-visible, disabled, executing, and reduced-motion behavior consistent across navigation, buttons, inputs, filters, chips, overlays, rails, and transient UI.
- Ensure elevated and transient surfaces remain readable with grounded inner content instead of decorative glass use.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-427 is blocked by MM-428, whose embedded status is Backlog.
- Trusted Jira link metadata also shows MM-427 blocks MM-426, which is not a blocker for MM-427 and is ignored for dependency gating.
