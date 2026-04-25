# Tasks: Task Details Edit and Rerun Actions

- [X] T001 Add failing API unit assertions for `canEditForRerun` on failed terminal tasks and absence on running update-input tasks.
- [X] T002 Add failing frontend assertions for **Edit task** and **Rerun** visibility/hrefs on failed Task Details records.
- [X] T003 Implement `canEditForRerun` in the Pydantic action capability model and executions API capability builder.
- [X] T004 Implement Task Details rendering from `canEditForRerun`, preserving `canUpdateInputs` behavior for running tasks.
- [X] T005 Implement edit-for-rerun route helper and route mode parsing for `/tasks/new?rerunExecutionId=:id&mode=edit`.
- [X] T006 Run targeted Python and frontend tests.
- [X] T007 Run final unit verification or document any blocker.
- [X] T008 Run `/speckit.verify` equivalent and record verification in `verification.md`.
