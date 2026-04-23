# Research: Finish Task Remediation Desired-State Implementation

## Input Classification

Decision: MM-483 is a single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-483-moonspec-orchestration-input.md` has one user story, one operator outcome, explicit acceptance criteria, and explicit non-goals.
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

Decision: Partial; plan adapter-boundary work after registry coverage.
Evidence: `RemediationActionAuthorityService` explicitly does not execute host/container/SQL/provider/storage operations; owning runtime activities exist for managed sessions and provider profiles.
Rationale: Authority decisions should stay side-effect-free while execution moves through owning subsystem adapters.
Alternatives considered: Execute directly from the authority service. Rejected because it would weaken the safety boundary.
Test implications: Adapter-boundary tests must cover real invocation shapes before production execution wiring.

## Durable Lock And Ledger

Decision: Partial; existing guard behavior is in-memory and tested, but MM-483 requires durability across process restarts and retries.
Evidence: `RemediationMutationGuardService` stores `_locks`, `_ledger`, and budgets in instance dictionaries.
Rationale: The current implementation is useful for policy semantics but not sufficient for restart durability.
Alternatives considered: Add another ad hoc compatibility layer. Rejected; either reuse existing durable records or introduce an explicit cutover-backed persistent representation.
Test implications: Unit/service tests must simulate new service instances and prove duplicate/idempotency state survives.

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
