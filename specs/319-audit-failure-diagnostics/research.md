# Research: Record Audit Events and Failure Diagnostics for Skills On Demand

## FR-001 / FR-002 / DESIGN-REQ-001 - Snapshot Preservation

Decision: Treat current snapshot-preservation behavior as implemented and verified; preserve it while adding audit event emission.

Evidence: `moonmind/services/skills_on_demand.py` builds denied request results with no derived `snapshot_id`; `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` covers disabled, invalid, and denial preservation; `tests/integration/temporal/test_skills_on_demand_request_activation.py` covers materialization, checksum, and runtime refresh failure preservation.

Rationale: The existing behavior satisfies the snapshot preservation contract, and MM-616 should layer audit and diagnostics over it rather than change activation semantics.

Alternatives considered: Reworking request resolution was rejected because MM-614 and MM-615 own resolution and activation behavior.

Test implications: Final verification plus regression assertions in new audit tests.

## FR-003 Through FR-006 / DESIGN-REQ-002 - Failure Diagnostic Shape

Decision: Add or extend a compact Skills On Demand failure diagnostic contract with stable code/message, optional current snapshot ref, and optional diagnostics ref.

Evidence: `SkillsOnDemandQueryResult` and `SkillsOnDemandRequestResult` already carry `code` and `message`; `SkillsOnDemandDeniedCode` includes most documented codes; no dedicated diagnostics ref exists on Skills On Demand results.

Rationale: The spec requires operator-actionable diagnostics and controlled refs. Existing status/code fields are a base, but they do not fully satisfy the documented failure diagnostic shape.

Alternatives considered: Storing diagnostic details only in unstructured messages was rejected because it is harder to validate, redact, and reference safely.

Test implications: Unit tests for all documented failure codes and optional diagnostics refs; integration tests for materialization/runtime refresh diagnostic refs where produced.

## FR-007 Through FR-009 / SC-001 / DESIGN-REQ-003 / DESIGN-REQ-005 - Query Audit Events

Decision: Add an explicit `skills_on_demand.query` event model and emit exactly one bounded event per query attempt.

Evidence: `SkillsOnDemandService.query()` already returns `metadata.result_count`, `metadata.denied`, `metadata.denial_code`, and `metadata.query_hash`, but no event named `skills_on_demand.query` is emitted.

Rationale: Result metadata is useful but does not prove an audit/observability event exists. The event contract is required for operator-visible audit evidence and consistent downstream tests.

Alternatives considered: Treating result metadata as the audit event was rejected because the source design requires one event per query and the spec separates audit evidence from command result shape.

Test implications: Unit tests should assert hash-only query evidence and no raw long query text; integration tests should assert event emission through the activity boundary.

## FR-010 Through FR-012 / SC-002 / DESIGN-REQ-004 - Request Audit Events

Decision: Add an explicit `skills_on_demand.request` event model and emit exactly one bounded event per request attempt.

Evidence: Request results currently include requested Skills, status, code, parent/derived snapshot refs, manifest/materialization summary, and no body refs in existing tests; no event named `skills_on_demand.request` is emitted.

Rationale: Requests are the highest-risk state-transition surface. Audit events need to record compact request/result state even when the result is denied, no-change, activated, or reserved for future approval semantics.

Alternatives considered: Recording only activated requests was rejected because the source design explicitly requires requests, denials, and failures to be auditable.

Test implications: Unit and integration tests should assert one request event for disabled, invalid, no-change, allowed, policy-denied, materialization-failed, and runtime-refresh-failed paths.

## FR-013 Through FR-016 / SC-004 / SC-005 / DESIGN-REQ-006 - Bounds, Redaction, And Policy Boundaries

Decision: Keep audit and diagnostics payloads bounded and non-secret by construction; use hashes for raw query text, names/refs only for Skills, and controlled diagnostics refs for larger evidence.

Evidence: Existing query tests assert hidden source paths, content refs, and digests do not leak in serialized query results; existing request tests assert Skill body refs do not appear in activation results. Audit and diagnostic outputs do not yet have equivalent tests.

Rationale: The new event surfaces must preserve the same secret/body/access boundaries as existing result surfaces. This is a security and operator-trust requirement, not just presentation behavior.

Alternatives considered: Embedding full diagnostic bodies directly in event metadata was rejected because it would risk high-cardinality data, secrets, and large workflow payloads.

Test implications: Unit tests for redaction and hash-only query metadata; integration tests to prove activity-boundary events do not include body refs or repo-authored projection mutations.

## FR-017 / SC-006 / DESIGN-REQ-007 - Test Matrix

Decision: Extend the existing Skills On Demand test matrix rather than create a separate unrelated suite.

Evidence: Existing files already cover disabled feature, bounded query metadata, already-active request, allowed activation, policy denial, materialization failure, checksum failure, repo-source preservation, and runtime refresh failure.

Rationale: Co-locating audit assertions with current behavior tests keeps the new requirements tied to the real service/activity paths and avoids a mock-only audit layer.

Alternatives considered: Adding only pure schema tests was rejected because Temporal activity-boundary evidence is required for workflow-facing behavior.

Test implications: Add focused unit tests under `tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` and integration_ci tests under `tests/integration/temporal/test_skills_on_demand_request_activation.py` or a new adjacent file.

## FR-018 / SC-007 - Traceability

Decision: Preserve `MM-616`, the original Jira preset brief, and source design mappings across plan, tasks, implementation notes, and verification.

Evidence: `specs/319-audit-failure-diagnostics/spec.md` preserves the original Jira preset brief and maps DESIGN-REQ-001 through DESIGN-REQ-007.

Rationale: Final verification depends on comparing the implementation against the original issue brief and source design requirements.

Alternatives considered: Referring only to the Jira issue key was rejected because final verification needs the full preserved brief.

Test implications: Final verification must check traceability artifacts; no code test required beyond final review.

## Test Strategy

Decision: Use `./tools/test_unit.sh` for final unit coverage and `./tools/test_integration.sh` for hermetic integration_ci coverage, with focused pytest targets during implementation.

Evidence: Repo instructions require these runners. Existing Skills On Demand tests already use pytest and Temporal `ActivityEnvironment`; integration tests are marked `integration` and `integration_ci`.

Rationale: The story touches service contracts and Temporal activity boundaries, so both unit and integration evidence are required.

Alternatives considered: Provider verification tests were rejected because this feature has no third-party credentialed provider dependency.

Test implications: Unit tests cover models/service event payload construction; integration tests cover real activity invocation shapes and failure paths.
