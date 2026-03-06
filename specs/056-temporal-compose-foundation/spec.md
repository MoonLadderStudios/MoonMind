# Feature Specification: Temporal Compose Foundation

**Feature Branch**: `044-temporal-compose-foundation`  
**Created**: 2026-03-05  
**Status**: Draft  
**Input**: User description: "Implement the Temporal foundation using docker compose in accordance with docs/Temporal/TemporalPlatformFoundation.md and docs/Temporal/TemporalArchitecture.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/TemporalPlatformFoundation.md` §2 "Locked decisions" | Platform deployment MUST be self-hosted Temporal and MUST use Docker Compose for MoonMind environments. |
| DOC-REQ-002 | `docs/Temporal/TemporalPlatformFoundation.md` §4 "Persistence and Visibility" | Platform MUST use PostgreSQL for Temporal persistence and PostgreSQL SQL-based advanced visibility. |
| DOC-REQ-003 | `docs/Temporal/TemporalPlatformFoundation.md` §4.1 "SQL-based visibility schema management" | Upgrade playbooks MUST include SQL visibility schema upgrades and rehearsal before rollout. |
| DOC-REQ-004 | `docs/Temporal/TemporalPlatformFoundation.md` §5 "Namespaces and retention" | Namespace `moonmind` retention management MUST be explicit, idempotent, and storage-cap driven using `TEMPORAL_RETENTION_MAX_STORAGE_GB` default `100`. |
| DOC-REQ-005 | `docs/Temporal/TemporalPlatformFoundation.md` §6 "Visibility contract for MoonMind" | Tasks list behavior MUST use Temporal Visibility as the source of truth with search attributes and supported filters. |
| DOC-REQ-006 | `docs/Temporal/TemporalPlatformFoundation.md` §7 "Task Queues" | Task queues MUST be treated as routing-only plumbing and not as user-visible queue semantics. |
| DOC-REQ-007 | `docs/Temporal/TemporalPlatformFoundation.md` §8 "Worker fleet strategy and versioning" | Worker versioning default MUST be Auto-Upgrade, with exceptions explicitly governed. |
| DOC-REQ-008 | `docs/Temporal/TemporalPlatformFoundation.md` §9 "History shards" | Default shard decision MUST be recorded before rollout; if 1 shard is chosen, migration implications MUST be acknowledged. |
| DOC-REQ-009 | `docs/Temporal/TemporalPlatformFoundation.md` §10 "Scheduling foundation" | Periodic triggers MUST be implemented through Temporal Schedules rather than external cron-style schedulers. |
| DOC-REQ-010 | `docs/Temporal/TemporalPlatformFoundation.md` §11-§12 "Security" and "Observability" | Temporal services MUST be private-network only and MUST provide baseline observability for server/worker health and key failure conditions. |
| DOC-REQ-011 | `docs/Temporal/TemporalArchitecture.md` §4, §5, §17 | Runtime model MUST be Temporal-first and Celery-free, with side effects in activities and no competing workflow engine semantics. |
| DOC-REQ-012 | `docs/Temporal/TemporalArchitecture.md` §8, §12, §16 | Runtime interfaces MUST support execution lifecycle controls (start, update/signal, cancel, list, describe) aligned to Temporal execution semantics. |
| DOC-REQ-013 | `docs/Temporal/TemporalArchitecture.md` §7, §14 | Large payloads/logs MUST be handled as artifacts or references rather than bloating workflow history. |
| DOC-REQ-014 | `docs/Temporal/TemporalArchitecture.md` §9 | Manifest ingestion orchestration MUST support explicit failure policy behavior (`fail_fast`, `continue_and_report`, `best_effort`). |
| DOC-REQ-015 | `docs/Temporal/TemporalArchitecture.md` §11 | External long-lived monitoring interactions MUST be handled through Temporal-native callback/signal or timer-based polling patterns. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Bring up Temporal platform foundation (Priority: P1)

As a platform engineer, I can provision and run the MoonMind Temporal foundation in a self-hosted Docker Compose environment with required persistence, visibility, and namespace baseline so runtime workflows can execute durably.

**Why this priority**: Without a valid Temporal foundation runtime, no Temporal-first execution path can operate in MoonMind.

**Independent Test**: Can be fully tested by bringing up the foundation environment and validating connectivity, persistence, visibility, namespace, and baseline operational policies.

**Acceptance Scenarios**:

1. **Given** a clean environment, **When** the foundation runtime is started, **Then** Temporal services become healthy and reachable only through approved private network paths.
2. **Given** the runtime is healthy, **When** platform validation checks run, **Then** PostgreSQL-backed persistence and SQL visibility are confirmed operational.
3. **Given** namespace management automation is executed, **When** namespace state is reconciled, **Then** namespace `moonmind` and storage-cap retention controls are present and idempotent.

---

### User Story 2 - Execute and observe Temporal-native lifecycle flows (Priority: P2)

As an application developer, I can exercise execution lifecycle operations aligned with Temporal semantics so MoonMind runtime behavior matches the architecture contracts.

**Why this priority**: Lifecycle operations are the minimum product behavior needed for Temporal-first runtime use.

**Independent Test**: Can be fully tested by running integration tests that cover execution start, list/query, update/signal, and cancel flows against the foundation runtime.

**Acceptance Scenarios**:

1. **Given** a started execution, **When** a supported lifecycle action is requested, **Then** the request is processed through Temporal-native controls and reflected in execution state.
2. **Given** multiple executions exist, **When** list and filter operations are requested, **Then** results and pagination tokens are sourced from Temporal Visibility behavior.
3. **Given** monitoring or integration callbacks are required, **When** callback or polling flows are exercised, **Then** workflow progress continues through Temporal-native signaling/timer behavior.

---

### User Story 3 - Operate safely and upgrade predictably (Priority: P3)

As an operator, I can validate observability, upgrade readiness, and scaling guardrails so the platform can be maintained without violating documented constraints.

**Why this priority**: Operational safety is required for reliable long-lived workflow management.

**Independent Test**: Can be fully tested by executing operational validation tests for metrics/logging coverage, versioning defaults, shard-decision gating, and upgrade rehearsal checks.

**Acceptance Scenarios**:

1. **Given** runtime telemetry is configured, **When** failure conditions are simulated, **Then** required platform signals are observable and actionable.
2. **Given** upgrade preparation is performed, **When** validation gates run, **Then** SQL visibility schema rehearsal and worker versioning defaults are verified.

### Edge Cases

- What happens when storage usage reaches `TEMPORAL_RETENTION_MAX_STORAGE_GB` while execution volume continues to increase?
- How does the platform behave when SQL visibility schema version is behind server expectations during a staged upgrade?
- How are lifecycle operations handled when there are no workers polling one or more required task queues?
- What happens when manifest ingestion receives partially valid content and failure policy differs by request?
- How does the platform recover when external callback delivery fails and timer-based polling fallback is required?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST deliver production runtime code changes that stand up a self-hosted Temporal foundation using Docker Compose, not documentation-only outputs. (Maps: DOC-REQ-001)
- **FR-002**: The runtime foundation MUST configure PostgreSQL-backed Temporal persistence and PostgreSQL SQL-based visibility and prove they are usable in validation runs. (Maps: DOC-REQ-002)
- **FR-003**: The runtime foundation MUST provide execution of visibility/listing behavior using Temporal Visibility as the primary list-of-record surface for runtime execution views. (Maps: DOC-REQ-005)
- **FR-004**: The runtime MUST define and enforce task-queue usage as routing boundaries only, without exposing queue-order guarantees as product semantics. (Maps: DOC-REQ-006)
- **FR-005**: The runtime MUST establish and validate namespace `moonmind` retention controls that are explicit, idempotent, storage-cap based, and default `TEMPORAL_RETENTION_MAX_STORAGE_GB=100`. (Maps: DOC-REQ-004)
- **FR-006**: The runtime MUST set worker versioning default behavior to Auto-Upgrade and include a documented enforcement point for exceptions. (Maps: DOC-REQ-007)
- **FR-007**: The runtime MUST capture a pre-rollout history shard decision gate and enforce acknowledgment of migration implications when using one shard. (Maps: DOC-REQ-008)
- **FR-008**: The runtime MUST support Temporal-native scheduling for recurring automation and avoid introducing external cron-style schedulers for these workflows. (Maps: DOC-REQ-009)
- **FR-009**: The runtime MUST include baseline private-network security posture and observability coverage for service health, worker polling state, retry storms, and visibility failures. (Maps: DOC-REQ-010)
- **FR-010**: The runtime MUST preserve Temporal-first, Celery-free execution semantics and keep side effects in activity execution boundaries. (Maps: DOC-REQ-011)
- **FR-011**: The runtime MUST expose execution lifecycle behavior for start, update/signal, cancel, list, and describe flows aligned with Temporal execution semantics. (Maps: DOC-REQ-012)
- **FR-012**: The runtime MUST keep large payloads/logs/artifacts out of workflow history and use reference-based handling for large execution data. (Maps: DOC-REQ-013)
- **FR-013**: The runtime MUST support manifest ingestion failure-policy handling for `fail_fast`, `continue_and_report`, and `best_effort`. (Maps: DOC-REQ-014)
- **FR-014**: The runtime MUST support external long-lived progress tracking through callback/signal or timer-based polling approaches inside Temporal orchestration. (Maps: DOC-REQ-015)
- **FR-015**: Delivery MUST include automated validation tests that verify foundational runtime behavior and contract coverage across the above requirements. (Maps: DOC-REQ-001 through DOC-REQ-015)
- **FR-016**: Upgrade readiness checks MUST validate SQL visibility schema compatibility and rehearsal before server upgrade rollout approval. (Maps: DOC-REQ-003)

### Key Entities *(include if feature involves data)*

- **Temporal Foundation Deployment Profile**: Declares runtime environment characteristics, network exposure constraints, persistence/visibility settings, and shard/versioning decisions.
- **Namespace Retention Policy**: Defines namespace identifier, storage cap threshold, and pruning governance behavior.
- **Execution Lifecycle Contract**: Represents allowed lifecycle actions, expected state transitions, and visibility outcomes.
- **Routing Queue Class**: Represents a workload routing boundary used by workers without user-facing ordering guarantees.
- **Validation Test Suite**: Represents automated checks that confirm platform contracts are met before rollout.
- **Upgrade Readiness Record**: Captures pre-rollout rehearsal status for SQL visibility schema and server compatibility.
- **Manifest Execution Policy**: Defines failure handling mode and aggregation expectations for manifest-driven orchestration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a clean environment, the Temporal foundation runtime can be started and passes all foundational health checks in under 15 minutes.
- **SC-002**: 100% of required source requirements (`DOC-REQ-001` to `DOC-REQ-015`) are covered by at least one passing automated validation test or explicitly traced validation gate.
- **SC-003**: For a validation run set of at least 50 execution lifecycle operations, at least 95% complete successfully without manual remediation.
- **SC-004**: Retention and visibility validation demonstrates correct behavior at configured storage-cap thresholds with no undocumented pruning side effects.
- **SC-005**: Upgrade readiness checks block rollout when SQL visibility schema compatibility rehearsal has not passed.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode requirement coverage is explicit and deterministic across artifacts:
  - Production runtime code task coverage in `tasks.md`: `T001-T015`, `T019-T021`, `T027-T033`, `T037-T040`.
  - Validation task coverage in `tasks.md`: `T016-T018`, `T022-T026`, `T034-T036`, `T042-T043`.
- `DOC-REQ-*` coverage guard is explicit:
  - Source requirements include `DOC-REQ-001` through `DOC-REQ-015`.
  - Deterministic implementation and validation task mappings are defined in `contracts/requirements-traceability.md` and the `DOC-REQ Coverage Matrix` in `tasks.md`.

### MEDIUM/LOW remediation status

- Cross-artifact determinism is preserved by aligning runtime-mode language and scope-gate requirements across `spec.md`, `plan.md`, and `tasks.md`.
- Runtime validation evidence requirements are explicit in `quickstart.md` and tied to requirement traceability updates.

### Residual risks

- Temporal migration scope crosses compose/runtime/API surfaces, so semantic drift remains possible if changes bypass `moonmind/workflows/temporal/` contracts.
- Upgrade/schema rehearsal and shard-decision controls depend on operators executing validation gates before rollout; skipped gates can still create deployment risk.

## Assumptions

- Runtime foundation work is scoped to MoonMind-controlled self-hosted environments where Docker Compose is the deployment orchestrator.
- Required credentials and infrastructure access for private-network service connectivity are available in the target environment.
- Existing code paths that conflict with Temporal-first execution semantics can be updated as part of this feature scope.

## Dependencies

- PostgreSQL environment suitable for Temporal persistence and SQL visibility.
- Temporal server/container images and required tooling available to deployment/runtime pipelines.
- Existing MoonMind runtime services can integrate with Temporal lifecycle and visibility behavior.
- Test infrastructure can execute automated validation against the compose-based runtime environment.
