# Feature Specification: Celery Chain Workflow Integration

**Feature Branch**: `001-celery-chain-workflow`  
**Created**: 2025-11-02  
**Status**: Draft (Updated 2026-02-14 for 015 umbrella alignment)  
**Input**: User description: "Awesome—MoonMind is a great place to centralize this. Below is a pragmatic blueprint to plug Codex CLI and GitHub Spec Kit into MoonMind so you can trigger, monitor, and land changes (branches/PRs) from Spec-driven tasks. I’ll also spell out the exact credentials you’ll need and where they live. Spec this out with Celery Chain handling the task chains."

## User Scenarios & Testing *(mandatory)*

### User Story 0 - Fast Worker Launch with Skills-First Defaults (Priority: P1)

MoonMind operator wants the shortest path to launch an authenticated Codex worker and Gemini embedding runtime, while keeping Speckit always available and skills-first stage execution enabled by default.

**Why this priority**: Worker startup/auth determinism and embedding readiness are prerequisites for every workflow run.

**Independent Test**: Perform one-time Codex auth on the shared worker volume, start `rabbitmq`, `api`, `celery_codex_worker`, and `celery_gemini_worker`, then verify startup logs and runtime settings show expected queue bindings, preflight status, and Google embedding defaults.

**Acceptance Scenarios**:

1. **Given** `DEFAULT_EMBEDDING_PROVIDER=google`, `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`, and `GOOGLE_API_KEY` are configured, **When** services start, **Then** embedding runtime resolves to Google Gemini without additional runtime overrides.
2. **Given** Codex auth is performed once on `CODEX_VOLUME_NAME`, **When** `celery_codex_worker` restarts, **Then** startup preflight succeeds non-interactively and processing can begin immediately.
3. **Given** a workflow stage executes under default policy, **When** task states are persisted, **Then** payloads include selected skill and execution path metadata (`selectedSkill`, `executionPath`) for observability.

### User Story 1 - Trigger Next Spec Phase (Priority: P1)

MoonMind operator chooses "Run next Spec Kit phase" in the UI, and the system uses a preconfigured Celery Chain to discover the next incomplete phase, submit the job to Codex Cloud, poll for a diff, and publish a branch + PR without the operator handling any manual steps.

**Why this priority**: Enables the primary value proposition—MoonMind can move a Spec-driven task from planning to PR-ready delivery in one click.

**Independent Test**: Execute the workflow with a known Spec Kit tasks file in a staging repo and verify the branch, PR, and task metadata are produced without manual intervention.

**Acceptance Scenarios**:

1. **Given** a Spec Kit project with pending phases and valid credentials, **When** the operator invokes the workflow, **Then** the Celery Chain completes all steps and reports the resulting task ID, branch, and PR link back to MoonMind.
2. **Given** a completed phase list (no work left), **When** the operator invokes the workflow, **Then** the system short-circuits after discovery and returns a descriptive "no remaining phases" status without running downstream tasks.

---

### User Story 2 - Monitor Workflow Progress (Priority: P2)

MoonMind operator opens a workflow run detail page and sees real-time status updates for each Celery task in the chain, including failures surfaced with actionable error messages and JSONL artifacts.

**Why this priority**: Operators must be able to observe progress to trust the automation and react quickly to failed steps.

**Independent Test**: Trigger a workflow in a test environment while capturing the Celery task events and confirm MoonMind renders each chain stage’s status changes and links to stored logs.

**Acceptance Scenarios**:

1. **Given** an in-progress Celery Chain, **When** the operator views the run details, **Then** each chain task is represented with its current state, timestamps, and the latest message payload.
2. **Given** a failed downstream task (e.g., PR creation), **When** the operator opens the run details, **Then** the UI highlights the failure, surfaces the Celery error context, and provides retry guidance.

---

### User Story 3 - Retry or Resume Failed Chains (Priority: P3)

MoonMind operator selects a workflow run that stopped at a specific Celery task and requests a retry, which replays the chain from the failing task forward while reusing prior outputs (e.g., task ID) when safe.

**Why this priority**: Reduces operational toil by allowing quick recovery from transient issues such as network hiccups or GitHub rate limits.

**Independent Test**: Induce a controlled failure at the PR creation step, then trigger the retry action and confirm the chain resumes at that point and completes successfully.

**Acceptance Scenarios**:

1. **Given** a chain that failed after Codex produced a patch, **When** the operator selects "retry from failure," **Then** the resume task restarts at the apply/PR stage and completes using the stored diff.
2. **Given** a failure caused by missing credentials, **When** the operator retries without resolving the credential issue, **Then** the workflow halts immediately with guidance to refresh the secret.

### Edge Cases

- Celery worker restarts mid-chain: ensure states persist so the workflow can resume or mark the run as failed with rollback guidance.
- Codex returns no diff (e.g., clarification required): system should capture the reason and notify MoonMind instead of looping indefinitely.
- Credentials (Codex or GitHub) expire between tasks: the chain must stop gracefully and emit a remediation message without leaving partial branches.
- Spec tasks file missing or malformed: discovery task should fail fast with validation errors captured for the operator.
- Apply step encounters merge conflicts: workflow should surface the conflict details and store the patch artifact for manual resolution.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The provider must compose a Celery Chain with discrete tasks for discovery, submission, application, PR publication, and run finalization.
- **FR-002**: The discovery task must parse the configured Spec Kit task source and return the next actionable phase or a "no work" signal.
- **FR-003**: The submission task must invoke Codex Cloud using the configured environment and model, capture the returned task identifier, and persist streamed JSONL logs.
- **FR-004**: The apply task must poll the Codex Cloud diff endpoint until a patch is applied or a terminal error occurs, capturing conflict artifacts when present.
- **FR-005**: The PR task must ensure a branch exists (creating it when absent), push commits, and create or update a pull request through the GitHub automation interface using the mounted credentials.
- **FR-006**: Each Celery task must emit structured status updates (state, timestamps, payload references) that MoonMind can surface in the UI and log store.
- **FR-007**: The chain must persist intermediate artifacts (task ID, branch name, PR URL, log paths) in a run-scoped record accessible after completion or failure.
- **FR-008**: The workflow must enforce idempotency by deriving branch names from the feature identifier and reusing existing pull requests when repeats occur.
- **FR-009**: The provider must expose retry semantics that resume the chain from the failing task when safe to do so, otherwise starting a new run with operator consent.
- **FR-010**: Credential validation must occur before tasks that need them, with clear failure messaging when tokens or environment identifiers are missing or invalid.
- **FR-011**: Workflow stage execution must resolve through a skills-first policy layer, with Speckit as the default skill for backward-compatible behavior.
- **FR-012**: Speckit capability checks must execute at worker startup for Spec Kit and Gemini worker runtimes so Speckit remains always available.
- **FR-013**: Stage task-state payloads and run artifacts must record selected skill and execution path (`skill`, `direct_fallback`, or `direct_only`) for each stage.
- **FR-014**: Skills policy controls must support global enable/disable, canary percentage, fallback enablement, and per-stage skill overrides via configuration.
- **FR-015**: Docker Compose and quickstart guidance must define a fastest startup path for authenticated Codex workers and Google Gemini embeddings.
- **FR-016**: Missing startup prerequisites (Codex auth volume, Codex/GitHub credentials, Google API key when Google embeddings are selected) must fail fast with actionable remediation.

### Key Entities *(include if feature involves data)*

- **SpecWorkflowRun**: Represents a single MoonMind invocation; stores feature identifier, Celery chain ID, task status map, artifact locations, and timestamps.
- **CeleryTaskState**: Captures per-task execution metadata including task name, current state, start/end timestamps, log references, and structured payload.
- **WorkflowCredentialAudit**: References the secret bundle used during execution, including validation timestamp and any detected issues.
- **WorkflowArtifact**: Describes generated outputs by type (e.g., `codex_logs`, `codex_patch`) with paths to stored files or signed URLs for operator access.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 95% of workflow runs with valid inputs reach PR creation within 15 minutes of submission under nominal load.
- **SC-002**: 100% of Celery tasks emit structured status updates consumable by MoonMind with no missing state transitions in audit logs.
- **SC-003**: Operators report (via post-run feedback or survey) that 90% of runs provide sufficient context to resolve failures without escalating to engineering.
- **SC-004**: Automation reduces manual time spent per Spec Kit phase handoff by at least 70% compared to the prior semi-manual process over one release cycle.
- **SC-005**: Startup quickstart from a clean `.env` produces ready `celery_codex_worker` and `celery_gemini_worker` processes without interactive prompts.
- **SC-006**: 100% of discover/submit/publish task payloads include `selectedSkill` and `executionPath`.
- **SC-007**: Validation gate `./tools/test_unit.sh` passes with skills-first runtime changes enabled by default.

## Assumptions & Dependencies

- MoonMind has an operational Celery deployment using RabbitMQ 3.x for the broker and PostgreSQL as the Celery result backend so chain state and artifacts survive worker restarts.
- The RabbitMQ broker runs as a single node with default classic queues (no HA or quorum queue policies) aligned with the initial deployment footprint.
- Codex Cloud environment identifiers, authentication tokens, and GitHub credentials are stored in MoonMind’s secret store and mounted for workflow execution.
- Spec Kit task definitions are maintained in a repository-accessible location compatible with the discovery task parser.
- Network egress from the execution environment to Codex Cloud and GitHub APIs is permitted within organizational policy.
- Governance for branch naming conventions and PR review routing remains managed by existing MoonMind policies; this feature does not alter approval flows.
- Speckit skill definitions are mirrored in `.codex/skills` and `.agents/skills`, and remain installed in worker images.
- The initial skills-first implementation keeps existing `/api/workflows/speckit/*` API naming for compatibility, even when stage orchestration is generalized.
- The fastest deployment profile uses Google Gemini embeddings (`gemini-embedding-001`) and authenticated Codex CLI volumes.

### Scope Boundaries

- The feature does not modify Spec Kit task authoring tooling or generate new task content.
- Manual code reviews and human approval steps remain outside the automated chain.
- Enhancements to the Codex Cloud environment (e.g., new test suites) are managed separately from this workflow integration.
