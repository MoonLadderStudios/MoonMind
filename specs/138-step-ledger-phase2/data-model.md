# Data Model: Step Ledger Phase 2

## 1. StepLedgerEvidenceContext

Compact metadata carried from the parent step into child/runtime artifact publication.

| Field | Type | Notes |
| --- | --- | --- |
| `logicalStepId` | string | Stable plan-node ID from the parent step row |
| `attempt` | integer | Current latest-run attempt number for the logical step |
| `scope` | string or null | Optional bounded scope value; Phase 2 uses `"step"` when present |

Storage posture:

- carried in request/result metadata only
- mirrored into artifact metadata when available
- never stored as a separate workflow payload blob

## 2. StepLedgerRefs

Phase 1 already froze the shape. Phase 2 fills it.

| Field | Source |
| --- | --- |
| `childWorkflowId` | Parent launch context and/or child result lineage metadata |
| `childRunId` | Child `MoonMind.AgentRun` result lineage metadata |
| `taskRunId` | Managed-run/session observability metadata from the child runtime result |

## 3. StepLedgerArtifacts

Phase 2 populates the reserved slots below without changing the schema:

| Slot | Primary source | Fallback |
| --- | --- | --- |
| `outputSummary` | `outputSummaryRef` from child/runtime metadata | `null` |
| `outputPrimary` | explicit `outputPrimaryRef` | first unassigned durable `outputRefs[]`, then `outputAgentResultRef` |
| `runtimeStdout` | `stdoutArtifactRef` | session artifact metadata |
| `runtimeStderr` | `stderrArtifactRef` | session artifact metadata |
| `runtimeMergedLogs` | `mergedLogArtifactRef` | `null` |
| `runtimeDiagnostics` | `diagnosticsRef` | session artifact metadata |
| `providerSnapshot` | explicit provider snapshot ref | `null` |

## 4. Parent Result Grouping Inputs

The parent workflow groups evidence from compact result metadata only:

- top-level flattened metadata fields produced by `_map_agent_run_result()`
- `outputRefs[]`
- `diagnosticsRef` / `diagnostics_ref`
- nested `sessionSummary` / `sessionArtifacts` metadata for Codex session-backed runs

The parent does not read artifact bodies when populating the row.
