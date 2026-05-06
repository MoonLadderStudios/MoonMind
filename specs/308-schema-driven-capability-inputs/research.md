# Research: Schema-Driven Capability Inputs

## Repository Gap: Capability Contracts

Decision: Treat current behavior as partial and add a normalized preset/skill capability input contract while preserving legacy template input compatibility.
Evidence: `api_service/services/task_templates/catalog.py` serializes `inputs`; `frontend/src/entrypoints/task-create.tsx` consumes template `inputs`; governed MCP tools expose `inputSchema`.
Rationale: The selected story needs preset and skill schema/UI/default metadata, not only simplified template input rows or tool schema summaries.
Alternatives considered: Reusing only legacy template inputs was rejected because it cannot represent nested Jira issue objects, UI schema hints, or shared skill input contracts.
Test implications: Backend unit tests for serialization/validation plus frontend integration tests for rendered preset and skill inputs.

## Repository Gap: Schema Renderer

Decision: Add a shared schema-field rendering path for capability inputs with the documented supported schema subset.
Evidence: Create page renders legacy template inputs via `TaskTemplateInputDefinition`; no generic renderer for JSON Schema `properties`, nested objects, arrays, or widget metadata was found.
Rationale: A schema renderer is the core user-visible behavior and unlocks new capabilities without custom forms.
Alternatives considered: Adding a Jira Orchestrate-specific form was rejected by the spec and docs.
Test implications: Frontend unit tests for each required schema construct and integration tests for selection through preset/skill details.

## Repository Gap: Widget Registry And Jira Picker

Decision: Add an allowlisted widget registry and implement `jira.issue-picker` as the first complex reusable widget for capability inputs.
Evidence: Existing Jira preset uses a `jira_issue_key` text input; docs require `jira.issue-picker` from metadata.
Rationale: The story explicitly validates widget selection by metadata, manual key preservation, and safe issue value storage.
Alternatives considered: Keeping Jira as a text input was rejected because it does not prove reusable widget metadata or enrichment behavior.
Test implications: Frontend tests for uiSchema and x-moonmind-widget paths, manual entry, unavailable lookup, and unsupported widget fallback/error behavior.

## Repository Gap: Validation And Errors

Decision: Add field-addressable validation for schema-backed capability inputs and map backend validation errors to generated fields.
Evidence: Backend template validation handles simplified required inputs; spec requires nested paths such as preset input object fields.
Rationale: Without path-aware errors, users cannot fix generated schema fields reliably.
Alternatives considered: Form-level error messages only were rejected because acceptance scenarios require errors next to the relevant input.
Test implications: Python unit tests for error shape and frontend integration tests for blocking preview/apply/submit with preserved draft values.

## Security Boundary

Decision: Treat schema/defaults/UI metadata as untrusted data and keep Jira credential resolution inside trusted Jira services only.
Evidence: `moonmind/integrations/jira/auth.py`, Jira MCP registry, and Jira service tests establish trusted-tool boundaries; no story-specific schema-default regression exists.
Rationale: Schema-driven inputs must not become a new path for secrets into drafts, artifacts, or agent-visible payloads.
Alternatives considered: Passing Jira auth state through frontend inputs was rejected by existing security design.
Test implications: Unit/integration security regression for secret-like defaults, safe Jira issue values, and absence of raw credentials in payload artifacts covered by this story.

## Testing Strategy

Decision: Use red-first focused Vitest/Testing Library coverage for Create-page behavior and pytest coverage for backend contract normalization/validation; run full `./tools/test_unit.sh` before completion.
Evidence: Existing repo guidance requires `./tools/test_unit.sh`; Create-page tests already exercise task-template detail and expansion paths.
Rationale: The story crosses UI and backend boundaries; separate unit and integration-style tests are needed before implementation.
Alternatives considered: Manual browser-only validation was rejected because this is a contract-heavy Create-page behavior.
Test implications: Unit: Python schema/validation and focused frontend renderer/widget tests. Integration: Create-page preset/skill selection payload tests and template catalog route/service tests.

