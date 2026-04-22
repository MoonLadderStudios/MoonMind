# Research: Remediation Lifecycle Audit

## Input Classification

Decision: MM-456 is a single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-456-moonspec-orchestration-input.md` defines one user story, one source document, one acceptance-criteria set, and one bounded runtime observability/audit behavior area.
Rationale: The Jira preset brief selects remediation lifecycle evidence from `docs/Tasks/TaskRemediation.md` sections 13, 14, and 16; it does not ask to implement the entire remediation design.
Alternatives considered: Treating `docs/Tasks/TaskRemediation.md` as a broad declarative design was rejected because the Jira brief already selected one independently testable story. Treating this as docs mode was rejected because the selected mode is runtime.
Test implications: Unit and service-boundary tests should target exactly this lifecycle evidence story.

## Existing Remediation Evidence Boundaries

Decision: Reuse remediation context, action authority, Temporal artifact, execution link, and execution read-model boundaries.
Evidence: `moonmind/workflows/temporal/remediation_context.py` publishes `remediation.context`; `moonmind/workflows/temporal/remediation_actions.py` produces redacted action authority request/result/audit payloads; `api_service/db/models.py` contains `execution_remediation_links` with target, status, lock, action summary, and outcome fields; generic run summaries are published by worker/runtime paths.
Rationale: The story is about durable runtime evidence and compact queryable state, so existing artifact and link surfaces are the narrowest extension points.
Alternatives considered: A new standalone remediation evidence store was rejected because the source design requires existing artifact presentation and compact audit/read-model evidence.
Test implications: Add tests around existing services rather than introducing provider or raw storage tests.

## DESIGN-REQ-017 - Runtime Lifecycle

Decision: Partial; target/run identity exists, but bounded remediation phase progression and continuation preservation are not implemented as a complete lifecycle contract.
Evidence: `execution_remediation_links` stores remediation workflow/run and target workflow/run; no `remediationPhase` or allowed phase enum was found by repository search.
Rationale: Existing target linkage is enough to anchor lifecycle evidence, but operators cannot yet inspect the remediation-specific phase sequence required by MM-456.
Alternatives considered: Reusing top-level `mm_state` alone was rejected because the source design explicitly says remediation phase is a bounded remediation-specific field inside summaries/read models.
Test implications: Unit tests for allowed phase values; integration/service-boundary tests for summary/read-model exposure.

## DESIGN-REQ-018 - Required Remediation Artifacts And Summary

Decision: Partial; `remediation.context` is implemented and verified, but plan, decision log, action request/result artifacts, verification artifacts, and final summary artifacts are missing or only present as in-memory action payloads.
Evidence: `RemediationContextBuilder` creates `reports/remediation_context.json` with artifact type `remediation.context`; searches did not find publishers for `remediation.plan`, `remediation.decision_log`, `remediation.verification`, or `remediation.summary`.
Rationale: Deep operator evidence belongs in artifacts. The existing context builder pattern can be generalized without embedding bodies in workflow history.
Alternatives considered: Encoding all lifecycle evidence into `reports/run_summary.json` was rejected because artifacts remain the operator-facing deep evidence and summary must stay compact.
Test implications: Artifact publication tests should assert names, artifact types, bounded metadata, redaction level, and execution links.

## DESIGN-REQ-019 - Target Linkage And Control-Plane Audit

Decision: Partial; link status fields and action audit payloads exist, but target-side linkage summary and queryable remediation audit events are incomplete.
Evidence: `execution_remediation_links` includes `active_lock_scope`, `active_lock_holder`, `latest_action_summary`, and `outcome`; `RemediationActionAuthorityResult.to_dict()` includes an `audit` mapping; generic intervention audit exists in Temporal execution memo paths.
Rationale: Link fields and action audit payloads provide useful ingredients, but the story requires compact control-plane audit events with actor, principal, remediation/target workflow-run identity, action kind, risk, approval decision, timestamps, and bounded metadata.
Alternatives considered: Making operators parse action artifacts for target detail summaries was rejected because the source requires compact queryable trails and downstream detail metadata.
Test implications: Unit tests for audit event schema/boundedness and read-model tests for target-side linkage summary.

## DESIGN-REQ-022 - Rerun, Precondition, Continuation, And Failure Preservation

Decision: Partial; pinned target run and mutation guard freshness decisions exist in adjacent work, but lifecycle summaries do not yet record resulting run, precondition-failed/no-op outcomes, or Continue-As-New preservation.
Evidence: `execution_remediation_links.target_run_id` stores the pinned target run; mutation guard services include target freshness decisions; no lifecycle continuation payload or resulting target run summary was found.
Rationale: The lifecycle evidence layer needs to make target changes and continuation state visible, not only enforce action guard decisions.
Alternatives considered: Treating target reruns as implicit target status changes was rejected because the source requires recording pinned and resulting runs.
Test implications: Unit tests should cover continuation payload validation, target rerun summary fields, and precondition failure outcomes.

## DESIGN-REQ-023 - Bounded Failure And Degraded Evidence

Decision: Partial; context and action guard paths have bounded errors, but the lifecycle evidence contract does not yet unify degraded evidence, partial artifact refs, live-follow fallback, lock conflict, no-op, escalation, and failed-remediator summaries.
Evidence: `RemediationContextBuilder` limits evidence sizes and sets `liveFollow.supported`; action guard services return bounded reason codes; no final lifecycle summary model ties these cases together.
Rationale: Operators need one consistent remediation summary and audit trail for failure/degraded cases.
Alternatives considered: Letting each producer use bespoke reason fields was rejected because the final summary must be stable and queryable.
Test implications: Tests should assert explicit bounded outcomes for target not visible, partial artifacts, live-follow unavailable, lock conflict, precondition failed, stale lease, missing container, unsafe forced termination, and remediator failure where supported in this story.

## Testing Strategy

Decision: Use focused unit tests plus service-boundary integration where artifact and read-model paths are touched.
Evidence: Adjacent remediation stories use `tests/unit/workflows/temporal/test_remediation_context.py`; artifact authorization and lifecycle suites already exercise `TemporalArtifactService`; API router tests cover execution and task-run read models.
Rationale: The highest-risk behavior is local runtime evidence publication and serialization, not external providers.
Alternatives considered: Provider verification tests were rejected because MM-456 does not require external credentials. Full Temporal time-skipping tests are unnecessary unless workflow signatures or replay-sensitive payloads change.
Test implications: Use `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` if hermetic integration routes or artifact lifecycle behavior are changed.
