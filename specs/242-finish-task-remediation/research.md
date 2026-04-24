# Research: Finish Task Remediation Desired-State Implementation

## Input Classification

Decision: MM-483 is a single-story runtime feature request.
Evidence: `spec.md` (Input) has one user story, one operator outcome, explicit acceptance criteria, and explicit non-goals.
Rationale: Although the story spans backend, workflow, API, and UI surfaces, all requirements support one independently testable operator outcome: complete Task Remediation runtime behavior.
Alternatives considered: Treat as broad design requiring breakdown. Rejected because Jira has already selected the single story and the brief defines concrete acceptance criteria.
Test implications: Unit, integration, and UI coverage are required.

## Canonical Action Registry

Decision: Implemented first.
Evidence: Initial gap analysis found `moonmind/workflows/temporal/remediation_actions.py` exposed `restart_worker` and `terminate_session`; the implementation now exposes the canonical dotted action kinds required by `docs/Tasks/TaskRemediation.md` and MM-483.
Rationale: The registry is the smallest high-value missing surface and is a prerequisite for safe action execution wiring.
Alternatives considered: Keep legacy aliases. Rejected by Constitution XIII because this is pre-release and superseded internal contracts should be removed.
Test implications: Unit tests must assert the full canonical action set, metadata shape, profile filtering, and raw action denial.

## Action Execution Boundary

Decision: Implemented at the evidence-tool service boundary; concrete subsystem action implementations remain open.
Evidence: `RemediationActionAuthorityService` remains side-effect-free, while `RemediationEvidenceToolService.execute_action` validates executable authority and guard results, delegates mutation to an injected action executor, and publishes `remediation.action_request`, `remediation.action_result`, and `remediation.verification` artifacts.
Rationale: Authority decisions stay pure and side-effecting work is forced through an owning executor boundary instead of raw shell, Docker, SQL, storage, or secret access.
Alternatives considered: Execute directly from the authority service. Rejected because it would weaken the safety boundary.
Test implications: Focused adapter-boundary tests prove delegation and artifact publication. Concrete managed-session, provider-profile, workload, and execution action implementations still need runtime-specific tests.

## Durable Lock And Ledger

Decision: Implemented for mutation locks and action ledger state.
Evidence: `RemediationMutationGuardService` persists active lock state and ledger entries on `execution_remediation_links` through `mutation_guard_lock_state` and `mutation_guard_ledger_state`; restart-durability coverage constructs a new service instance and verifies duplicate idempotency replay plus cross-remediator lock conflict detection.
Rationale: Reusing the durable remediation link keeps guard state attached to the remediation-to-target relationship without adding a separate persistence concept for this slice.
Alternatives considered: Keep process-local dictionaries. Rejected because MM-483 requires restart durability. Add another ad hoc compatibility layer. Rejected because the existing remediation link is the natural durable boundary.
Test implications: Focused service-boundary tests prove restart durability for lock and ledger state; final verification still needs aggregate action execution, artifact, read-model, and UI coverage.

## Lifecycle Artifacts And Read Models

Decision: Implemented unverified for the MM-483 aggregate story.
Evidence: `RemediationContextBuilder`, MM-456 artifacts, and execution read models already expose context, summary, approval, status, lock, action, and outcome fields.
Rationale: Existing slices likely satisfy part of this story, but MM-483 needs a final aggregate verification matrix.
Alternatives considered: Reimplement lifecycle artifacts. Rejected because existing MM-456 work should be reused where correct.
Test implications: Add verification tests first; implement only gaps exposed by those tests.

## Mission Control Presentation

Decision: Partial.
Evidence: `frontend/src/entrypoints/task-detail.tsx` renders remediation relationships, creation preview, approval controls, and evidence panels.
Rationale: The UI already has remediation surfaces, but MM-483 requires completed action/approval/verification lifecycle visibility without unsafe paths or secret-bearing data.
Alternatives considered: Skip UI. Rejected because the Jira brief explicitly requires Mission Control coverage.
Test implications: Vitest coverage is required if task detail rendering changes.
