# Feature Specification: Celery OAuth Volume Mounts

**Feature Branch**: `001-celery-oauth-volumes`  
**Created**: 2025-11-05  
**Status**: Draft  
**Input**: User description: "Celery OAuth: Spec out implementing the celery OAuth and volume approach documented by docsSpecKitAutomation.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reliable Codex Authentication (Priority: P1)

Spec Kit operators need Codex automation runs to reuse a persistent sign-in so Codex-driven phases finish without manual intervention.

**Why this priority**: Without persistent authentication every Codex task stalls for human re-login, blocking the automation promise of Spec Kit.

**Independent Test**: Trigger a Celery Codex phase and confirm it completes end-to-end without prompting for Codex authentication while recording the mounted volume that was used.

**Acceptance Scenarios**:

1. **Given** a Celery worker assigned to a Codex queue, **When** the worker launches a job container for a Spec run, **Then** the container mounts that worker’s dedicated Codex auth volume at the run’s home directory so subsequent Codex CLI calls use an existing login.
2. **Given** a Codex auth volume that lacks a valid login, **When** the pre-flight authentication check executes before the Codex phase, **Then** the run halts with an actionable error instructing the operator to refresh the sign-in for that specific volume.

---

### User Story 2 - Sharded Worker Routing (Priority: P2)

Platform reliability engineers want Codex-heavy workloads to distribute predictably across a limited pool of prepared workers.

**Why this priority**: Deterministic sharding prevents multiple workers from racing on the same credentials and keeps the login volumes healthy.

**Independent Test**: Dispatch Codex tasks with different affinity keys and verify that each routes to a consistent queue/worker pairing while non-Codex tasks continue on default queues.

**Acceptance Scenarios**:

1. **Given** two Spec runs with different repositories, **When** Codex tasks are enqueued, **Then** each run is routed to a stable `codex-{n}` queue derived from its shard key so the same worker volume serves all Codex steps for that run.
2. **Given** a non-Codex task submitted to Celery, **When** it is scheduled, **Then** it continues to use the default queue so Codex-specific routing never delays other workloads.

---

### User Story 3 - Credential Stewardship (Priority: P3)

Operations staff need a lightweight process to provision, audit, and recover Codex authentication without touching container internals.

**Why this priority**: Clear stewardship keeps the OAuth credentials compliant and reduces downtime when tokens expire or volumes move between hosts.

**Independent Test**: Follow the documented runbook to authenticate a fresh Codex volume, confirm status via the published check command, and restore service after intentionally expiring a token.

**Acceptance Scenarios**:

1. **Given** a newly created Codex auth volume, **When** an operator follows the documented sign-in steps, **Then** the login persists across multiple Codex runs without further human input.
2. **Given** an expiring or rotated Codex credential, **When** the operator uses the runbook to refresh it, **Then** the next Codex run succeeds without manual rework beyond the single reauthentication.

---

### Edge Cases

- Codex worker starts without its configured auth volume; the run should fail fast with guidance rather than silently defaulting to ephemeral storage.
- Pre-flight login check times out or the Codex service is unreachable; the system must log the failure and avoid starting the Codex phase until connectivity is restored.
- A Codex volume is accidentally shared between workers; the platform should detect duplicate assignments during startup to prevent token clobbering.
- The number of active Codex workers changes; routing logic must adjust shard mappings or provide clear steps for extending the shard count.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The automation platform MUST route all Codex phases through dedicated `codex-{n}` queues so each run consistently reaches its assigned worker shard.
- **FR-002**: Each Codex worker MUST map exactly one named persistent Codex auth volume and mount it into every job container’s Codex configuration path.
- **FR-003**: Before any Codex phase executes, the platform MUST perform an automated login status check using the worker’s assigned volume and block execution on failure.
- **FR-004**: When a login check fails, the system MUST surface an actionable message identifying the affected shard and offering remediation steps to refresh the volume’s credentials.
- **FR-005**: Deployment tooling MUST provide service definitions for the three Codex-focused workers and the three associated volumes so operators can start them with standard compose commands.
- **FR-006**: Runtime logging MUST record the Codex queue, shard identifier, and volume name attached to each run to support auditing and troubleshooting.
- **FR-007**: Operator documentation MUST include a runbook for authenticating each Codex volume once and for confirming status without entering containers manually.

### Key Entities *(include if feature involves data)*

- **Codex Auth Volume**: Persistent storage that holds ChatGPT OAuth artifacts for a single Codex worker; uniquely named and reused across runs.
- **Codex Worker Shard**: A Celery worker instance bound to one Codex queue and its corresponding auth volume; responsible for executing Codex phases of Spec runs routed to its shard.
- **Spec Automation Run**: A recorded automation execution that now references the Codex shard and volume used for its submission phase to support traceability.

## Assumptions

- Three Codex worker shards provide sufficient capacity for initial rollout; additional shards can be added later using the same pattern.
- Job containers continue to run as the non-root user expected by the Codex CLI so mounted credentials remain readable.
- Operators have existing access to Docker tooling and the ChatGPT sign-in flow needed to authenticate volumes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At least 95% of Codex automation runs during the first month complete without prompting for manual reauthentication.
- **SC-002**: Authentication failures detected during the pre-flight check are resolved within one business hour in 90% of cases by following the provided runbook.
- **SC-003**: Provisioning a new Codex worker from scratch, including initial volume authentication, takes under 15 minutes when executed by an on-call operator.
- **SC-004**: Operations reports confirm that 100% of Codex runs emit logs identifying their queue and volume, enabling complete traceability during audits.
