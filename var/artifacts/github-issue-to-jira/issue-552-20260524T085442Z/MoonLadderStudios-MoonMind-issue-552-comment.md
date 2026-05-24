Triaged this into Jira because the requested post-run review behavior is not fully implemented yet, and the remaining work is clear enough for backlog tracking.

Created Jira story: MM-733
https://moonladder.atlassian.net/browse/MM-733

Evidence from the current codebase:

| Requirement | Status | Evidence |
| --- | --- | --- |
| Review after failed or successful tasks | Not implemented | `moonmind/workflows/temporal/workflows/run.py` only runs `_run_proposals_stage()` when proposal generation is explicitly requested. |
| Diagnose failed tasks and propose a repair task | Partially implemented | Proposal delivery exists, but `moonmind/workflows/temporal/activity_runtime.py` `proposal_generate()` depends on an explicit proposal idea and does not inspect terminal failure evidence by itself. |
| Check successful tasks for real completeness | Not implemented | `moonmind/workflows/temporal/activities/step_review.py` currently returns `FULLY_IMPLEMENTED` unconditionally and has a TODO for the actual LLM review call. |
| Add follow-up work when success is incomplete | Partially implemented | Task proposal infrastructure exists, but `tests/integration/workflows/temporal/workflows/test_run.py` confirms proposal generation only runs with `task.proposeTasks` enabled. |

I did not close this GitHub issue because the behavior still needs product implementation. No product code was changed during this triage.
