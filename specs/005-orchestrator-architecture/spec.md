# Feature Specification: MoonMind Orchestrator Implementation

**Feature Branch**: `005-orchestrator-architecture`
**Created**: 2025-11-13
**Status**: Draft
**Input**: User description: "Create a spec which will implement the orchestrator architecture defined by docsOrchestratorArchitecture.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Autonomous Fix Run (Priority: P1)

MoonMind operator submits a high-level instruction such as "repair missing dependency for the API service," and the orchestrator analyzes logs, proposes a patch to allowed files, rebuilds the target image, restarts the affected service, and validates health without requiring the operator to touch Docker commands manually.

**Why this priority**: Delivers the core value: translating intent into a repeatable build/relaunch cycle that keeps services healthy with minimal operator time.

**Independent Test**: Provide a controlled failing service, trigger one orchestrator run, and verify it produces a patch, rebuilds only the targeted service, restarts it, and records verification artifacts end-to-end.

**Acceptance Scenarios**:

1. **Given** a reproducible failure with clear logs, **When** the operator launches an orchestrator run, **Then** the system outputs a structured ActionPlan, applies an allowed file patch, and persists the resulting diff for review.
2. **Given** a successful rebuild + restart, **When** the orchestrator performs the configured health checks, **Then** it marks the run successful only after container state and HTTP health checks pass within the allowed time window.

---

### User Story 2 - Run Visibility & Audit (Priority: P2)

Operators need to inspect ongoing and historical runs to see what the orchestrator changed, which Docker commands were executed, and how health verification progressed so they can trust automated fixes or intervene quickly.

**Why this priority**: Without transparent status, operators cannot approve or troubleshoot automated remediation, undermining adoption.

**Independent Test**: Trigger a run while observing the operator interface/log store and confirm that each action emits timestamps, command summaries, artifacts (diff, build log, health log), and final disposition.

**Acceptance Scenarios**:

1. **Given** an in-progress run, **When** an operator opens the run record, **Then** they see step-level status (plan, patch, build, restart, verify, rollback) with links to stored artifacts.
2. **Given** a failed verification, **When** the operator inspects the run, **Then** the orchestrator exposes failure cause, rollback result, and the exact files touched so manual remediation can continue safely.

---

### User Story 3 - Policy-Governed Rollback & Approvals (Priority: P3)

When an instruction targets sensitive services or a verification step fails, the orchestrator must honor approval gates, automatically rollback to the prior state, and document what happened so the change can be re-reviewed.

**Why this priority**: Safeguards production stability by ensuring automation never leaves services in an unknown state and enforces human approvals on high-risk assets.

**Independent Test**: Configure a policy that requires approval for the API service, trigger a run without approval and another run whose verification fails, and confirm the system blocks the first and rolls back the second while recording evidence.

**Acceptance Scenarios**:

1. **Given** a protected service without recorded approval, **When** the orchestrator receives an instruction to modify it, **Then** the run halts before editing files and emits an approval-needed status.
2. **Given** a run that fails health checks, **When** rollback executes, **Then** the orchestrator reverts touched files, restarts the prior image, and marks the run as rolled back with the failure reason documented.

---

### Edge Cases

- Docker daemon or socket unavailable: orchestrator must fail fast, log the infrastructure issue, and skip any patching to avoid partial changes.
- Instruction targets a service missing from `docker-compose.yml`: run should validate service names upfront and return an actionable error before altering files.
- Health endpoint never becomes ready within the timeout: orchestrator must attempt rollback once, then mark the run failed with references to verification logs.
- Approval received mid-run: system should resume the blocked step without re-running earlier successful steps, provided artifacts remain valid.
- Log or artifact storage unavailable: run should continue core remediation but flag missing artifacts so operators can rerun once storage is restored.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The orchestrator must run as a dedicated service (mm-orchestrator) within the existing docker-compose project, mounting the repository workspace and host Docker socket as described in `docs/OrchestratorArchitecture.md`.
- **FR-002**: Each run must accept a high-level instruction, derive or validate the target service, and expand it into a structured ActionPlan with explicit steps (analyze, patch, build, restart, verify, rollback).
- **FR-003**: The orchestrator must restrict file edits to the approved allow list (service Dockerfiles and dependency manifests) and block all other filesystem changes unless a policy override exists.
- **FR-004**: Patch generation must capture diffs before and after modifications, store them as artifacts per run, and expose the diff path to operators for audit.
- **FR-005**: Builds must be executed through the Compose control plane for only the targeted service while streaming build output to storage so operators can review compiler or dependency results.
- **FR-006**: Service restarts must redeploy only the targeted service via Compose's dependency-skipping semantics and confirm that all other containers remain untouched.
- **FR-007**: Verification must include at least container-state checks plus configurable HTTP health probes with backoff and a maximum wait time, marking the run failed if any probe does not succeed.
- **FR-008**: When verification fails, the orchestrator must execute rollback by reverting touched files, rebuilding if needed, restarting the previous image, and documenting the rollback outcome.
- **FR-009**: Approval policies must be enforced before the patch step, requiring affirmative approval records for protected services and logging the approver identity with timestamps.
- **FR-010**: Every step in the run lifecycle must emit structured status updates (state, timestamp, key message, artifact references) so MoonMind's UI and logs can present real-time progress.
- **FR-011**: The orchestrator must persist per-run artifacts inside the designated spec workflow artifact store (default `spec_workflows` namespace), covering diffs, build logs, compose outputs, health logs, and rollback notes.
- **FR-012**: Metrics counters and timers (e.g., runs attempted, success/failure counts, verification duration, rollback frequency) must be emitted via the configured StatsD endpoint when `STATSD_HOST/PORT` or `SPEC_WORKFLOW_METRICS_*` are set.

### Key Entities *(include if feature involves data)*

- **OrchestratorRun**: Represents a single instruction execution; stores run ID, instruction text, target service, timestamps per step, approval references, and final status.
- **ActionPlan**: Structured list of ordered steps with parameters (files to edit, build targets, health URLs, rollback strategy) generated before execution and updated as steps complete.
- **RunArtifact**: Metadata record pointing to stored diffs, build logs, verification logs, and rollback reports, including checksum and retention info for audit trails.
- **ApprovalGate**: Policy record that defines which services require approval, acceptable approver roles, and the validity window of an approval token referenced by runs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 90% of successful orchestrator runs complete patch -> verify within 20 minutes for single-service fixes using the documented orchestrator workflow.
- **SC-002**: 100% of runs touching protected services either include a valid approval record before patching begins or are blocked with an approval-required status.
- **SC-003**: 95% of runs produce a complete artifact set (diff, build log, health log, rollback log when applicable) retrievable by operators within 1 minute of run completion.
- **SC-004**: Automation reduces manual intervention for targeted service recovery by at least 60% per month, measured by operator-reported manual command counts compared to the prior baseline.

## Assumptions & Dependencies

- RabbitMQ broker, Celery worker, and MoonMind API service are already running per the Spec Workflow Verification Checklist so orchestrator tasks can be queued and monitored.
- Host Docker daemon access via `/var/run/docker.sock` remains available and secured; orchestrator runs on trusted hosts where this access is acceptable.
- Service health endpoints (or equivalent checks) are documented so verification can be configured without further discovery.
- Secrets such as GitHub and Codex tokens are provided through environment variables or Docker secrets and are masked in logs.
- The spec workflow artifact storage location (local directory or object store namespace) has sufficient capacity and backup to retain logs for auditing periods.

## Scope Boundaries

- Feature focuses on orchestrating Docker Compose environments; migrating to Kubernetes or adding blue/green routing is explicitly out of scope.
- Log analysis may leverage existing MoonMind tooling or LLM helpers but designing new diagnostic AI models is not part of this effort.
- Manual PR creation or CI integration beyond storing diffs is excluded; those workflows remain manual or handled by other specs.
- Multi-service coordinated deployments (restart of multiple services simultaneously) are deferred until orchestration maturity is proven on single-service runs.
