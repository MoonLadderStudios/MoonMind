# Research: Step Ledger Phase 2

## Decision 1: Group evidence in the parent workflow from compact result metadata

- **Decision**: Keep the parent `MoonMind.Run` workflow responsible for translating child/activity result metadata into `refs` and `artifacts` slots on the step ledger.
- **Rationale**: The step ledger is the canonical live-state owner. Grouping evidence there lets later API/UI phases reuse the same latest-run contract without adding a second grouping layer first.
- **Alternatives considered**:
  - Group evidence only in a later API service projection: rejected because Phase 2 is explicitly the workflow/runtime evidence phase and later layers should consume the same row shape unchanged.
  - Read artifact metadata inside the workflow to derive slots: rejected because it would add avoidable IO and coupling during execution.

## Decision 2: Enrich `MoonMind.AgentRun` results with lineage and task-run metadata before returning to the parent

- **Decision**: Attach `childWorkflowId`, `childRunId`, and best-available `taskRunId` metadata inside `MoonMind.AgentRun` before the parent consumes the result.
- **Rationale**: The child workflow is the authoritative source for its own workflow identity and the managed-run/session identifiers it orchestrates.
- **Alternatives considered**:
  - Infer child run identity only in the parent: rejected because the parent does not always have the child run instance ID after completion.

## Decision 3: Stamp step-scoped metadata on published agent-runtime artifacts using request-carried step context

- **Decision**: Carry compact step context (`logicalStepId`, `attempt`, `scope`) through `AgentExecutionRequest.parameters.metadata.moonmind.stepLedger`, then copy that into artifact metadata during `agent_runtime.publish_artifacts`.
- **Rationale**: The parent already knows the current logical step and attempt, and artifact publication already writes the canonical summary/result artifacts for later grouping and projection.
- **Alternatives considered**:
  - Derive step identity from artifact names: rejected because it is brittle and violates the explicit metadata requirement in the normative doc.

## Decision 4: Prefer explicit runtime refs, then deterministic fallback selection for `outputPrimary`

- **Decision**: Slot grouping should first use explicit refs from metadata (`outputSummaryRef`, stdout/stderr/merged/diagnostics refs, provider snapshot refs). If `outputPrimary` is still missing, choose the first durable output ref that is not already assigned to a runtime log or diagnostics slot, falling back to the agent-result artifact when necessary.
- **Rationale**: This keeps grouping deterministic without artifact hydration while still giving later consumers a useful primary output ref.
- **Alternatives considered**:
  - Leave `outputPrimary` empty unless a provider names it directly: rejected because it would make Phase 2 materially less useful for later API/UI work.
