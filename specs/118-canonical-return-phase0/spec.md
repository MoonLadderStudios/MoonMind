# Canonical Return Phase 0 (Scope and Strategy)

## Problem Description
The external and managed runtime agent execution loop inside MoonMind currently repairs provider-shaped payloads. The objective is to move all provider-specific and runtime-specific normalization out of the workflow entirely and back into the adapter/activity boundary so `MoonMind.AgentRun` and `MoonMind.Run` only process canonical Pydantic structures (`AgentRunHandle`, `AgentRunStatus`, `AgentRunResult`).

Phase 0 sets the stage for this refactor by identifying the exact workflow coercion functions, pinpointing which activity handlers need to adapt, and stipulating the safe in-flight cutover strategy.

## Scope
1. **Inventory**: Gather all workflow coercions and legacy activity handlers emitting mixed formats.
2. **Compatibility Rules**: Declare the exact method for maintaining Temporal playback replay safety without cluttering the workflow with long-term compatibility wrappers.

## Requirements
- **DOC-REQ-CANON-001**: Establish a complete list of `MoonMind.AgentRun` and `MoonMind.Run` normalization points that must be removed.
- **DOC-REQ-CANON-002**: Identify all provider and runtime integrations that emit non-canonical payload dicts.
- **DOC-REQ-CANON-003**: Formalize the use of `workflow.patched` for in-flight history execution of active agents, or declare an explicit cutover.
- **DOC-REQ-CANON-004**: Record findings directly in [`010-CanonicalReturnPlan.md`](../../docs/tmp/010-CanonicalReturnPlan.md) or a structured plan document.
