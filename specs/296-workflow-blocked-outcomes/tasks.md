# Tasks: Workflow Blocked Outcomes

- [X] T001 Diagnose `mm:9b25b452-8bbc-4a20-8901-966e793cea33` workflow history, agent artifacts, and workspace state.
- [X] T002 Confirm the failure was caused by a structured blocker report being ignored before publish handling.
- [X] T003 Add generic structured blocked-outcome detection in `moonmind/workflows/temporal/workflows/run.py`.
- [X] T004 Stop remaining plan nodes and mark them skipped when a blocked outcome is detected.
- [X] T005 Suppress PR publish failure and preserve the blocked terminal reason.
- [X] T006 Add unit tests for structured blocker parsing and blocked publish completion.
- [X] T007 Add unit test proving execution stops after a structured blocked outcome.
- [X] T008 Run focused and broader unit verification.
- [X] T009 Mark blocked parent runs as terminal failed instead of completed.
- [X] T010 Add unit coverage for blocked parent terminal-state mapping.
- [X] T011 Require PR-created evidence for `publishMode=pr` terminal success.
- [X] T012 Require successful merge evidence when merge automation is requested.
- [X] T013 Require final report artifact evidence when required report output is requested.
- [X] T014 Add unit coverage for final outcome success and failure gates.
