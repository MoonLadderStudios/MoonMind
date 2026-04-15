# Temporal Activity Boundary Contract

## Shared Converter

All MoonMind-owned Temporal clients use `MOONMIND_TEMPORAL_DATA_CONVERTER`. Workers inherit that converter through the connected client.

## Activity Types Preserved

This feature does not rename activity type strings. The migrated contract covers:

- `integration.jules.start`
- `integration.jules.status`
- `integration.jules.fetch_result`
- `integration.jules.cancel`
- `integration.codex_cloud.start`
- `integration.codex_cloud.status`
- `integration.codex_cloud.fetch_result`
- `integration.codex_cloud.cancel`
- `agent_runtime.status`
- `agent_runtime.fetch_result`
- `agent_runtime.cancel`
- `agent_runtime.publish_artifacts`

## Workflow Call-Site Rules

- Start activities receive `AgentExecutionRequest`.
- External status/fetch/cancel activities receive `ExternalAgentRunInput`.
- Managed status activities receive `AgentRuntimeStatusInput`.
- Managed fetch-result activities receive `AgentRuntimeFetchResultInput`.
- Managed cancel activities receive `AgentRuntimeCancelInput`.
- Publish-artifacts receives `AgentRunResult` or `None`.
- Calls go through `execute_typed_activity` so overloads document the workflow-facing response type.

## Legacy Boundary Rules

Legacy dict payloads are accepted only at activity entry points. Accepted aliases validate into the canonical model immediately and are not propagated as business logic payloads.

## Workflow-Facing Responses

Workflows consume canonical MoonMind models: `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`. Provider-specific fields may appear only as bounded metadata after normalization.
