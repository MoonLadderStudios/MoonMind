# Research: Shimmer Quality Regression Guardrails

## FR-001 / SCN-001 / SC-001 / SC-002 / DESIGN-REQ-004 / DESIGN-REQ-009 Executing Readability, Bounds, And Scrollbar Safety

Decision: Treat executing readability, clipping, and scrollbar isolation as implemented but unverified and add focused CSS plus supported-surface verification before any implementation change.
Evidence: `frontend/src/styles/mission-control.css` applies the shimmer only through the shared executing selector, keeps `overflow: hidden`, and uses background layers rather than extra DOM wrappers; `frontend/src/entrypoints/mission-control.test.tsx` already asserts the shared shimmer selector block, cycle tokens, and reduced-motion media query; current tests do not prove text readability at sampled sweep points or explicitly tie scrollbar non-interaction to MM-491.
Rationale: The shared shimmer implementation appears aligned with the design, but the evidence is indirect. MM-491 exists to harden proof around readability and bounded behavior, so tests should lead and implementation should only move if the new assertions fail.
Alternatives considered: Mark the requirement fully verified from existing CSS and selector tests; rejected because story-level readability and scrollbar-safety proof is still missing.
Test implications: Unit + integration.

## FR-002 / SCN-002 / SC-003 / DESIGN-REQ-014 Full State-Matrix Isolation

Decision: Treat executing-only activation as implemented but unverified for the full source state matrix and expand helper/render coverage first.
Evidence: `frontend/src/utils/executionStatusPillClasses.ts` returns shimmer metadata only for `executing`; `frontend/src/utils/executionStatusPillClasses.test.ts`, `frontend/src/entrypoints/tasks-list.test.tsx`, and `frontend/src/entrypoints/task-detail.test.tsx` already prove selected non-executing states stay plain; no current MM-491 test enumerates every listed non-executing state from `docs/UI/EffectShimmerSweep.md` in one regression set.
Rationale: The helper logic is already strict, but the story requires broad regression protection across the declarative design's state matrix, not just spot checks.
Alternatives considered: Mark the state matrix fully verified from current targeted non-executing examples; rejected because the full matrix is broader than the current evidence.
Test implications: Unit + integration.

## FR-003 / SCN-003 / SC-004 Layout Stability

Decision: Classify layout stability as partial and plan verification tests first with a CSS/markup contingency only if they fail.
Evidence: The shimmer is implemented as a background overlay on the existing pill class in `frontend/src/styles/mission-control.css`, which suggests stable layout, but there is no current assertion that compares pill dimensions or nearby layout before and after activation.
Rationale: Background-only styling usually preserves footprint, but MM-491 specifically exists to catch regressions. That makes missing layout-stability evidence a real planning gap.
Alternatives considered: Assume no layout shift because the current implementation uses backgrounds rather than wrappers; rejected because the story needs explicit guardrails rather than inference.
Test implications: Unit + integration.

## FR-004 / SCN-004 / SC-005 Theme Intent

Decision: Treat theme intent as implemented but unverified and add theme-aware shimmer assertions before changing styles.
Evidence: `frontend/src/entrypoints/mission-control.test.tsx` already proves shared token swaps for light and dark themes, and the shimmer CSS derives its band and halo colors from shared MoonMind accent tokens; no existing story-level evidence proves the executing shimmer reads as an intentional active treatment in both themes.
Rationale: Theme token presence is necessary but not sufficient for MM-491. The story needs explicit proof that the active treatment remains intentional and not accidental or washed out in either theme.
Alternatives considered: Mark theme intent fully verified from token coverage alone; rejected because token existence does not verify the executing shimmer treatment itself.
Test implications: Unit + integration.

## FR-005 / SCN-005 / SC-006 / DESIGN-REQ-011 Reduced-Motion Active Fallback

Decision: Treat reduced-motion fallback as implemented but unverified and add focused fallback assertions first.
Evidence: `frontend/src/styles/mission-control.css` disables animation under `@media (prefers-reduced-motion: reduce)` and preserves a fixed highlighted background; `frontend/src/entrypoints/mission-control.test.tsx` asserts `animation: none` for the executing shimmer selector; existing list/detail tests render executing pills but do not directly prove the fallback remains a clear active cue.
Rationale: The reduced-motion mechanics appear present, but MM-491 requires proof that the non-animated state still reads as active rather than simply becoming inert.
Alternatives considered: Treat animation suppression alone as sufficient; rejected because the story explicitly requires a static active fallback, not just disabled motion.
Test implications: Unit + integration.

## FR-006 / DESIGN-REQ-016 Non-Goal Preservation And Effect-Family Guardrails

Decision: Treat non-goal preservation as implemented but unverified and encode it through contract-level regression assertions and final verification evidence.
Evidence: The shared shimmer contract in `frontend/src/styles/mission-control.css` remains a layered background treatment on existing status pills, and adjacent shimmer stories already scoped the effect family; there is no dedicated MM-491 coverage that would fail if a future change replaced the shimmer with a different animated effect while still toggling on `executing`.
Rationale: MM-491 is the right place to anchor tests and verification notes that preserve the intended shimmer model instead of silently accepting any alternate animated treatment.
Alternatives considered: Leave non-goals to manual review only; rejected because the story is specifically about regression guardrails.
Test implications: Unit + integration.

## FR-007 / SC-007 MM-491 Traceability Surface

Decision: Classify MM-491 traceability as missing and plan a small helper/test update.
Evidence: `frontend/src/utils/executionStatusPillClasses.ts` currently exposes `jiraIssue: 'MM-488'` and `relatedJiraIssues: ['MM-489', 'MM-490']`; corresponding tests assert MM-488/MM-489/MM-490 but do not mention MM-491.
Rationale: Final verification needs MM-491 to appear in runtime-adjacent evidence, and traceability is the one clearly missing implementation item rather than just a coverage gap.
Alternatives considered: Keep MM-491 only in Moon Spec artifacts; rejected because neighboring shimmer stories already preserve Jira traceability in runtime-adjacent code/test evidence.
Test implications: Unit.

## Unit Test Strategy

Decision: Use focused Vitest CSS/helper assertions first, then finish with the full repo unit suite.
Evidence: `frontend/src/entrypoints/mission-control.test.tsx` already parses the shared Mission Control CSS contract, and `frontend/src/utils/executionStatusPillClasses.test.ts` already verifies executing-only selector behavior and Jira traceability exports.
Rationale: MM-491 is primarily a regression-proof story. Contract-level CSS/helper tests are the fastest way to go red first and to localize failures before considering any implementation changes.
Alternatives considered: Jump straight to `./tools/test_unit.sh`; rejected because the focused tests map requirements more directly and shorten iteration.
Test implications: Unit.

## Integration Test Strategy

Decision: Keep integration evidence explicit through Vitest entrypoint render tests for task list and task detail, then run `./tools/test_unit.sh`.
Evidence: `frontend/src/entrypoints/tasks-list.test.tsx` and `frontend/src/entrypoints/task-detail.test.tsx` already exercise the shared executing shimmer contract across supported list/card/detail surfaces.
Rationale: The story affects shared UI behavior, not backend orchestration or provider integration, so render-level frontend tests are the correct integration seam for the plan gate.
Alternatives considered: Compose-backed `integration_ci`; rejected because this story is isolated frontend behavior and the repo already treats these entrypoint render tests as the practical integration layer.
Test implications: Integration.

## Planning Tooling Constraint

Decision: Record the setup helper as blocked by managed branch naming and continue with manual artifact generation.
Evidence: `./.specify/scripts/bash/setup-plan.sh --json` failed with `ERROR: Not on a feature branch. Current branch: mm-491-d125a4e3`.
Rationale: The managed workspace branch does not match the setup helper's `001-feature-name` expectation, but the active feature directory is already known and planning can proceed safely without regenerating artifacts.
Alternatives considered: Rename the branch or stop planning; rejected because branch mutation was not requested and the feature directory already exists.
Test implications: None.

## Repo Gap Summary

Decision: Treat MM-491 as a verification-first frontend story with one small known implementation gap for traceability.
Evidence: The shared shimmer selector, current motion profile, reduced-motion suppression, and selected surface coverage already exist in the repo, while full state-matrix coverage, layout-stability proof, theme-specific active-treatment proof, explicit reduced-motion fallback clarity, and MM-491 runtime-adjacent traceability are still missing or incomplete.
Rationale: The next stage should start red by strengthening coverage around the shared Mission Control shimmer contract, then apply only the smallest necessary CSS/helper changes if failing tests expose a defect.
Alternatives considered: Mark the story fully implemented or fully missing; rejected because the repo already contains substantial shimmer behavior that should be verified rather than reimplemented blindly.
Test implications: Unit + integration.
