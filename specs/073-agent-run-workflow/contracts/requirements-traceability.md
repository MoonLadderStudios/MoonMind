# Requirements Traceability Matrix

| DOC-REQ ID   | Functional Requirement | Planned Implementation Surface | Validation Strategy |
|--------------|------------------------|--------------------------------|---------------------|
| DOC-REQ-001 | FR-001                 | `MoonMind.AgentRun` workflow definition in Temporal codebase (`services/temporal/workflows/agent_run.py`) | Unit/Integration testing of the Temporal workflow structure |
| DOC-REQ-002 | FR-002                 | Adapter routing logic inside `MoonMind.AgentRun` | Mock adapter unit tests verifying correct class invocation based on input |
| DOC-REQ-003 | FR-003                 | Wait phase using `asyncio.Event` and Temporal signals | Trigger signal in test and verify workflow progression |
| DOC-REQ-004 | FR-004                 | Exception catching and timeout handling | Supply timeout limits in tests and check state changes |
| DOC-REQ-005 | FR-005                 | Activity to call artifact storage API | Validate that artifact output payload matches expected format |
| DOC-REQ-006 | FR-006                 | `AgentRunResult` return structure | Test end-to-end to ensure the final payload maps exactly to `AgentRunResult` |
| DOC-REQ-007 | FR-007                 | `try/finally` block combined with Temporal's `Shield()` / `in_background()` | Cancel the workflow during wait, verify adapter `cancel` is called |
