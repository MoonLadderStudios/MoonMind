# Research: Remediation Action Registry

## Input Classification

Decision: The MM-454 brief is a single-story runtime feature request.
Evidence: `spec.md` (Input) defines one actor, one user story, one source document slice, one validation set, and no sibling stories requiring splitting.
Rationale: The story is independently testable by evaluating typed remediation action requests and durable decision outputs.
Alternatives considered: Treating `docs/Tasks/TaskRemediation.md` as a broad design was rejected because the Jira brief already selected sections 11, 10.6, and 17 for one action-registry slice.
Test implications: Unit and service-boundary tests are sufficient for the selected slice; broader remediation workflows belong to adjacent specs.

## DESIGN-REQ-012 Typed Action Registry

Decision: Implement and verify typed action metadata in `RemediationActionAuthorityService.list_allowed_actions()` and `_ACTION_CATALOG`.
Evidence: `moonmind/workflows/temporal/remediation_actions.py`; `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_action_authority_lists_policy_compatible_actions`.
Rationale: A local catalog keeps v1 scope explicit and omits unsupported future actions instead of exposing raw access.
Alternatives considered: A database-backed registry was rejected for this slice because the brief does not require new persistent storage and the current policy/profile inputs are compact.
Test implications: Unit tests verify filtering by permissions/profile and returned risk/input metadata.

## DESIGN-REQ-013 Request And Result Contracts

Decision: Serialize v1 request/result/audit envelopes from `RemediationActionAuthorityResult.to_dict()`.
Evidence: `to_dict()` includes `schemaVersion`, `request`, `result`, and `audit`; profile/risk tests assert these fields.
Rationale: The authority service is not the owning executor, but it can produce the durable bounded contract that downstream execution/audit publishing consumes.
Alternatives considered: Adding a new artifact writer was deferred because the current story is the registry decision contract, not artifact persistence plumbing.
Test implications: Unit tests assert required request/result fields and audit principal preservation.

## DESIGN-REQ-023 Bounded Degradation

Decision: Unsupported, disabled, missing-link, and unauthorized actions fail closed through explicit decisions; unsupported future actions are omitted from action listing.
Evidence: Existing tests cover missing links, unsupported modes/actions, disabled profiles, and idempotency.
Rationale: Bounded denial is safer than attempting raw fallback behavior.
Alternatives considered: Returning all recommended future actions with disabled flags was rejected because the brief says unavailable actions should be omitted rather than exposed as raw access.
Test implications: Unit tests cover denied decisions and allowed listing output.

## DESIGN-REQ-024 Raw Access And Redaction

Decision: Keep raw host, Docker, SQL, raw storage, and redaction-bypass action kinds outside the catalog and deny known raw access names before profile validation.
Evidence: `_RAW_ACCESS_ACTION_KINDS`, raw-prefix check, redaction helpers, and tests for raw host shell and redaction-sensitive params.
Rationale: Denying raw access at the registry boundary preserves the source design non-goals.
Alternatives considered: Allowing operators to pass arbitrary command params was rejected as contrary to MM-454 and source non-goals.
Test implications: Unit tests assert raw access rejection and absence of raw secrets/paths in serialized output.

## Integration Strategy

Decision: Use service-boundary unit tests rather than compose-backed integration for this story.
Evidence: The registry does not call external providers, Docker, Temporal workers, or credentialed services; it reads local remediation link rows and pure profile/permission inputs.
Rationale: The required integration behavior is the boundary between persisted remediation link data, prepared action context, and action authority evaluation, which the existing async database test harness covers.
Alternatives considered: Compose-backed integration was rejected because it would add runtime cost without exercising a new external boundary.
Test implications: `./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py` is the required command.
