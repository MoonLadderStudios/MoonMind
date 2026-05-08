# Research: Remediation Authority Policy

## FR-001 through FR-003 / SCN-001 / DESIGN-REQ-013 - Authority Modes

Decision: Treat authority-mode validation and observe-only side-effect denial as implemented and verified.
Evidence: `moonmind/workflows/temporal/service.py` defines supported authority modes and rejects unsupported values; `moonmind/workflows/temporal/remediation_actions.py` evaluates `observe_only`, `approval_gated`, and `admin_auto`; `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_action_authority_enforces_authority_modes` verifies observe-only dry-run/no-side-effect behavior.
Rationale: The source design requires explicit authority modes and fail-closed unsupported values; existing service and authority tests cover both persisted input validation and action evaluation.
Alternatives considered: Add new authority-mode aliases. Rejected by the compatibility policy and by the story requirement to fail closed.
Test implications: Focused unit tests are sufficient unless workflow/activity invocation shapes change.

## FR-004 through FR-005 / SCN-002 / SCN-004 - Approval And Risk Policy

Decision: Treat approval-gated and high-risk approval behavior as implemented and verified.
Evidence: `test_remediation_action_authority_requires_approval_for_gated_mode`, `test_remediation_action_authority_enforces_profile_permissions_and_risk`, and `tests/unit/workflows/temporal/test_temporal_service.py::test_record_remediation_approval_decision_appends_bounded_audit`.
Rationale: Approval-gated actions without approval return `approval_required`, high-risk actions require approval, and approval decisions append bounded audit entries.
Alternatives considered: Make approval policy a new persisted schema in this story. Rejected because the current story only needs the runtime behavior described by MM-619 and existing policy behavior already satisfies it.
Test implications: Run focused remediation authority and Temporal service tests.

## FR-006 through FR-009 / SCN-003 / SCN-005 - Named Profiles, Permissions, And Capabilities

Decision: Treat named privileged profile checks, requester/effective principal audit identity, separate permissions, and capability listing as implemented and verified.
Evidence: `RemediationPermissionSet`, `RemediationSecurityProfile`, `RemediationActionAuthorityService.list_allowed_actions`, `test_remediation_action_authority_lists_policy_compatible_actions`, `test_remediation_action_authority_enforces_profile_permissions_and_risk`, and API remediation approval-state serialization in `api_service/api/routers/executions.py`.
Rationale: The authority service does not advertise actions without target visibility/admin-profile permission, filters actions by profile, blocks disabled profiles, and records requester plus effective principal when allowed.
Alternatives considered: Move permission evaluation into the UI. Rejected because authority must be enforced at the service boundary.
Test implications: Unit tests cover the policy engine; API tests cover bounded approval state display.

## FR-010 through FR-013 / SCN-006 / DESIGN-REQ-014 - Secret And Visibility Guardrails

Decision: Treat redaction and mediated evidence behavior as implemented and verified, with MM-618 bounded-evidence behavior as an upstream dependency.
Evidence: `test_remediation_action_authority_redacts_audits_and_deduplicates`, `test_remediation_action_authority_denies_raw_access_and_unknown_targets`, `moonmind/workflows/temporal/remediation_context.py`, and existing bounded context tests in `tests/unit/workflows/temporal/test_remediation_context.py`.
Rationale: Decisions redact secret-like values and local paths, missing links do not expose hidden target identifiers, and remediation context uses refs and bounded summaries rather than raw storage access.
Alternatives considered: Add a second redaction layer in API serialization. Rejected because existing tests verify redaction before serialization and no API code change is planned.
Test implications: Run focused redaction and remediation context tests.

## FR-014 through FR-015 / SCN-007 / DESIGN-REQ-017 - Unsupported Raw Operations

Decision: Treat raw operation denial as implemented and verified.
Evidence: `_RAW_ACCESS_ACTION_KINDS`, `test_remediation_action_authority_does_not_advertise_raw_admin_actions`, and `test_remediation_action_authority_denies_raw_access_and_unknown_targets`.
Rationale: Raw host shell, Docker, SQL, storage-key, and similar operations are not advertised and are denied with explicit policy reasons.
Alternatives considered: Convert raw operations into generic tool invocations. Rejected because the source design explicitly forbids hidden fallback execution.
Test implications: Focused unit tests are sufficient.

## FR-016 / SC-005 - Traceability

Decision: Preserve MM-619 and DESIGN-REQ-013/014/017 across the new artifact set and final verification.
Evidence: `specs/319-remediation-authority-policy/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-authority-policy.md`, `quickstart.md`, `tasks.md`, and final `verification.md`.
Rationale: The implementation behavior already exists, but MM-619 requires a canonical MoonSpec trail so downstream PR and verification evidence can reference the Jira story.
Alternatives considered: Reuse MM-618 artifacts. Rejected because MM-618 explicitly excludes future MM-619 authority/policy scope.
Test implications: Run `rg -n "MM-619|DESIGN-REQ-013|DESIGN-REQ-014|DESIGN-REQ-017" specs/319-remediation-authority-policy`.
