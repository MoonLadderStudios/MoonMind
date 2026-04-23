# Research: Themed Shimmer Band and Halo Layers

## FR-001 / SCN-001 Preserved Executing Base Appearance

Decision: Treat the current `.status-running` pill styling as the baseline executing shell and verify that the MM-489 shimmer layers remain additive instead of replacing the base appearance.
Evidence: `frontend/src/styles/mission-control.css` defines a base `.status-running` background and border, then adds executing-specific `background-color` and `background-image` on `.status-running[data-state="executing"][data-effect="shimmer-sweep"], .status-running.is-executing`.
Rationale: The source design and spec require the normal executing appearance to remain visible beneath the shimmer treatment.
Alternatives considered: Replace the base styling with a dedicated shimmer-only fill; rejected because it would break the additive treatment requirement.
Test implications: Unit CSS contract assertions plus integration render checks.

## FR-002 / DESIGN-REQ-006 Dual-Layer Band And Halo Model

Decision: Use the existing two-gradient shimmer block as the implementation baseline, but classify the requirement as implemented-unverified until tests prove the effect contains both a bright core band and a wider dimmer halo.
Evidence: `frontend/src/styles/mission-control.css` already defines two diagonal `linear-gradient(...)` layers with different opacity sources and different `background-size` / `background-position` values.
Rationale: The code already suggests the intended three-layer model, but current tests only assert the presence of `background-image`, not the semantic distinction between core and halo.
Alternatives considered: Treat the current CSS as fully verified; rejected because the existing test evidence is too coarse for the MM-489 requirement.
Test implications: Unit CSS contract tests.

## FR-003 / DESIGN-REQ-005 / DESIGN-REQ-008 Theme-Bound Color Roles

Decision: Keep the layered shimmer bound to existing Mission Control accent tokens and add explicit verification for token-derived role usage.
Evidence: The shimmer CSS uses `--mm-accent` and `--mm-accent-2`, and the token block already exposes executing sweep variables for timing, angle, and opacity.
Rationale: The source design explicitly forbids disconnected one-off palette choices and requires coherence across light and dark themes.
Alternatives considered: Introduce dedicated shimmer-only colors; rejected because it would weaken the source-design alignment and make the effect harder to maintain.
Test implications: Unit CSS contract tests.

## FR-004 Text Priority And Readability

Decision: Preserve the existing status-pill text rendering path and prove readability through additive styling tests before changing implementation.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/task-detail.tsx` continue to render plain text inside the shared pill span; the shimmer is currently a background treatment rather than a foreground overlay element.
Rationale: Background-based styling is the least invasive way to keep text primary, but the story still needs explicit proof that the layered treatment does not compromise readability.
Alternatives considered: Introduce overlay elements with explicit text wrappers; rejected at planning time because the current implementation may already satisfy the requirement with less churn.
Test implications: Unit CSS assertions plus integration render verification.

## FR-005 / DESIGN-REQ-009 / DESIGN-REQ-012 Bounds And Non-Interference

Decision: Reuse the current bounded CSS approach and verify it directly rather than assuming compliance from the existing MM-488 tests.
Evidence: The executing shimmer block uses `position: relative`, `overflow: hidden`, and `isolation: isolate`; task-list and task-detail pills still use the same span markup and do not create interactive overlay nodes.
Rationale: The current background-based treatment likely already satisfies hit-testing and layout requirements, but MM-489 needs explicit evidence for bounded placement and unchanged interaction behavior.
Alternatives considered: Add dedicated pointer-event or pseudo-element wrappers now; rejected because the current implementation may already be sufficient.
Test implications: Unit CSS contract tests and integration render checks.

## FR-006 / DESIGN-REQ-015 Reusable Effect Tokens

Decision: Expand the token surface if needed so band, halo, and related tunable values are reusable instead of partially hard-coded.
Evidence: `frontend/src/styles/mission-control.css` already exposes `--mm-executing-sweep-duration`, `--mm-executing-sweep-delay`, `--mm-executing-sweep-angle`, `--mm-executing-sweep-core-opacity`, and `--mm-executing-sweep-halo-opacity`, but width and positioning remain literal values inside the shimmer block.
Rationale: The source design’s suggested token block is broader than the current implementation, so the safest plan is to treat tokenization as partial until proof or refinement closes the gap.
Alternatives considered: Leave all remaining values hard-coded; rejected because it would weaken the reusable-token requirement.
Test implications: Unit CSS contract tests.

## FR-007 MM-489 Traceability

Decision: Add explicit MM-489 traceability to implementation/test artifacts instead of relying only on spec-level references.
Evidence: `frontend/src/utils/executionStatusPillClasses.ts` and its tests currently preserve MM-488 traceability only.
Rationale: Final verification needs downstream evidence tied to MM-489, not just the earlier shared-modifier story.
Alternatives considered: Preserve traceability only in the spec and plan; rejected because the spec requires implementation/test evidence too.
Test implications: Unit tests.

## Repo Gap Summary

Decision: Treat MM-489 as an implementation-refinement story with verification-first work.
Evidence: The repo already contains the shared shimmer selector contract, shared CSS shimmer block, list/detail render usage, and MM-488-oriented tests, but lacks MM-489-specific proof for preserved base appearance, semantic dual-layer behavior, complete tokenization, and MM-489 traceability.
Rationale: The fastest defensible path is to add MM-489-focused tests first, then adjust the shared CSS/helper only where those tests reveal a real gap.
Alternatives considered: Mark the story as fully implemented; rejected because current evidence does not directly prove several MM-489-specific requirements.
Test implications: Unit + integration.
