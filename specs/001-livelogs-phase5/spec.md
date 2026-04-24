# Specification: Fully implement Phase 5 from docs/ManagedAgents/LiveLogs.md

## High-Level Objective
Decouple task run observation (live logs) from task run intervention. Establish explicit, separate channels for executing operator controls (such as pause, resume, cancel, or approve) apart from the log streaming surface, ensuring logs remain purely passive observability surfaces.

## Source Document Requirements
- **DOC-REQ-001**: **Citation**: `Phase 5 - Tasks`. **Requirement**: Remove any backend/frontend assumptions that a live log session provides or implies shell access or operator control.
- **DOC-REQ-002**: **Citation**: `Phase 5 - Tasks`. **Requirement**: Define and implement a separate Intervention panel or control surface in the UI outside of the log viewer.
- **DOC-REQ-003**: **Citation**: `Phase 5 - Tasks`. **Requirement**: Ensure specific intervention actions (Pause, Resume, Cancel, Approve, Reject, Send Message) route strictly through Temporal signals/updates or native provider adapters, bypassing any legacy terminal transport.
- **DOC-REQ-004**: **Citation**: `Phase 5 - Tasks`. **Requirement**: Intervention actions must be audited/logged separately from the stdout/stderr stream.
- **DOC-REQ-005**: **Citation**: `Phase 5 - Tasks`. **Requirement**: Ensure the live log viewer preserves the ability to surface inline system annotations but remains free of interactive control functionality.
- **DOC-REQ-006**: **Citation**: `Phase 5 - Tasks`. **Requirement**: Update UI language to clearly separate "Observation" (Logs) from "Control" (Intervention Panel).
- **DOC-REQ-007**: **Citation**: `Phase 5 - Exit criteria`. **Requirement**: Managed-run operator actions must not depend on a terminal embed or an attached log session.
- **DOC-REQ-008**: **Citation**: `Phase 5 - Tasks`. **Requirement**: Add tests verifying intervention actions do not require a live log connection.

## Functional Requirements
1. The Task Detail view MUST completely separate the Intervention UI from the Live Logs observability UI (SATISFIES DOC-REQ-002, DOC-REQ-006).
2. The UI controls for Pause, Resume, Cancel, Approve, Reject, and Send Message MUST invoke MoonMind's execution/intervention backend endpoints (e.g. Temporal signals) instead of writing to a streaming input terminal (SATISFIES DOC-REQ-003).
3. The system MUST remove legacy assumptions that rely on a pty/interactive session to trigger state changes in managed runs (SATISFIES DOC-REQ-001, DOC-REQ-007).
4. System interventions MUST trigger separate audit trails or status timeline events and MUST NOT be blindly prepended/appended into the raw stdout/stderr artifact logs (SATISFIES DOC-REQ-004).
5. The Live log viewer MUST be capable of displaying system timeline annotations without enabling operator input (SATISFIES DOC-REQ-005).
6. Test suites MUST include tests verifying that issuing intervention controls succeeds without active log stream connections (SATISFIES DOC-REQ-008).

## User Scenarios & Testing
*Scenario 1: Pausing/Resuming a task*
Given a user is observing an active task run,
When they click "Pause",
Then the intervention panel invokes the correct backend Temporal API to pause the agent logic,
And the pause action is recorded in the run's audit log/system annotations, independently of any terminal log stream state.

*Scenario 2: Lack of terminal input reliance*
Given an active log viewer,
When the operator tries to blindly type "cancel" or other commands into the log stream window,
Then the stream window does not capture interactive input because it is purely an observability surface,
And they must use the explicit "Cancel" button in the intervention controls.

## Success Criteria
- 100% of Intervention features (Pause, Cancel, message sending, etc.) function correctly without any live log session connection.
- Elimination of terminal embed interactive inputs from the task run view.
- Audit history transparently documents operator interventions without injecting operator prompt input into standard output.

## Assumptions
- Base Temporal signaling/update infrastructure (via `proxy.py` and `moonmind/workflows`) exists and can route these explicitly defined actions for managed agents.
