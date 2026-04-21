# MM-424 MoonSpec Orchestration Input

Jira Orchestrate for MM-424.

Trusted Jira issue: MM-424.
Source document: `docs/UI/MissionControlDesignSystem.md`.
Source title: Mission Control Design System.
Source sections: 1. Purpose; 2. Product expression; 5. Color system and token contract; 6. Typography and iconography; 10.11 Background separators and horizon lines; 14. Summary.

## Canonical Brief

Establish Mission Control visual tokens and atmosphere.

As a Mission Control operator, I want the UI foundation to use the documented tokenized color, typography, atmosphere, and product-expression rules so every route feels coherent, readable, and unmistakably MoonMind.

### Coverage IDs

- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-011
- DESIGN-REQ-027

### Acceptance Criteria

- All core `--mm-*` tokens from the design document are defined as RGB triplets for both light and dark modes.
- Accent usage follows the documented semantic roles: purple/violet for identity, cyan for live/executing, amber/orange for warning, red/rose for failure/destructive, and green/teal for create/commit/complete.
- Mission Control backgrounds use restrained layered atmosphere and remain content-dominant in light and dark themes.
- IBM Plex Sans is the default UI typeface and IBM Plex Mono or tabular numerics are used for IDs, timestamps, runtime values, logs, versions, counts, durations, and compact telemetry.
- The resulting style avoids novelty HUD framing and preserves professional operator readability.

### Requirements

- Implement token-first visual foundation.
- Apply atmosphere and separator styling through reusable Mission Control CSS.
- Align typography and telemetry styling with the design system.
- Keep spectacle subordinate to readability and hierarchy.

## Runtime Constraints

- Use the existing Jira Orchestrate / MoonSpec lifecycle.
- Do not run implementation inline inside a breakdown task.
- Treat this as a single-story runtime UI design-system foundation story.
- Preserve MM-424 in spec artifacts, verification output, commit text, and PR metadata.
