# Canonical Return Phase 0 (Plan)

Following the spec requirements and the strategy outlined in [`010-CanonicalReturnPlan.md`](../../docs/tmp/010-CanonicalReturnPlan.md), we catalog the exact normalization touchpoints and clarify our deployment/playback constraints.

## 1. Inventory

### Workflow-side normalization points to remove (DOC-REQ-CANON-001)
- `MoonMind.AgentRun._coerce_external_status_payload` (`agent_run.py`)
- `MoonMind.AgentRun._coerce_external_start_status` (`agent_run.py`)
- `MoonMind.AgentRun._coerce_managed_status_payload` (`agent_run.py`)
- `MoonMind.AgentRun` inline special-case handling for non-canonical external start dicts (`agent_run.py`)
- `MoonMind.Run._map_agent_run_result` (`run.py`)

### Activity handlers emitting mixed shapes (DOC-REQ-CANON-002)
External provider activities mapping:
- `integration.codex_cloud.[start|status|fetch_result|cancel]`
- `integration.jules.[start|status|fetch_result|cancel]`
- `oauth_session_*` operations (status updates)

Managed runtime activities mapping:
- `agent_runtime.launch`
- `agent_runtime.status`
- `agent_runtime.fetch_result`
- `agent_runtime.publish_artifacts`
- `agent_runtime.cancel`

## 2. In-Flight Cutover Strategy & Compatibility (DOC-REQ-CANON-003)

Because removing coercion functions directly mutates the workflow's handling mechanism, replaying old recorded Temporal events containing raw external schemas would lead to `NonDeterministicWorkflowError` or strict Pydantic parsing failures.

The approved cutover strategy demands:
- Using `workflow.patched("canonical_agent_run_returns_v1")` inline.
- The default behavior acts upon the strictly typed canonical payloads (`AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`).
- Legacy compatibility behavior (patched out) must maintain the existing code path for running tasks that have not completed.
- We deliberately refuse soft wrapper aliases outside of the patched boundary to keep adherence to MoonMind's pre-release policy tight. New activity loops or executions will strictly reject non-typed dictionary returns directly.

## 3. Plan Modifications (DOC-REQ-CANON-004)
We append these specific deliverables directly into [`010-CanonicalReturnPlan.md`](../../docs/tmp/010-CanonicalReturnPlan.md) and mark `Phase 0` complete.
