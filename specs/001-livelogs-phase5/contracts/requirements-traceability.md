# Requirements Traceability: Phase 5

| DOC-REQ ID | Function Req | Implementation Surface | Validation Strategy |
|---|---|---|---|
| DOC-REQ-001 | FR3 | `moonmind/workflows/temporal/workflows/run.py` & UI | Verify the task run view no longer uses a terminal embed component for inputs |
| DOC-REQ-002 | FR1 | `ui/src/components/InterventionPanel.tsx` | Visual verification of separate UI component for controls |
| DOC-REQ-003 | FR2 | `ui/src/hooks/useInterventions.ts`, `proxy.py` | E2E integration tests sending signals via REST API |
| DOC-REQ-004 | FR4 | `moonmind/workflows/temporal/activities/` | Test verifying intervention adds to Temporal audit history or separate log table, not stdout |
| DOC-REQ-005 | FR5 | `ui/src/components/LiveLogs.tsx` | Confirm stream continues to display system messages properly |
| DOC-REQ-006 | FR1 | `ui/src/components/TaskDetail.tsx` | Verify UI text labels reflect observation vs control split |
| DOC-REQ-007 | FR3 | `moonmind/agents/managed/launcher.py` | Verify that removing tmate/pty backend attachment does not break interventions |
| DOC-REQ-008 | FR6 | `tests/integration/test_interventions.py` | Pytest ensuring signal delivery without active log stream connections |
