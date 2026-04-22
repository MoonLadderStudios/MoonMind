# Research: Remediation Authority Boundaries

## Classification

Decision: Treat MM-453 as a single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-453-moonspec-orchestration-input.md` contains one user story, one source design, one bounded acceptance set, and runtime-mode constraints.
Rationale: The story can be independently validated by creating remediation action authority decisions and checking allowed, gated, audited, rejected, and redacted outcomes.
Alternatives considered: Broad technical design was rejected because the Jira brief already selected one story from `docs/Tasks/TaskRemediation.md`; existing feature directory was rejected because no `specs/*` artifact referenced MM-453.
Test implications: Unit and service-boundary integration-style tests are required.

## Authority Mode Gap

Decision: Existing create-time validation is partial; action-boundary enforcement is missing.
Evidence: `moonmind/workflows/temporal/service.py` defines `ALLOWED_REMEDIATION_AUTHORITY_MODES` and rejects unsupported modes. `tests/unit/workflows/temporal/test_temporal_service.py` covers unsupported modes and link persistence. No side-effecting remediation action authority module exists.
Rationale: Accepting and persisting a mode is not enough to prove mode semantics for side-effecting actions.
Alternatives considered: Reusing generic execution interventions was rejected because remediation actions require their own authority and audit boundary.
Test implications: Add tests for `observe_only`, `approval_gated`, and `admin_auto` action decisions.

## Permission And Security Profile Model

Decision: Add compact service-boundary permission and security profile inputs for action decisions.
Evidence: Target owner checks exist through `_can_reference_dependency_target`, but no separate permission model for admin profile request, high-risk approval, or audit inspection was found. `docs/Tasks/TaskRemediation.md` section 10.3 requires these permissions to be distinct.
Rationale: The feature requires target view permission to be insufficient for admin remediation and high-risk approvals.
Alternatives considered: Inferring admin authority from target ownership was rejected because it violates the security model.
Test implications: Add permission matrix tests for view-only, admin remediation, high-risk approval, and audit permissions.

## Action Policy And Risk Gating

Decision: Add a typed policy evaluation surface with low/medium/high action risk and approval requirements.
Evidence: `ALLOWED_REMEDIATION_ACTION_POLICY_REFS` validates `admin_healer_default`; no action registry or high-risk approval gate exists. `RemediationEvidenceToolService.prepare_action_request` already re-reads target health but intentionally does not execute actions.
Rationale: The new authority boundary should consume the fresh target-health preparation and decide whether a side-effecting action may execute, must wait for approval, or must be denied.
Alternatives considered: Executing actions directly in evidence tools was rejected because those tools are intentionally read-only plus pre-action preparation.
Test implications: Add unit tests for allowed low/medium actions, high-risk approval requirements, disabled actions, and unsupported action kinds.

## Idempotency And Audit

Decision: Represent each action decision with a deterministic request key, result status, audit payload, and redacted summary while avoiding a new persistent table in this slice.
Evidence: `execution_remediation_links` already has compact latest action fields; `docs/Tasks/SkillAndPlanEvolution.md` and `docs/Tasks/TaskRemediation.md` require idempotent side effects and reviewable audit evidence.
Rationale: This story can validate action authority and duplicate request behavior without introducing the full future action ledger table.
Alternatives considered: Adding a database ledger now was rejected because the Jira brief asks for authority boundaries, not the full action registry storage slice.
Test implications: Add tests proving duplicate idempotency keys return the same decision and do not create duplicate executable results.

## Redaction And No-Raw-Access Boundaries

Decision: Redact action parameters, action results, and audit payloads through existing logging redaction helpers and explicitly reject raw access action kinds.
Evidence: `moonmind/utils/logging.py` provides secret redaction helpers. Existing remediation context and evidence tools avoid raw bodies and raw storage access, but no action-output redaction test exists.
Rationale: Stronger authority must not override secret redaction or expose host, Docker, SQL, storage, path, or credential access.
Alternatives considered: Trusting callers to redact was rejected because the boundary must be safe by default.
Test implications: Add tests with token-like and private-key-like payloads, raw access action names, and unauthorized direct fetch inputs.

## Test Strategy

Decision: Keep verification in `tests/unit/workflows/temporal/test_remediation_context.py` with focused tests for the new action authority module and one end-to-end service-boundary flow.
Evidence: Existing remediation context, evidence, and create/link tests are already in this file and use local async DB and artifact fixtures.
Rationale: The story stays inside the Temporal service/activity boundary and does not require compose-backed services.
Alternatives considered: Adding a new integration_ci suite was rejected because no Docker-backed or external provider seam is introduced.
Test implications: Run targeted unit tests during implementation and full `./tools/test_unit.sh` before finalization when feasible.
