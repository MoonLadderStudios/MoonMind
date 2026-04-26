# Research: Typed Deployment Update Tool Contract

## FR-001 / DESIGN-REQ-001 - Canonical Tool Name And Version

Decision: Add a shared deployment tool contract helper that exposes `DEPLOYMENT_UPDATE_TOOL_NAME`, `DEPLOYMENT_UPDATE_TOOL_VERSION`, and a registry payload builder.
Evidence: `api_service/services/deployment_operations.py` currently hardcodes `deployment.update_compose_stack`; `moonmind/workflows/skills/tool_plan_contracts.py` already parses versioned `ToolDefinition` payloads.
Rationale: A shared helper prevents the API queued-run path and registry tests from diverging.
Alternatives considered: Keeping strings inline was rejected because MM-519 requires a canonical registry contract.
Test implications: Unit tests for payload parsing and API queued-run references.

## FR-002 / FR-008 - Strict Input Schema

Decision: Use JSON Schema `additionalProperties: false` at the root and `image` object levels, with enums for stack and mode.
Evidence: `plan_validation.py` enforces `additionalProperties: false`, required fields, types, and enum values during plan validation.
Rationale: Strict schema validation blocks arbitrary shell snippets, Compose paths, host paths, runner image overrides, and unrecognized flags before execution.
Alternatives considered: Relying only on API request validation was rejected because plans can be validated independently against registry snapshots.
Test implications: Integration-style plan validation tests for valid and invalid node inputs.

## FR-003 - Output Schema

Decision: Model output fields documented by section 8.2, with required status, stack, requestedImage, updatedServices, and runningServices, plus optional resolvedDigest and artifact refs.
Evidence: `docs/Tools/DockerComposeUpdateSystem.md` section 8.2 lists the required output shape.
Rationale: Downstream workflow steps need structured result fields and artifact refs, not command text scraping.
Alternatives considered: Leaving output schema open was rejected because source requires documented output fields.
Test implications: Unit test asserts required output fields and status enum.

## FR-004 / FR-005 / FR-006 - Privileged Execution Policy

Decision: The registry payload declares capabilities `deployment_control` and `docker_admin`, `security.allowed_roles = [admin]`, executor `mm.tool.execute` with selector `by_capability`, and retries `max_attempts = 1` with non-retryable privileged error codes.
Evidence: Existing `ToolDefinition` supports these fields and Temporal activity catalog consumes retry policy from definitions.
Rationale: Deployment updates are privileged operational work and should fail closed for invalid or locked operations.
Alternatives considered: A generic skill execution contract was rejected because the source doc requires deployment-control capabilities.
Test implications: Unit test assertions on parsed `ToolDefinition`.

## FR-007 / FR-009 - Plan Validation And API Binding

Decision: Validate representative deployment update plan payloads against a pinned registry snapshot and update the API queued-run builder to use shared tool constants.
Evidence: `create_registry_snapshot` and `validate_plan_payload` already provide deterministic validation against tool schemas.
Rationale: This proves the exact invocation shape used by the policy-gated API can be accepted by the plan/tool system.
Alternatives considered: Testing only the helper payload was rejected because MM-519 requires plan-node validation.
Test implications: Unit plus integration-style validation tests.
