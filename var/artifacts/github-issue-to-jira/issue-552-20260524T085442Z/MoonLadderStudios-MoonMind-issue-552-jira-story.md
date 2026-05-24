# Story: Add automatic post-run review and follow-up proposal generation

## User story
As a MoonMind operator, I want every completed or failed task to go through an automated review step so that failures produce actionable repair proposals and successful runs are checked for completeness before being treated as done.

## Background / source GitHub issue
- Repository: MoonLadderStudios/MoonMind
- GitHub issue: https://github.com/MoonLadderStudios/MoonMind/issues/552
- Issue title: After tasks fail or are considered successful, there should be a review step
- Requested behavior: after tasks fail or are considered successful, review the result; failed tasks should diagnose why they failed and propose a task to fix the root cause; successful tasks should be checked for actual completeness and, when incomplete, additional work should be proposed or queued.

## Current codebase findings
- `moonmind/workflows/temporal/workflows/run.py` has a best-effort proposal phase, but `_run_proposals_stage()` only runs when proposal generation is requested by the run payload.
- `moonmind/workflows/temporal/workflows/run.py` gates proposal generation through `_proposal_generation_requested()`, which requires `task.proposeTasks` or legacy root `proposeTasks`.
- `moonmind/workflows/temporal/activity_runtime.py` `proposal_generate()` builds a follow-up proposal only when an explicit proposal idea can be resolved; it does not review terminal success or failure evidence by itself.
- `moonmind/workflows/temporal/activities/step_review.py` is currently a placeholder: it returns `FULLY_IMPLEMENTED` with confidence `1.0` and contains a TODO to wire the LLM call.
- `tests/unit/workflows/temporal/test_step_review_activity.py` asserts the placeholder behavior.
- `tests/integration/workflows/temporal/workflows/test_run.py` covers proposal invocation only when `task.proposeTasks` is true and confirms no proposal activity runs when proposal opt-in is absent.

## Acceptance criteria
1. Every `MoonMind.Run` terminal path for success and failure records a post-run review decision or an explicit skipped reason before the final execution summary is published.
2. Failed runs pass structured failure diagnostics, failed step identity, relevant artifact/log refs, task instructions, and publish/proposal context into the post-run review.
3. Successful runs pass task instructions, step ledger/result evidence, publish status, generated artifacts, and completion summary into the post-run review.
4. The review can classify at least: complete, incomplete_follow_up_needed, failure_fix_needed, review_blocked, and review_not_applicable.
5. For failure_fix_needed, MoonMind creates or delivers a repair task proposal that includes the diagnosed cause, evidence refs, affected step, and concrete repair instructions.
6. For incomplete_follow_up_needed, MoonMind creates or delivers a continuation task proposal or appends additional planned work according to the existing task/proposal contract chosen by implementation design.
7. Post-run review must not silently mark incomplete work as successful when the reviewer cannot inspect required evidence; it should record review_blocked with actionable diagnostics.
8. Proposal creation remains idempotent for workflow retries and does not create duplicate repair or continuation proposals for the same workflow/run/review verdict.
9. Mission Control execution details expose the post-run review status and proposal/delivery outcome without requiring operators to inspect raw workflow history.
10. Tests cover success-complete, success-incomplete, failed-with-repair-proposal, review-blocked, proposal idempotency, and the existing proposal-disabled behavior.

## Implementation notes
- Reuse the existing proposal infrastructure where possible: `proposal.generate`, `proposal.submit`, `TaskProposalService`, and existing proposal delivery status surfaces.
- Replace or extend the placeholder `step.review` behavior with a real post-run review boundary rather than relying on the current unconditional `FULLY_IMPLEMENTED` verdict.
- Preserve Temporal compatibility for in-flight workflow payloads; if workflow/activity signatures change, add boundary or replay-style coverage as required by repo policy.
- The review should be best-effort only if that is an explicit product decision; otherwise terminal completion should visibly reflect review failure or blocked review state.

## Verification
- Add unit tests for review verdict parsing and proposal payload creation.
- Add Temporal workflow boundary tests proving review runs after terminal success and failure paths.
- Add integration or API serialization coverage showing review status and proposal outcomes in execution detail payloads.
- Run `./tools/test_unit.sh` and targeted integration tests for proposal/review workflow boundaries.

## Out of scope
- Reworking unrelated proposal queue UI flows.
- Creating provider-specific reviewer logic outside the existing runtime/tool adapter boundaries.
- Changing external provider billing/model semantics beyond passing configured runtime values through existing validation.
