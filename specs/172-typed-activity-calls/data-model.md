# Data Model: Typed Temporal Activity Calls

## Shared Temporal Data Converter

- Purpose: Single MoonMind import used by Temporal clients and workers for Pydantic-aware payload conversion.
- Validation: Must be identical to the converter passed into `Client.connect`.
- State transitions: None; this is a stateless contract object.

## External Run Identifier Request

- Purpose: Identifies one external provider run/session for status, fetch-result, and cancel activities.
- Fields:
  - `runId`: required nonblank canonical run/session identifier.
- Legacy boundary aliases:
  - `external_id`, `externalId`, and `run_id` validate into `runId` at public activity entry only.
- Validation:
  - Unknown fields are rejected.
  - Blank identifiers are rejected.

## Agent Runtime Status Request

- Purpose: Reads managed runtime status for one run.
- Fields:
  - `runId`: required nonblank run identifier.
  - `agentId`: optional agent/runtime identifier, default `managed`.
- Validation:
  - Unknown fields are rejected.
  - Legacy snake-case aliases validate at activity entry only.

## Agent Runtime Fetch Result Request

- Purpose: Reads and optionally publishes the terminal result for one managed run.
- Fields:
  - `runId`: required nonblank run identifier.
  - `agentId`: optional agent/runtime identifier, default `managed`.
  - `publishMode`: optional, one of `none`, `pr`, or `branch`.
  - `commitMessage`, `targetBranch`, `headBranch`: optional publish metadata.
  - `prResolverExpected`: optional boolean.
- Validation:
  - Unknown fields are rejected.
  - Unsupported publish mode fails request validation.

## Agent Runtime Cancel Request

- Purpose: Cancels one runtime run at the activity boundary.
- Fields:
  - `runId`: required nonblank run identifier.
  - `agentKind`: required runtime kind.
  - `agentId`: optional runtime identifier.
- Validation:
  - Unknown fields are rejected.
  - Blank identifiers are rejected.

## Canonical Agent Runtime Responses

- Purpose: Workflow-facing return contracts.
- Models:
  - `AgentRunHandle`
  - `AgentRunStatus`
  - `AgentRunResult`
- Validation:
  - Provider-shaped dictionaries must be converted into these models before workflow logic stores or branches on them.
