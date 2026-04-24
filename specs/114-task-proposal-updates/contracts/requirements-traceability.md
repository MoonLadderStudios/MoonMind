# Requirements Traceability

This document traces normative source requirements (`DOC-REQ-*`) from `docs/Tasks/TaskProposalSystem.md` directly into the planned implementation surfaces and corresponding validation strategies.

| Requirement ID | Mapped Functional Requirement | Planned Implementation Surface | Validation Strategy |
| --- | --- | --- | --- |
| **DOC-REQ-001** | FR-001 | `TaskProposalPolicy` in `moonmind/workflows/tasks/task_contract.py` | Add validation field testing against `SUPPORTED_EXECUTION_RUNTIMES`. Tested in `test_run_artifacts.py` and canonical payload validation. |
| **DOC-REQ-002** | FR-002 | `moonmind/workflows/tasks/proposals.py`, `moonmind/api/routes/proposals.py` | Align API inputs and DB payloads to match `CanonicalTaskPayload` creation flow. Assert output matches during integration endpoint tests. |
| **DOC-REQ-003** | FR-003 | `moonmind/workflows/tasks/proposals.py`, `CanonicalTaskPayload` | Refactor generation layer to drop `agent_runtime`. Unit test ensures proposal model enforces proper schema. |
| **DOC-REQ-004** | FR-004 | `moonmind/workflows/temporal/workflows/run.py` | Assign raw `TaskProposalPolicy` values into `initialParameters` and remove destructive destructuring blocks. Validated in Temporal workflow testing. |
| **DOC-REQ-005** | FR-005 | `moonmind.workflows.temporal.workflows.run.py`, `MoonMind.Run` payload injection | Assure metadata assignment specifically crafts exact `origin.source = "workflow"` (with additional details in `origin_metadata`). Tested via `test_proposal_stage` boundaries. |
| **DOC-REQ-006** | FR-006 | `moonmind/api/routes/proposals.py`, `moonmind.api.models.proposals` | Expand API to emit `promoted_execution_id`. Integration test exercises `POST /api/proposals/{id}/promote` returning linkage. |
| **DOC-REQ-007** | FR-007 | `moonmind/workflows/temporal/workflows/run.py` | Intercept execution if toggle fails inside `_run_proposals_stage`. Unit test workflow boundary flags. |
| **DOC-REQ-008** | FR-004, FR-008 | `moonmind/workflows/temporal/workflows/run.py` | Remove fallback on `proposalTargets`, assert usage of strictly matched policy overrides inside execution logic. Workflow history simulation. |
| **DOC-REQ-009** | FR-008 | `moonmind/workflows/tasks/proposals.py`, `_run_proposals_stage` in workflow | Extract merging resolution directly from within `proposal.submit` / workflow boundaries instead of scattered logic. Check payload defaults via unit spec execution mock. |
| **DOC-REQ-010** | FR-009 | `moonmind/workflows/tasks/proposals.py`, `submit_proposal` activity | Force-inject `defaultRuntime` strictly when `candidate.task.runtime` lacks definitions. Check activity payload outputs. |
| **DOC-REQ-011** | FR-010 | `MoonMind.Run` Finalization Stage (`run.py`) | Accurately pull aggregated proposal counts ensuring valid outputs populate workflow execution summary. Verified by `RunArtifacts` testing outputs. |

> All traceability guarantees will be independently verified during testing via `test_unit.sh` boundary coverage mapping in addition to explicit workflow simulations.
