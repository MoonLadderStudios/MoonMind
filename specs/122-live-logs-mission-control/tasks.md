# Tasks

- [x] T001 Persist the owning logical workflow id on managed run records and add store lookup helpers that prefer the latest active run for a workflow
- [x] T002 Move task-run binding persistence to after successful managed run record save and pass the parent logical workflow id through the managed launch path
- [x] T003 Make `/api/executions/{workflowId}` derive `taskRunId` from the durable managed-run store when memo/search attributes do not already provide it
- [x] T004 Align `/api/task-runs/*` observability authorization with execution ownership for admins and owning users
- [x] T005 Update the React task-detail observability state model to distinguish waiting, launch-failed, binding-missing, and authorization-failed states and to auto-attach once `taskRunId` appears
- [x] T006 Add unit and integration coverage, including simulated long-running stream validation and browser-facing delayed-`taskRunId` attach tests
- [x] T007 Update `docs/tmp/009-LiveLogsPlan.md` to reflect the actual completion state after the implementation and tests land
