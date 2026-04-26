# Research: Policy-Gated Deployment Update API

## Request Classification

Decision: `MM-518` is a single-story runtime feature request.
Evidence: `spec.md` preserves one Jira Story with one administrator actor, one deployment update API goal, one source document, and one bounded acceptance set.
Rationale: The Jira preset brief does not require story splitting; the source document is an implementation/source requirements document for runtime behavior.
Alternatives considered: Treating `docs/Tools/DockerComposeUpdateSystem.md` as a broad declarative design was rejected because the Jira brief selected sections 7, 10.1, and 13.1-13.3 for one policy-gated API slice.
Test implications: Unit and API-router tests are required.

## API Boundary

Decision: Add a dedicated FastAPI router at `/api/v1/operations/deployment`.
Evidence: Existing routers live under `api_service/api/routers/` and are registered in `api_service/main.py`.
Rationale: Deployment operations are operator-facing API controls, not task template, proxy, or manifest concerns.
Alternatives considered: Extending the task execution router was rejected because FR-004 requires deployment update permissions to remain distinct from ordinary task submission.
Test implications: Router tests should import the router directly and override the current-user dependency.

## Policy Validation

Decision: Put stack/repository/reference/mode/reason validation in `api_service/services/deployment_operations.py`.
Evidence: No existing deployment operation validator exists; service modules contain domain logic separate from routers.
Rationale: A service boundary keeps policy checks testable and ensures invalid requests fail before any workflow or tool execution.
Alternatives considered: Inline router validation was rejected because it would mix HTTP details with deployment policy rules.
Test implications: Router tests cover service behavior through HTTP status and error-code assertions.

## Authorization

Decision: Require `is_superuser` for update submission and authenticated user access for read endpoints.
Evidence: Existing API tests and dependencies use `get_current_user()` and `is_superuser` for admin-sensitive operations.
Rationale: The story explicitly requires administrator-only update submission and permission distinct from ordinary task submission; read operations are inspection endpoints with no mutation.
Alternatives considered: Reusing task submission enablement was rejected because it would violate FR-004.
Test implications: Include a non-admin submit rejection test.

## Typed Contract And No Arbitrary Shell

Decision: Use Pydantic models with `extra="forbid"` and typed image/update fields.
Evidence: The source design forbids arbitrary shell commands, Compose paths, host paths, runner images, and unrecognized flags.
Rationale: Rejecting unknown payload fields at model validation prevents unsafe caller-controlled command surfaces from reaching policy or execution logic.
Alternatives considered: Accepting flexible `dict` payloads was rejected because it would allow hidden input expansion.
Test implications: Include a request containing `command` and `composeFile` and assert validation rejection.

## Requirement Coverage Status

Decision: All implementation requirements are new or partial before this story.
Evidence: Repository search found no deployment operations router/service and no existing `/api/v1/operations/deployment` contract.
Rationale: Existing auth and router patterns are reusable, but the deployment-specific contract and policy checks are missing.
Alternatives considered: Marking admin auth as implemented-verified was rejected because no deployment endpoint uses it.
Test implications: Add targeted tests and run the unit suite.
