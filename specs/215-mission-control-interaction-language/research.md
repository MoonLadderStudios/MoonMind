# Research: Mission Control Shared Interaction Language

## Decision 1: Implement shared interaction primitives in CSS tokens

Decision: Add reusable interaction tokens to `frontend/src/styles/mission-control.css`.

Rationale: `docs/UI/MissionControlDesignSystem.md` defines the desired interaction language as a shared product posture. Existing controls already consume CSS tokens for atmosphere and glass, so CSS tokens are the smallest coherent extension.

Alternatives considered: Introducing React wrapper components was rejected because existing controls are distributed across pages and the immediate gap is styling consistency, not state ownership.

## Decision 2: Replace routine translate lift with scale-only glow/grow behavior

Decision: Update routine button, button-link, action, extension, and icon-button hover/press rules to use shared scale tokens and no `translateY`.

Rationale: The design system explicitly prefers small scale changes and avoids translate lift for core buttons and nav pills. Existing CSS still had several `translateY(-1px)` hover rules.

Alternatives considered: Keeping lift on elevated controls was rejected for this story because the supplied brief asks to align components with the shared interaction language, and no specific component requires vertical motion to remain understandable.

## Decision 3: Treat toggles, filters, and chips as compact control shells

Decision: Give `.queue-inline-toggle`, `.queue-inline-filter`, and `.task-list-filter-chip` a shared shell/focus/disabled language.

Rationale: These controls appear in task-list control decks and utility bars. They are not full CTA buttons, but they should still share border light, fill, focus, and disabled posture.

Alternatives considered: Leaving chips as display-only badges was rejected because active filter chips are part of the operator control language and must remain visually related to adjacent reset/filter controls.

## Decision 4: Validate with CSS contract tests and existing behavior regression tests

Decision: Add focused assertions to `frontend/src/entrypoints/mission-control.test.tsx` and rerun the task-list UI test file.

Rationale: The story is a shared CSS contract with no backend behavior changes. CSS contract tests can directly verify token definitions and no-lift rules, while existing render tests prove behavior stayed intact.
