# Feature Specification: Claude Runtime Enabled by API Key

**Feature Branch**: `027-claude-api-key-gate`  
**Created**: February 24, 2026  
**Status**: Active  

## User Goals

- Prevent Claude runtime use unless `ANTHROPIC_API_KEY` (or `CLAUDE_API_KEY`) is configured.
- Hide Claude from the task UI runtime selector when the API key is missing.
- Reject API requests that still send Claude runtime while the key is unavailable.

## Requirement

When `ANTHROPIC_API_KEY` is empty, `claude` is not considered an available task runtime.

When `ANTHROPIC_API_KEY` is present, `claude` is available under existing `claude`-capable workers.

Tasks that resolve to `targetRuntime=claude` must be rejected before queue persistence unless the key is available.

## Functional Requirements

1. **Runtime availability**
   - `supportedTaskRuntimes` returned to the dashboard must only include `claude` when the key is configured.
   - The server-side default runtime should never resolve to `claude` when the key is missing.
2. **Queue validation**
   - Any normalized task with runtime `claude` must fail with a 400-style validation error message:
     - `targetRuntime=claude requires ANTHROPIC_API_KEY to be configured`
3. **Operator docs**
   - Quickstart should instruct operators to configure `ANTHROPIC_API_KEY` for Claude runtime.
   - Remove OAuth-based Claude bootstrap instructions from documentation.

## Acceptance

- With no key configured:
  - Dashboard `supportedTaskRuntimes` excludes `claude`.
  - Queue API rejects `targetRuntime=claude`.
  - README no longer advertises legacy Claude auth workflows.
- With key configured:
  - Dashboard and queue docs describe Claude as available.
  - Optional `claude` worker profile can be started through compose profile flow.
