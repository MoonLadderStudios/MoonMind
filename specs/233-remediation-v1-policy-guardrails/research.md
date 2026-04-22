# Research: Remediation V1 Policy Guardrails

## Input Classification

Decision: MM-458 is a single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-458-moonspec-orchestration-input.md` defines one user story, one acceptance-criteria set, and one bounded runtime behavior area.
Rationale: The story focuses on manual-by-default remediation v1, future self-healing policy constraints, raw capability denial, and bounded edge-case outcomes.
Alternatives considered: Treating `docs/Tasks/TaskRemediation.md` as a broad design was rejected because the Jira preset brief already selected one independently testable slice.
Test implications: Unit and service-boundary tests should target the selected policy/capability surface.

## Existing Remediation Creation Boundary

Decision: Use `TemporalExecutionService.create_execution()` as the create-time boundary for manual remediation and inert future policy verification.
Evidence: `moonmind/workflows/temporal/service.py` creates a remediation link only when `task.remediation` is present and validates that target. Search found no `remediationPolicy` consumer.
Rationale: MM-458 requires default v1 behavior not to spawn admin healers. The create service is the canonical local boundary for proving that policy-only task metadata does not create remediation links.
Alternatives considered: Full workflow failure simulation was rejected because automatic remediation is not implemented; verifying the creation boundary is the narrowest deterministic proof.
Test implications: Add async DB-backed unit tests in `tests/unit/workflows/temporal/test_temporal_service.py`.

## Existing Capability Surface

Decision: Use `RemediationActionAuthorityService.list_allowed_actions()` and raw-action denial as the capability-surface boundary.
Evidence: `_ACTION_CATALOG` in `moonmind/workflows/temporal/remediation_actions.py` lists typed actions only, and existing tests deny `raw_host_shell`.
Rationale: Unsupported raw host, Docker, SQL, storage, and secret-reading actions must be absent or fail closed. The action authority service is the runtime source of action metadata and action decisions.
Alternatives considered: Testing frontend strings alone was rejected because runtime capability metadata is the authoritative boundary.
Test implications: Add a catalog absence assertion for raw capability names.

## DESIGN-REQ-016 - Manual V1 And Bounded Self-Healing

Decision: Partially implemented and needs verification; v1 has no automatic self-healing creation path, while mutation guard policy already defaults self-healing depth to 1.
Evidence: `RemediationMutationGuardPolicy.max_self_healing_depth` defaults to 1; `TemporalExecutionService` only creates remediation links for explicit `task.remediation`.
Rationale: Existing absence of automatic creation should be locked down with a regression test because this is the central MM-458 risk.
Alternatives considered: Adding automatic policy parsing was rejected as outside this v1 guardrail story.
Test implications: Unit test verifies `task.remediationPolicy` remains inert and does not create `TemporalExecutionRemediationLink`.

## DESIGN-REQ-022 - Target Changes And Preconditions

Decision: Implemented and verified by existing mutation guard tests.
Evidence: `RemediationMutationGuardService` target freshness decisions compare pinned/current run and target state, with no-op, rediagnose, or escalate outcomes.
Rationale: MM-458 does not need a second implementation of freshness handling; it needs traceability to the existing bounded outcome behavior.
Alternatives considered: Adding workflow-level freshness logic was rejected to avoid duplicate semantics.
Test implications: Existing `test_remediation_context.py` tests remain part of final targeted verification.

## DESIGN-REQ-023 - Bounded Failure Outcomes

Decision: Implemented and verified across remediation context/action tests.
Evidence: `moonmind/workflows/temporal/remediation_context.py` defines bounded resolution states; `test_remediation_context.py` covers degraded evidence, summaries, action authority denials, lock conflicts, and redaction-safe outputs.
Rationale: The story depends on these bounded outcomes but does not need new storage or workflow state for them.
Alternatives considered: New result enum was rejected because existing remediation models already expose the required outcomes.
Test implications: Run existing remediation context tests after adding MM-458 focused tests.

## DESIGN-REQ-024 - Enforced Non-Goals

Decision: Implemented and verified for action authority, with one additional verification test planned for catalog absence.
Evidence: `RemediationActionAuthorityService` denies raw access action kinds; service validation rejects unsupported authority modes and incompatible policies.
Rationale: Raw capability absence must be true both for action requests and discoverable action metadata.
Alternatives considered: UI-only checks were rejected because the runtime action catalog is the primary source.
Test implications: Add a unit test that allowed action metadata does not expose raw host, Docker, SQL, storage-key, secret-read, or redaction-bypass actions.

## Testing Strategy

Decision: Use targeted unit/service-boundary tests plus final full unit suite when feasible.
Evidence: Adjacent remediation specs use `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/workflows/temporal/test_remediation_context.py` for these boundaries.
Rationale: No external provider or compose-backed service is needed for this policy guardrail story.
Alternatives considered: Temporal time-skipping workflow tests were rejected because no workflow signature or activity contract changes are planned.
Test implications: Primary command is `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/test_remediation_context.py`; final command is `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
