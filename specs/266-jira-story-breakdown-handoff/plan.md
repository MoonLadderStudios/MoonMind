# Implementation Plan: Jira Story Breakdown Handoff

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. The change keeps agent generation and deterministic Jira creation in their existing boundaries.
- IV Own Your Data: PASS. Artifact refs become a first-class handoff input before external GitHub fetches.
- V Skills Are First-Class: PASS. The executable `story.create_jira_issues` contract is clarified and tested.
- IX Resilient by Default: PASS. Protected source branches no longer masquerade as writable handoff branches, and failures become actionable.
- XI Spec-Driven Development: PASS. This feature artifact records the behavior change and test scope.
- XII Canonical Documentation: PASS. Long-lived docs describe desired contract behavior, while rollout notes stay in this feature directory.
- XIII Pre-Release Velocity: PASS. No compatibility shim is added; callers are updated to the stricter handoff behavior.

## Technical Approach

- Update `worker_runtime._build_runtime_planner()` so Jira story-output setup only treats explicit `targetBranch` as a writable handoff branch. If only `branch` is present, copy it to `startingBranch` and generate a new target branch.
- Extend `story.create_jira_issues` to resolve stories from direct inputs, previous step outputs, and `storyBreakdownArtifactRef` before falling back to repository path fetches.
- Wire the production story-output handler to an artifact reader backed by `TemporalArtifactService`.
- Improve the protected-branch failure path to explain the missing handoff payload.
- Update `docs/Tasks/SkillAndPlanContracts.md` and focused unit tests.

## Validation

- Run targeted unit tests:
  - `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`
  - `tests/unit/workflows/temporal/test_story_output_tools.py`
- Run full unit verification with `./tools/test_unit.sh`.
