# Tasks: Manual Dependency Wait Bypass

**Input**: `specs/213-manual-dependency-bypass/spec.md`

## Implementation

- [X] T001 Add `BypassDependencies` to the supported Temporal signal contract.
- [X] T002 Expose `canBypassDependencies` only for runs in `waiting_on_dependencies`.
- [X] T003 Implement `MoonMind.Run` dependency bypass handling that records `bypassed` metadata and clears unresolved prerequisites.
- [X] T004 Record an operator intervention audit entry when the bypass signal is submitted through the execution service.
- [X] T005 Render an explicit confirmed Dependency panel action in task detail.

## Verification

- [X] T006 Add workflow-level unit coverage for bypassing an active dependency wait.
- [X] T007 Add service coverage for sending the bypass signal and recording audit metadata.
- [X] T008 Add API serialization coverage for `canBypassDependencies`.
- [X] T009 Add task-detail UI coverage for the bypass signal request.
- [X] T010 Run focused tests and final unit verification.
