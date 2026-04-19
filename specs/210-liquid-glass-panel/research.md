# Research: Liquid Glass Publish Panel

## FR-001 / SC-001: Liquid Glass Treatment

Decision: `partial`; refine the existing `.queue-floating-bar` styling rather than introduce a new component.
Evidence: `frontend/src/styles/mission-control.css` already defines `.queue-floating-bar` with translucent background, border, shadow, and `backdrop-filter: blur(22px) saturate(1.5)`.
Rationale: The requested behavior is a visual enhancement of the current bottom publish/action panel. Reusing the existing fixed bar keeps behavior stable and limits risk.
Alternatives considered: Create a new panel component or move controls into a different container; rejected because the target controls already share the correct panel.
Test implications: Unit/style assertions plus visual quickstart verification.

## FR-002: Correct Target Panel

Decision: `implemented_unverified`; preserve the existing control grouping.
Evidence: `frontend/src/entrypoints/task-create.tsx` renders repository, branch, publish mode, and submit button inside `.queue-floating-bar.queue-step-actions.queue-step-submit-actions`.
Rationale: The target panel already holds the requested controls, so implementation should not restructure the Create page.
Alternatives considered: Move the panel into the Steps card markup; rejected for this story because the controls are already grouped and fixed at the bottom.
Test implications: Unit assertions should target the submission controls group and verify the controls remain present.

## FR-003 / SC-002: Readability

Decision: `partial`; preserve existing accessible names and strengthen visual contrast if needed.
Evidence: `task-create.tsx` gives the controls `aria-label` values for GitHub Repo, Branch, Publish Mode, and the primary create action; existing CSS uses tokenized foreground and panel colors.
Rationale: Visual polish must not make controls harder to read. The plan should test both presence and accessible names and include light/dark visual verification.
Alternatives considered: Visual-only quickstart checks; rejected because accessible names are cheap to verify automatically.
Test implications: Unit tests for accessible controls; quickstart visual check for light/dark readability.

## FR-004 / FR-005 / SC-004 / SC-005: Behavior Preservation

Decision: `implemented_unverified`; add regression coverage around current behavior after style changes.
Evidence: Existing submit code in `task-create.tsx` maps repository, branch, publish mode, and merge automation into the task payload; existing `task-create.test.tsx` contains broad Create page submission tests.
Rationale: Styling should not alter create behavior, but final proof should rerun focused Create page tests and keep request-shape coverage intact.
Alternatives considered: No new behavior tests; rejected because the story explicitly requires unchanged create behavior.
Test implications: Integration-style UI request-shape tests and full focused Create page test rerun.

## FR-006 / FR-007 / SC-003: Layout Stability and Responsiveness

Decision: `partial`; use existing grid layout and improve only if verification exposes fit or overlap problems.
Evidence: `.queue-floating-bar-row` uses explicit grid tracks and a mobile media rule that lets the repository selector span full width.
Rationale: Existing stable dimensions are close to the requirement. The implementation should avoid dynamic layout churn and verify desktop/mobile behavior.
Alternatives considered: Convert to a new flex layout; rejected unless testing shows the current grid cannot satisfy mobile fit.
Test implications: Unit assertions for stable class usage plus quickstart checks at desktop and mobile widths.

## FR-008: Light and Dark Appearance

Decision: `partial`; rely on existing theme tokens and verify both appearances.
Evidence: `MissionControlStyleGuide.md` defines glass utility guidance and tokenized light/dark backgrounds; `mission-control.css` uses `rgb(var(--mm-*` token colors.
Rationale: The treatment should fit the existing Mission Control visual system and not create a one-off theme.
Alternatives considered: Hardcoded colors; rejected because they would drift from the design system and dark mode.
Test implications: Documented visual verification in light and dark mode; unit tests where class/style selectors are stable.

## FR-009: Verification Coverage

Decision: `missing`; add focused Create page UI tests for the panel treatment and preserve existing interaction tests.
Evidence: Current tests cover Create page behavior but no test is specific to MM-210 liquid glass panel treatment.
Rationale: The spec explicitly requires automated or documented UI verification.
Alternatives considered: Manual-only verification; rejected because focused DOM/style coverage is practical.
Test implications: Add Vitest/Testing Library checks and quickstart visual checklist.

## FR-010 / SC-006: Traceability

Decision: `implemented_unverified`; preserve the supplied non-key Jira reference in downstream artifacts.
Evidence: `specs/210-liquid-glass-panel/spec.md` preserves the supplied issue reference and original preset brief verbatim.
Rationale: Final verification needs to compare implementation against the original prompt, even though the supplied Jira reference is not a canonical Jira key.
Alternatives considered: Omit invalid Jira key text; rejected because the user explicitly requested preservation.
Test implications: Final MoonSpec verification only.
