# Research: Calm Shimmer Motion and Reduced-Motion Fallback

## FR-001 / DESIGN-REQ-007 Executing Sweep Path And Bounds

Decision: Treat the existing shimmer as implemented but unverified for left-to-right travel and pill-bound clipping, then add MM-490-focused CSS and render tests before any fallback implementation changes.
Evidence: `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/styles/mission-control.css` defines `--mm-executing-sweep-start-x`, `--mm-executing-sweep-end-x`, `overflow: hidden`, and the `mm-status-pill-shimmer` keyframes; `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/mission-control.test.tsx` asserts selector and animation presence but not bounded travel semantics.
Rationale: The visible contract appears present, but the current proof is indirect and story-level verification is still missing.
Alternatives considered: Mark the requirement fully verified from existing CSS and tests; rejected because no current test ties the sweep path and clipping behavior to MM-490 acceptance evidence.
Test implications: Unit + integration.

## FR-002 / DESIGN-REQ-010 Cadence, Delay, And No-Overlap Timing

Decision: Classify the cadence requirement as partial and plan code plus tests.
Evidence: `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/styles/mission-control.css` defines `--mm-executing-sweep-duration: 1450ms` and `--mm-executing-sweep-delay: 220ms`, but the shimmer block uses `animation-delay` instead of a per-cycle repeat gap, and the keyframes do not reserve an idle segment that proves no overlap between cycles.
Rationale: The source design and Jira brief call for a total 1.6 to 1.8 second cadence including delay plus no overlap between cycles. The current CSS approximates parts of that profile but does not encode the cycle gap defensibly.
Alternatives considered: Treat the current duration plus initial delay as sufficient; rejected because that only delays the first cycle, not the repeat cadence.
Test implications: Unit + integration.

## FR-003 Center-Brightness Emphasis

Decision: Classify the brightest-at-center requirement as partial and plan code plus tests.
Evidence: `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/styles/mission-control.css` moves the gradients to `50%` / `62%` at `65%` progress, but the current opacity values are constant and there is no explicit midpoint emphasis or verification for centerline brightness.
Rationale: Positioning the sweep near center is not the same as making the brightest moment occur near the centerline. The story needs explicit contract evidence for midpoint emphasis.
Alternatives considered: Treat the current midpoint keyframe as sufficient; rejected because the visual intensity contract is not encoded or verified directly.
Test implications: Unit + integration.

## FR-004 / DESIGN-REQ-012 Reduced-Motion Static Fallback

Decision: Treat reduced-motion fallback as implemented but unverified for the full MM-490 story and add focused verification tests first.
Evidence: `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/styles/mission-control.css` disables shimmer animation under `@media (prefers-reduced-motion: reduce)` and keeps a fixed background position and size; `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/mission-control.test.tsx` asserts `animation: none` for the shimmer selector.
Rationale: The static treatment likely exists already, but current tests do not prove the replacement remains a clear active highlight rather than simply freezing an arbitrary frame.
Alternatives considered: Mark the requirement fully verified from the existing reduced-motion assertion; rejected because story-level fallback semantics are broader than animation suppression alone.
Test implications: Unit + integration.

## FR-005 Reduced-Motion Comprehension Without Motion

Decision: Treat the comprehension requirement as implemented but unverified and add verification tests before any implementation changes.
Evidence: Existing reduced-motion CSS preserves the executing status pill base and a fixed shimmer highlight, while task list and detail tests already render executing pills through the shared contract in `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/tasks-list.test.tsx` and `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/task-detail.test.tsx`.
Rationale: The product likely remains readable as active today, but no MM-490-specific test proves that reduced-motion users still receive an active-state cue without animation.
Alternatives considered: Treat text content alone as sufficient evidence; rejected because the story explicitly requires a static active highlight, not just surviving status text.
Test implications: Unit + integration.

## FR-006 / DESIGN-REQ-014 Executing-Only Activation Boundary

Decision: Treat the executing-only activation boundary as already implemented and verified.
Evidence: `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/utils/executionStatusPillClasses.ts` adds shimmer metadata only for `executing`; `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/utils/executionStatusPillClasses.test.ts`, `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/tasks-list.test.tsx`, and `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/task-detail.test.tsx` assert that non-executing states remain plain.
Rationale: The current helper and entrypoint coverage directly enforce the executing-only state matrix for the shared shimmer selector.
Alternatives considered: Re-test broader running-like states first; rejected because existing evidence already covers the key regression boundary this story owns.
Test implications: None beyond final verify.

## FR-007 MM-490 Traceability

Decision: Classify MM-490 traceability as missing and plan code plus tests.
Evidence: `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/utils/executionStatusPillClasses.ts` preserves `MM-488` and `MM-489`, but there is no `MM-490` reference in the current frontend traceability export or tests.
Rationale: Final verification requires the Jira issue key to appear in implementation and verification evidence, not just spec artifacts.
Alternatives considered: Keep MM-490 traceability only in spec and planning artifacts; rejected because neighboring shimmer stories already preserve Jira keys in runtime-adjacent evidence.
Test implications: Unit.

## Unit Test Strategy

Decision: Use focused Vitest CSS/helper coverage first, then the full repo unit suite.
Evidence: `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/mission-control.test.tsx` already parses and asserts shared Mission Control CSS, and `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/utils/executionStatusPillClasses.test.ts` already validates selector and traceability behavior.
Rationale: MM-490 primarily changes contract-level CSS timing and traceability. Those are best driven by focused unit-style assertions before broader execution.
Alternatives considered: Jump directly to the full unit suite; rejected because focused tests give faster red/green iteration and clearer requirement-level failures.
Test implications: Unit.

## Integration Test Strategy

Decision: Keep integration evidence explicit through Vitest entrypoint render tests for task list and task detail, then finish with `./tools/test_unit.sh`.
Evidence: `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/tasks-list.test.tsx` and `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/frontend/src/entrypoints/task-detail.test.tsx` already exercise the shared status-pill contract across supported surfaces.
Rationale: This story affects shared UI behavior, not backend orchestration or provider integration, so render-level frontend tests are the correct integration layer for the plan gate.
Alternatives considered: Compose-backed `integration_ci`; rejected because the story is isolated frontend behavior and the repo already treats these entrypoint render tests as the practical integration seam.
Test implications: Integration.

## Planning Tooling Constraint

Decision: Record the setup helper as blocked by managed branch naming and continue with manual artifact generation.
Evidence: Running `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/.specify/scripts/bash/setup-plan.sh --json` failed with `ERROR: Not on a feature branch. Current branch: mm-490-29fa2a3f`.
Rationale: The managed workspace branch does not match the setup script's `001-feature-name` expectation, but the active feature directory is already resolved through `/work/agent_jobs/mm:5a938de1-47ac-4e9f-8d8f-5bf1f56d6a4a/repo/.specify/feature.json`, so planning can proceed safely without regenerating artifacts.
Alternatives considered: Rename the branch or stop planning; rejected because branch mutation was not requested and the active feature directory is already known.
Test implications: None.

## Repo Gap Summary

Decision: Treat MM-490 as a mixed verification-and-implementation frontend story.
Evidence: The shared shimmer selector contract, executing-only routing, and reduced-motion suppression already exist in the frontend helper/CSS/test seams, but cadence/no-overlap timing, center-brightness emphasis, and MM-490 traceability are not fully encoded or proven.
Rationale: The next stage should start red with focused CSS/helper tests, then adjust the shared Mission Control shimmer contract only where those tests expose real gaps.
Alternatives considered: Mark the story fully implemented or fully missing; rejected because both would ignore meaningful existing work.
Test implications: Unit + integration.
