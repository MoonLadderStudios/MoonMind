# Research: Shared Executing Shimmer for Status Pills

## FR-001 / FR-008 Shared Executing Modifier Surface

Decision: Treat the existing `status-running` pill as the baseline shared surface, then layer the executing shimmer contract on top of it rather than creating a second status-pill component.
Evidence: `frontend/src/utils/executionStatusPillClasses.ts` already routes executing-like states to `status status-running`; `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/task-detail.tsx` already render those shared status pills across list, card, and detail surfaces.
Rationale: The story is about a shared modifier on existing status pills, not a replacement component.
Alternatives considered: Introduce a dedicated React status-pill component for every surface; rejected because the current repo already shares helper-driven pill markup and the story explicitly forbids layout-changing wrappers.
Test implications: Integration tests for task list and detail surfaces, plus unit/CSS contract tests.

## FR-002 / DESIGN-REQ-003 Selector Contract

Decision: Add the preferred executing-state contract through explicit pill attributes such as `data-state="executing"` and `data-effect="shimmer-sweep"`, while preserving an additive `.is-executing` fallback path for supported existing hosts.
Evidence: No preferred selector or fallback executing marker currently appears in `frontend/src/styles/mission-control.css` or the status-pill spans in `tasks-list.tsx` and `task-detail.tsx`.
Rationale: The source design requires a shared modifier contract with a preferred selector and an acceptable fallback hook.
Alternatives considered: Key the effect off `.status-running` alone; rejected because `executionStatusPillClasses` groups `planning`, `initializing`, and `finalizing` with running today, while the shimmer contract is explicitly for executing only.
Test implications: Unit helper/CSS tests and entrypoint render tests.

## FR-003 / DESIGN-REQ-016 Executing-Only Isolation

Decision: Keep the shimmer effect opt-in and executing-only, even though the broader semantic class helper currently groups additional active states under `status-running`.
Evidence: `executionStatusPillClasses` maps `running`, `executing`, `planning`, `initializing`, and `finalizing` to `status-running`; no shimmer-specific selectors exist yet.
Rationale: The spec and source design say non-executing states must not inherit the shimmer accidentally.
Alternatives considered: Treat every `status-running` pill as shimmer-eligible; rejected because it would violate the executing-only state matrix.
Test implications: Unit and integration regression tests for non-executing states.

## FR-004 / FR-005 / DESIGN-REQ-004 Text and Layout Preservation

Decision: Implement the effect as additive shared status-pill styling, preferably with pseudo-elements, so text, icon choice, and pill dimensions remain unchanged.
Evidence: Current `.status` styling in `frontend/src/styles/mission-control.css` is a compact inline-flex pill, and list/detail entrypoints render plain text status inside the span without extra wrappers.
Rationale: The source design requires the shimmer to stay inside the pill bounds, preserve host content, and avoid measurable layout shift.
Alternatives considered: Nested decorative spans inside every status pill; rejected because it increases markup churn across surfaces and risks layout drift.
Test implications: CSS contract tests and entrypoint render tests.

## FR-006 / DESIGN-REQ-013 Reduced Motion

Decision: Use `prefers-reduced-motion: reduce` to disable animated shimmer movement and fall back to a static executing highlight that still reads as active.
Evidence: `frontend/src/styles/mission-control.css` already uses reduced-motion media queries for `MaskedConicBorderBeam`, but status pills have no equivalent reduced-motion shimmer behavior.
Rationale: CSS media queries keep the behavior deterministic, lightweight, and testable without adding runtime state machinery.
Alternatives considered: JavaScript `matchMedia` state in entrypoints; rejected because CSS alone is sufficient and less invasive for shared decorative behavior.
Test implications: CSS contract tests and render assertions for reduced-motion conditions.

## FR-007 / DESIGN-REQ-001 Calm Active Read

Decision: Derive the executing shimmer from existing Mission Control accent tokens and assert calm active behavior through CSS contract tests rather than introducing new warning-like colors or aggressive motion.
Evidence: `.status-running` already uses `--mm-accent-2`, and `docs/UI/EffectShimmerSweep.md` binds shimmer roles to existing MoonMind accent tokens.
Rationale: The story’s visual tone is “focused, intelligent, in-progress,” not warning or instability.
Alternatives considered: Add a warmer or higher-contrast warning pulse; rejected because it conflicts with the source design and existing design-system semantics.
Test implications: CSS contract unit tests.

## FR-009 Traceability

Decision: Preserve MM-488 in planning artifacts now and add explicit traceability assertions in frontend tests or exported constants during implementation.
Evidence: MM-488 currently appears in `spec.md` and the Jira orchestration input only; frontend code and tests do not reference it yet.
Rationale: Final verification needs the Jira issue key preserved beyond the spec alone.
Alternatives considered: Keep traceability only in spec artifacts; rejected because downstream verification requires evidence from implementation/test artifacts too.
Test implications: Unit traceability assertions.

## Repo Gap Summary

Decision: Treat MM-488 as a partial frontend enhancement with new CSS/helper work and new tests.
Evidence: Existing shared status-pill styling and render sites provide a reusable baseline; the shimmer selector contract, reduced-motion path, and shimmer-specific tests are absent.
Rationale: This supports a TDD-first tasks phase: add focused tests for selector contract and reduced-motion behavior, then implement the shared modifier conservatively.
Alternatives considered: Mark the story as entirely missing; rejected because shared pill infrastructure already exists and should be reused.
Test implications: Unit + integration.
