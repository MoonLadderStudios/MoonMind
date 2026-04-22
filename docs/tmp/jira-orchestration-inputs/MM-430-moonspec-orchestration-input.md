# MM-430 MoonSpec Orchestration Input

## Source

- Jira issue: MM-430
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Preserve Mission Control styling source and build invariants
- Labels: `moonmind-workflow-mm-d6451203-b4c9-4ae4-92b2-2b618c958888`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-430 from MM project
Summary: Preserve Mission Control styling source and build invariants
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-430 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-430: Preserve Mission Control styling source and build invariants

Source Reference
Source Document: docs/UI/MissionControlDesignSystem.md
Source Title: Mission Control Design System
Source Sections:
- 1. Purpose
- 13. Implementation invariants
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-024
- DESIGN-REQ-025
- DESIGN-REQ-026

User Story
As a maintainer, I want Mission Control styling to remain token-first, semantically named, and built from the canonical source files so future UI work does not drift into hardcoded colors or hand-edited build artifacts.

Acceptance Criteria
- Existing semantic shell class names such as dashboard-root, masthead, route-nav, panel, card, toolbar, status-*, and queue-* remain stable where applicable.
- New shared styling uses additive modifiers such as panel--controls, panel--data, panel--floating, panel--utility, or table-wrap--wide where useful.
- Mission Control semantic classes consume --mm-* tokens instead of introducing hardcoded opaque colors for tokenized roles.
- Light and dark themes stay behaviorally identical through token swaps rather than scattered one-off overrides.
- Tailwind content scanning includes the documented template and frontend source paths.
- Design-system changes edit frontend/src/styles/mission-control.css or source components/templates, not generated dist assets.

Requirements
- Protect semantic class stability.
- Enforce token-first theming.
- Validate Tailwind source scanning.
- Preserve the canonical styling source and generated-artifact boundary.

Implementation Notes
- Preserve MM-430 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/MissionControlDesignSystem.md` as the source design reference for Mission Control purpose, semantic styling conventions, and implementation invariants.
- Scope implementation to Mission Control styling source preservation, semantic class stability, token-first theming, Tailwind content scanning, and generated-asset boundary checks.
- Keep styling changes in `frontend/src/styles/mission-control.css` or source components/templates; do not hand-edit generated dist assets.
- Ensure light and dark themes continue to vary through `--mm-*` token swaps rather than scattered one-off overrides.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-430 blocks MM-429, whose embedded status is Code Review. This is not a blocker for MM-430 and is ignored for dependency gating.
