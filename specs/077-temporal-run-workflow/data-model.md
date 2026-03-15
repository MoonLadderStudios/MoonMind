# Phase 1: Design & Contracts

## Entities

### `MoonMind.Run` (Temporal Workflow)
- **State/Phases:** initializing, planning, executing, awaiting_external, finalizing
- **Inputs:** `RunWorkflowInput` (workflow type, parameters, etc.)
- **Outputs:** `RunWorkflowOutput` (final status, artifact references)
- **Search Attributes:**
  - `mm_state`: Current phase
  - `mm_owner_type`: Owner entity type
  - `mm_owner_id`: Owner entity ID
  - `mm_repo`: Repository name
  - `mm_integration`: Current active integration (if any)

### Artifact Reference
- **Fields:** `artifact_id`, `storage_path`, `size`, `type`
- **Purpose:** Passed between activities and stored in workflow history to avoid large payloads.
