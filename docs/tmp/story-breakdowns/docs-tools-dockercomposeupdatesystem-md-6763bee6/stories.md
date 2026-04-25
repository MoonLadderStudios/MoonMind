# Story Breakdown: Docker Compose Deployment Update System

- Source design: `docs/Tools/DockerComposeUpdateSystem.md`
- Output mode: `jira`
- Extracted at: `2026-04-25T22:37:56Z`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines an admin-only Mission Control Settings -> Operations workflow for updating a Docker Compose-managed MoonMind deployment by selecting an allowlisted target MoonMind image. The backend turns that request into a typed privileged deployment.update_compose_stack tool invocation that persists desired image state, serializes per-stack updates, runs policy-controlled Compose pull/up operations, verifies health and image identity, records audit data and artifacts, and exposes progress without becoming a general shell, Docker UI, agent instruction skill, GitOps replacement, or Kubernetes deployment system.

## Coverage Points

- `DESIGN-REQ-001` (requirement, 1. Purpose; 6. Settings -> Operations UX): Mission Control Operations update surface - Administrators need a Settings -> Operations Deployment Update card that exposes current state, target selection, update mode, options, reason, confirmation, and recent runs.
- `DESIGN-REQ-002` (constraint, 1. Purpose; 5. Core invariants; 20. Locked decisions): Target MoonMind image is the operator choice - The UI and API center on selecting an allowlisted MoonMind target image tag or digest, while the privileged updater runner image remains deployment-controlled configuration.
- `DESIGN-REQ-003` (security, 4.1 Goals; 10.1 Validate request; 13. Security and policy model): Admin authorization and deployment policy enforcement - Only administrators may initiate updates, and stack names, host paths, Compose files, image repositories, modes, flags, and runner images must be checked against deployment policy.
- `DESIGN-REQ-004` (constraint, 4.2 Non-goals; 13.3 No arbitrary shell): No arbitrary shell or general Docker UI - The feature must not expose user-authored shell snippets, arbitrary Compose paths, unrecognized flags, or a general-purpose Docker control surface.
- `DESIGN-REQ-005` (integration, 7. API contract): Typed API contract for update submission and state reads - The backend exposes typed endpoints for submitting updates, reading current stack state, and listing allowed image targets.
- `DESIGN-REQ-006` (integration, 8. Executable tool contract): Typed executable tool contract - Deployment update is represented by the privileged executable tool deployment.update_compose_stack with schema, capability, timeout, retry, output, and security requirements.
- `DESIGN-REQ-007` (state-model, 9. Desired state storage; 20. Locked decisions): Desired image state persistence before Compose up - The requested image reference must be written to an allowlisted deployment env file or equivalent state store before services are brought up so restarts preserve the target state.
- `DESIGN-REQ-008` (artifact, 9.3 Mutable tag rule; 20. Locked decisions): Mutable tags and digest recording - Mutable tags may be allowed, but resolved digests must be recorded when available and displayed distinctly from the requested reference.
- `DESIGN-REQ-009` (state-model, 5. Core invariants; 10. Execution lifecycle): Serialized per-stack update lifecycle - Only one update may run per Compose stack; the workflow validates, acquires a deployment lock, captures before state, persists desired state, pulls images, recreates services, verifies, captures after state, releases the lock, and reports.
- `DESIGN-REQ-010` (requirement, 10.5 Pull images; 10.6 Recreate services; 11. Updater runner execution model): Compose command execution modes - The tool performs policy-controlled equivalents of docker compose pull and docker compose up, supporting changed-service recreation and force-recreate-all modes.
- `DESIGN-REQ-011` (security, 11. Updater runner execution model; 13.4 Docker socket risk): Privileged runner boundary - Compose commands run only through trusted deployment-control infrastructure, either a privileged worker or ephemeral updater container; Docker socket access is not exposed to ordinary runtimes, agents, repo workspaces, or user-authored tools.
- `DESIGN-REQ-012` (requirement, 12. Verification model): Verification gates success - Compose state, health, image identity/digest matching, optional smoke checks, and orphan cleanup must be verified; unresolved desired state cannot be marked SUCCEEDED.
- `DESIGN-REQ-013` (artifact, 14. Audit and artifacts): Durable artifacts and audit record - Every run records identity, reason, image, options, timestamps, result, failure reason, and immutable before state, command log, verification, and after state artifacts.
- `DESIGN-REQ-014` (security, 13.5 Secret handling; 14.3 Artifact redaction; 14.4 Operations display): Secret redaction and artifact access controls - Registry credentials and sensitive values must not appear in image refs, UI defaults, logs, or deployment env files unless explicitly protected; logs and state captures must redact secrets and raw logs require operational-admin permissions.
- `DESIGN-REQ-015` (migration, 15. Failure and rollback semantics): Failure and rollback semantics - Invalid input, auth/policy failures, lock conflicts, Compose failures, pull/recreate failures, and verification failures fail fast; automatic multi-attempt retries are off by default, and rollback is an explicit audited update.
- `DESIGN-REQ-016` (integration, 16. Interaction with task execution; 17. Interaction with Settings information architecture): Task execution and Settings IA integration - Deployment update is operational tool work invokable from UI, scheduled maintenance, admin tasks, or future release workflows, and remains within Settings Operations rather than becoming top-level navigation.
- `DESIGN-REQ-017` (observability, 19. Observability): Operator-facing progress states - The workflow exposes queue, validation, lock, capture, persistence, pull, recreate, verify, capture-after, success, failure, and partial-verification states with concise messages while detailed output stays in artifacts.
- `DESIGN-REQ-018` (non-goal, 4.2 Non-goals): Explicit exclusions remain enforced - The system must not replace GitOps or Kubernetes, manage non-allowlisted stacks or host paths, treat deployment updates as agent instruction skills, or silently roll back without policy.

## Ordered Story Candidates

### STORY-001: Policy-gated deployment update API

- Short name: `deployment-update-api`
- Jira issue type: `Story`
- Source reference: `docs/Tools/DockerComposeUpdateSystem.md`; sections: 7. API contract, 10.1 Validate request, 13.1 Authorization, 13.2 Allowlisted stacks, 13.3 No arbitrary shell
- Dependencies: None
- Independent test: API-level tests submit valid and invalid deployment update requests against configured policy and assert accepted responses, fail-closed validation errors, current-state output, and image-target output without invoking Docker.
- Description: As a MoonMind administrator, I need typed deployment update APIs that enforce deployment policy before any Compose operation can start, so updates can be submitted and inspected without exposing arbitrary host controls.
- Scope:
  - POST /api/v1/operations/deployment/update accepts the typed request shape and returns a queued run/workflow identity.
  - GET current stack state exposes configured/running image, service state, health, and last run.
  - GET image targets returns allowlisted repositories, references, recent tags, and digest recommendation metadata.
  - Validation rejects unauthorized callers, unknown stacks, unapproved repositories, invalid references, unpermitted modes/options, and missing reasons.
- Out of scope:
  - Executing Docker Compose commands.
  - Rendering the Settings UI.
  - Automatic rollback.
- Acceptance criteria:
  - Admin callers can submit a valid update request and receive deploymentUpdateRunId, taskId or workflowId, and QUEUED status.
  - Non-admin callers and ordinary task submitters cannot submit deployment updates.
  - Unknown stacks, caller-provided paths, unapproved repositories, unrecognized flags, invalid references, and missing reasons are rejected before workflow/tool execution.
  - Current deployment state and allowed image-target endpoints return the documented typed shapes.
  - Mutable tag responses identify digest pinning as recommended and preserve requested-reference versus resolved-digest semantics where known.
  - The API does not accept arbitrary shell command text, arbitrary Compose file paths, arbitrary host paths, or updater runner image choices.
- Requirements:
  - Expose the documented typed backend endpoints.
  - Bind request validation to deployment policy and admin authorization.
  - Represent unsupported values as explicit errors rather than hidden fallback behavior.
  - Keep deployment update permissions distinct from ordinary task submission.
- Source design coverage:
  - `DESIGN-REQ-003`: Admin authorization and deployment policy enforcement - Only administrators may initiate updates, and stack names, host paths, Compose files, image repositories, modes, flags, and runner images must be checked against deployment policy.
  - `DESIGN-REQ-004`: No arbitrary shell or general Docker UI - The feature must not expose user-authored shell snippets, arbitrary Compose paths, unrecognized flags, or a general-purpose Docker control surface.
  - `DESIGN-REQ-005`: Typed API contract for update submission and state reads - The backend exposes typed endpoints for submitting updates, reading current stack state, and listing allowed image targets.
  - `DESIGN-REQ-008`: Mutable tags and digest recording - Mutable tags may be allowed, but resolved digests must be recorded when available and displayed distinctly from the requested reference.
  - `DESIGN-REQ-018`: Explicit exclusions remain enforced - The system must not replace GitOps or Kubernetes, manage non-allowlisted stacks or host paths, treat deployment updates as agent instruction skills, or silently roll back without policy.
- Assumptions:
  - Existing authentication and role checks can distinguish administrators from ordinary users.
- Needs clarification: None

### STORY-002: Typed deployment update tool contract

- Short name: `deployment-tool-contract`
- Jira issue type: `Story`
- Source reference: `docs/Tools/DockerComposeUpdateSystem.md`; sections: 8. Executable tool contract, 16. Interaction with task execution, 20. Locked decisions
- Dependencies: STORY-001
- Independent test: Tool registry and plan-contract tests resolve deployment.update_compose_stack, validate its schema and capability requirements, and reject plan nodes that attempt unsupported inputs or arbitrary shell execution.
- Description: As an operator of MoonMind workflows, I need deployment.update_compose_stack registered as a typed privileged executable tool, so deployment updates can be orchestrated through the plan/tool system rather than ad hoc shell execution.
- Scope:
  - Register deployment.update_compose_stack with version, input schema, output schema, executor activity type, capability selector, admin security policy, timeout policy, retry policy, and non-retryable error codes.
  - Support representative plan-node invocation from operational workflows.
  - Make the target image the tool input while keeping runner-image selection outside user inputs.
- Out of scope:
  - Building the full UI.
  - Implementing all Compose command execution internals.
- Acceptance criteria:
  - The tool registry exposes deployment.update_compose_stack version 1.0.0 with the documented required inputs and outputs.
  - The tool requires deployment_control and docker_admin capabilities and admin authorization.
  - The tool schema accepts stack, image.repository, image.reference, optional resolvedDigest, mode, options, and reason.
  - The tool output schema includes status, stack, requestedImage, resolvedDigest, updatedServices, runningServices, and artifact refs.
  - Retry policy uses max_attempts 1 with documented non-retryable error codes.
  - Plan-node validation supports the representative skill tool invocation and rejects arbitrary shell snippets or runner image overrides.
- Requirements:
  - Keep deployment update as executable operational work in the tool/plan system.
  - Avoid treating deployment update as an agent instruction bundle.
  - Preserve exact target image input semantics without hidden transformations.
- Source design coverage:
  - `DESIGN-REQ-006`: Typed executable tool contract - Deployment update is represented by the privileged executable tool deployment.update_compose_stack with schema, capability, timeout, retry, output, and security requirements.
  - `DESIGN-REQ-016`: Task execution and Settings IA integration - Deployment update is operational tool work invokable from UI, scheduled maintenance, admin tasks, or future release workflows, and remains within Settings Operations rather than becoming top-level navigation.
  - `DESIGN-REQ-002`: Target MoonMind image is the operator choice - The UI and API center on selecting an allowlisted MoonMind target image tag or digest, while the privileged updater runner image remains deployment-controlled configuration.
  - `DESIGN-REQ-004`: No arbitrary shell or general Docker UI - The feature must not expose user-authored shell snippets, arbitrary Compose paths, unrecognized flags, or a general-purpose Docker control surface.
- Assumptions:
  - The existing tool registry supports capability-based selection for privileged executors.
- Needs clarification: None

### STORY-003: Serialized Compose desired-state execution

- Short name: `compose-update-execution`
- Jira issue type: `Story`
- Source reference: `docs/Tools/DockerComposeUpdateSystem.md`; sections: 9. Desired state storage, 10. Execution lifecycle, 11. Updater runner execution model
- Dependencies: STORY-002
- Independent test: Boundary tests drive the tool executor with a fake deployment policy and fake Compose runner, assert lock serialization, desired-state persistence order, command construction for both modes, digest recording, and rejection of caller-selected files or runner images.
- Description: As a deployment administrator, I need MoonMind to persist the requested image and run the policy-controlled Compose update lifecycle under a per-stack lock, so a requested update survives restarts and cannot race with another update.
- Scope:
  - Acquire and release a per-stack deployment lock.
  - Capture before state before mutating desired state.
  - Persist the requested image reference to an allowlisted deployment env file or equivalent state store before Compose up.
  - Resolve/record digest when available.
  - Run policy-controlled docker compose pull and docker compose up equivalents for changed_services and force_recreate modes.
  - Support trusted deployment-control worker or ephemeral updater container implementation modes without exposing runner selection to users.
- Out of scope:
  - Rendering detailed progress in the UI.
  - Automatic retry loops or rollback.
- Acceptance criteria:
  - A second update for the same stack is rejected with DEPLOYMENT_LOCKED or queued only according to explicit policy.
  - Before-state capture occurs before desired-state persistence.
  - The desired image is persisted before Compose up is invoked.
  - changed_services mode runs the documented pull/up behavior without force-recreate.
  - force_recreate mode adds force-recreate behavior only when policy permits it.
  - removeOrphans and wait options adjust command construction only through recognized policy-controlled flags.
  - The executor never edits arbitrary caller-selected files and never accepts caller-selected updater runner images.
  - The privileged Docker access path is restricted to deployment-control infrastructure.
- Requirements:
  - Serialize update runs per stack.
  - Persist desired target state durably before service recreation.
  - Perform Compose command construction from typed inputs and policy only.
  - Support both privileged worker and ephemeral updater container as implementation modes.
- Source design coverage:
  - `DESIGN-REQ-007`: Desired image state persistence before Compose up - The requested image reference must be written to an allowlisted deployment env file or equivalent state store before services are brought up so restarts preserve the target state.
  - `DESIGN-REQ-008`: Mutable tags and digest recording - Mutable tags may be allowed, but resolved digests must be recorded when available and displayed distinctly from the requested reference.
  - `DESIGN-REQ-009`: Serialized per-stack update lifecycle - Only one update may run per Compose stack; the workflow validates, acquires a deployment lock, captures before state, persists desired state, pulls images, recreates services, verifies, captures after state, releases the lock, and reports.
  - `DESIGN-REQ-010`: Compose command execution modes - The tool performs policy-controlled equivalents of docker compose pull and docker compose up, supporting changed-service recreation and force-recreate-all modes.
  - `DESIGN-REQ-011`: Privileged runner boundary - Compose commands run only through trusted deployment-control infrastructure, either a privileged worker or ephemeral updater container; Docker socket access is not exposed to ordinary runtimes, agents, repo workspaces, or user-authored tools.
- Assumptions:
  - A fake or adapter-backed Compose runner can be used in hermetic tests without Docker socket access.
- Needs clarification: None

### STORY-004: Deployment verification, artifacts, and progress

- Short name: `deployment-verification-audit`
- Jira issue type: `Story`
- Source reference: `docs/Tools/DockerComposeUpdateSystem.md`; sections: 12. Verification model, 14. Audit and artifacts, 19. Observability
- Dependencies: STORY-003
- Independent test: Executor tests simulate successful, failed, and partially verified updates and assert status decisions, artifact refs, redaction behavior, audit fields, and progress-state emission without placing command output in workflow history.
- Description: As an administrator reviewing an update, I need MoonMind to verify the deployed state and preserve audit artifacts, so a run is only successful when the desired state is proven and the before/after evidence is durable.
- Scope:
  - Verify Compose service state, health, image identity/digests, optional smoke checks, and orphan removal expectations.
  - Write immutable before state, command log, verification, and after state artifacts.
  - Redact secrets from state captures and command logs.
  - Record structured audit fields and final status.
  - Expose progress states and concise progress messages while keeping command output in artifacts.
- Out of scope:
  - Implementing the visible Settings card.
  - Starting rollbacks.
- Acceptance criteria:
  - A run is marked SUCCEEDED only when expected services are running, health checks pass when present, image IDs match the requested target or resolved digest where applicable, requested smoke checks pass, and orphan expectations hold.
  - If verification cannot prove the requested desired state, the final status is FAILED or PARTIALLY_VERIFIED, never SUCCEEDED.
  - Every run writes beforeStateArtifactRef, commandLogArtifactRef, verificationArtifactRef, and afterStateArtifactRef.
  - Audit output includes run/workflow/task IDs where applicable, stack, operator identity and role, reason, image request, resolved digest, mode, options, timestamps, final status, and failure reason when applicable.
  - Secrets, auth tokens, registry credentials, and sensitive environment variables are redacted from artifacts and logs.
  - Progress states include the documented lifecycle values with short messages; detailed command output remains in artifacts.
- Requirements:
  - Make verification a success gate.
  - Store durable audit and artifact evidence for every run.
  - Redact sensitive values before artifact publication or UI display.
  - Represent partial verification explicitly.
- Source design coverage:
  - `DESIGN-REQ-012`: Verification gates success - Compose state, health, image identity/digest matching, optional smoke checks, and orphan cleanup must be verified; unresolved desired state cannot be marked SUCCEEDED.
  - `DESIGN-REQ-013`: Durable artifacts and audit record - Every run records identity, reason, image, options, timestamps, result, failure reason, and immutable before state, command log, verification, and after state artifacts.
  - `DESIGN-REQ-014`: Secret redaction and artifact access controls - Registry credentials and sensitive values must not appear in image refs, UI defaults, logs, or deployment env files unless explicitly protected; logs and state captures must redact secrets and raw logs require operational-admin permissions.
  - `DESIGN-REQ-017`: Operator-facing progress states - The workflow exposes queue, validation, lock, capture, persistence, pull, recreate, verify, capture-after, success, failure, and partial-verification states with concise messages while detailed output stays in artifacts.
- Assumptions:
  - Existing artifact services can store immutable JSON/text artifacts and enforce admin-only raw log access.
- Needs clarification: None

### STORY-005: Settings Operations deployment update UI

- Short name: `operations-update-ui`
- Jira issue type: `Story`
- Source reference: `docs/Tools/DockerComposeUpdateSystem.md`; sections: 6. Settings -> Operations UX, 17. Interaction with Settings information architecture, 18. UI copy recommendations
- Dependencies: STORY-001, STORY-004
- Independent test: React/Vitest tests render the Operations section with mocked deployment state and image targets, exercise target selection, warnings, confirmation, submission, recent actions, and permission-aware artifact/log links.
- Description: As a MoonMind administrator, I need a Settings -> Operations Deployment Update card that shows current deployment state and lets me submit a confirmed target image update, so I can update MoonMind without SSH access.
- Scope:
  - Add the Deployment Update card under Settings -> Operations.
  - Show current deployment, update target controls, update mode, policy-controlled options, reason entry, confirmation modal, mutable-tag and force-recreate warnings, and recent actions.
  - Submit typed API requests and show run links, artifact links, before/after summaries, status, timestamps, operator, reason, requested image, and resolved digest.
  - Keep the feature in Settings subsection routing rather than top-level navigation.
- Out of scope:
  - Implementing backend policy enforcement.
  - Displaying raw command logs to non-operational-admin users.
- Acceptance criteria:
  - The Deployment Update card appears under /tasks/settings?section=operations and not as top-level navigation.
  - Current deployment shows stack name, Compose project, configured image, running image ID or digest when available, version/build when available, health summary, and last run result.
  - Update target controls prefer digest-pinned or release-tagged choices and show a mutable-tag warning for latest or other mutable references.
  - Update mode defaults to Restart changed services and offers Force recreate all services only when policy permits it, with the documented warning.
  - The operator must enter a reason and confirm current image, target image, mode, stack, expected affected services, mutable tag warning when applicable, and restart warning before submission.
  - Recent actions show status, requested image, resolved digest, operator, reason, timestamps, run detail link, logs artifact link, and before/after summary.
  - Raw command-log links are hidden or disabled for users without operational-admin permissions.
- Requirements:
  - Expose the operator-facing deployment update workflow in Settings Operations.
  - Make target MoonMind image the primary UI choice.
  - Keep updater runner image internal and absent from ordinary UI controls.
  - Use concise progress states and artifact links for review.
- Source design coverage:
  - `DESIGN-REQ-001`: Mission Control Operations update surface - Administrators need a Settings -> Operations Deployment Update card that exposes current state, target selection, update mode, options, reason, confirmation, and recent runs.
  - `DESIGN-REQ-002`: Target MoonMind image is the operator choice - The UI and API center on selecting an allowlisted MoonMind target image tag or digest, while the privileged updater runner image remains deployment-controlled configuration.
  - `DESIGN-REQ-016`: Task execution and Settings IA integration - Deployment update is operational tool work invokable from UI, scheduled maintenance, admin tasks, or future release workflows, and remains within Settings Operations rather than becoming top-level navigation.
  - `DESIGN-REQ-017`: Operator-facing progress states - The workflow exposes queue, validation, lock, capture, persistence, pull, recreate, verify, capture-after, success, failure, and partial-verification states with concise messages while detailed output stays in artifacts.
- Assumptions:
  - The existing Settings page supports an Operations subsection route.
- Needs clarification: None

### STORY-006: Explicit failure and rollback controls

- Short name: `rollback-failure-controls`
- Jira issue type: `Story`
- Source reference: `docs/Tools/DockerComposeUpdateSystem.md`; sections: 15. Failure and rollback semantics, 4.2 Non-goals, 20. Locked decisions
- Dependencies: STORY-004
- Independent test: Workflow/tool tests simulate each failure class and a rollback request, asserting fail-fast terminal status, no automatic retry by default, rollback-as-new-update behavior, required reason/confirmation, artifact references, and policy rejection when a safe previous image cannot be derived.
- Description: As an operations administrator, I need failed updates and rollbacks to remain explicit audited actions, so partial deployment changes do not trigger hidden retries or silent rollbacks.
- Scope:
  - Fail fast for invalid input, authorization failure, policy violation, unavailable deployment lock, Compose config validation failure, image pull failure, service recreation failure, and verification failure.
  - Disable automatic multi-attempt deployment retries by default.
  - Allow a rollback affordance only when before-state artifacts can construct a safe previous target image reference.
  - Run rollback through the same admin authorization, reason, confirmation, lock, artifact, and verification path as any other deployment update.
  - Make silent rollback impossible unless a separately documented policy explicitly enables it.
- Out of scope:
  - Designing a full GitOps or Kubernetes rollback system.
  - Managing non-allowlisted stacks or host paths.
- Acceptance criteria:
  - Each documented failure class produces a clear failed or partially verified result and actionable failure reason.
  - Deployment updates do not perform automatic multi-attempt retries by default.
  - A rollback request is submitted as a normal deployment update to a previous image reference, with admin authorization, reason, confirmation, lock acquisition, before/after artifacts, and verification.
  - The UI or API offers rollback only when before-state artifacts contain enough information to construct a safe target image reference.
  - The system never silently rolls back after failure unless an explicit separate policy enables automatic rollback.
  - Rollback and failure records remain visible in recent actions and audit output.
- Requirements:
  - Keep retries and rollback operator-driven by default.
  - Preserve the same security, audit, artifact, and verification requirements for rollback as for forward updates.
  - Do not expand the feature into general GitOps, Kubernetes, or non-allowlisted stack management.
- Source design coverage:
  - `DESIGN-REQ-015`: Failure and rollback semantics - Invalid input, auth/policy failures, lock conflicts, Compose failures, pull/recreate failures, and verification failures fail fast; automatic multi-attempt retries are off by default, and rollback is an explicit audited update.
  - `DESIGN-REQ-018`: Explicit exclusions remain enforced - The system must not replace GitOps or Kubernetes, manage non-allowlisted stacks or host paths, treat deployment updates as agent instruction skills, or silently roll back without policy.
  - `DESIGN-REQ-003`: Admin authorization and deployment policy enforcement - Only administrators may initiate updates, and stack names, host paths, Compose files, image repositories, modes, flags, and runner images must be checked against deployment policy.
  - `DESIGN-REQ-013`: Durable artifacts and audit record - Every run records identity, reason, image, options, timestamps, result, failure reason, and immutable before state, command log, verification, and after state artifacts.
- Assumptions:
  - Before-state artifacts include enough image identity information for at least some previous-image rollback offers.
- Needs clarification: None

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-005
- `DESIGN-REQ-002` -> STORY-002, STORY-005
- `DESIGN-REQ-003` -> STORY-001, STORY-006
- `DESIGN-REQ-004` -> STORY-001, STORY-002
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-002
- `DESIGN-REQ-007` -> STORY-003
- `DESIGN-REQ-008` -> STORY-001, STORY-003
- `DESIGN-REQ-009` -> STORY-003
- `DESIGN-REQ-010` -> STORY-003
- `DESIGN-REQ-011` -> STORY-003
- `DESIGN-REQ-012` -> STORY-004
- `DESIGN-REQ-013` -> STORY-004, STORY-006
- `DESIGN-REQ-014` -> STORY-004
- `DESIGN-REQ-015` -> STORY-006
- `DESIGN-REQ-016` -> STORY-002, STORY-005
- `DESIGN-REQ-017` -> STORY-004, STORY-005
- `DESIGN-REQ-018` -> STORY-001, STORY-006

## Dependencies

- `STORY-001` depends on no prior story.
- `STORY-002` depends on STORY-001.
- `STORY-003` depends on STORY-002.
- `STORY-004` depends on STORY-003.
- `STORY-005` depends on STORY-001, STORY-004.
- `STORY-006` depends on STORY-004.

## Out Of Scope

- General-purpose Docker UI or shell runner: The design explicitly restricts the feature to typed deployment update inputs and known command forms.
- Non-admin deployment control: Only administrators may initiate deployment updates.
- Operator-selected updater runner images: Runner images are privileged infrastructure and deployment-controlled.
- GitOps or Kubernetes replacement: The document scopes the feature to allowlisted Docker Compose stacks.
- Silent rollback: Rollback is a separate explicit audited update unless future policy says otherwise.
- Spec generation: Breakdown only; downstream specify creates feature specs.

## Coverage Gate

PASS - every major design point is owned by at least one story.
