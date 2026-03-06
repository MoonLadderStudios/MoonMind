# Feature Specification: Activity Catalog and Worker Topology

**Feature Branch**: `047-activity-worker-topology`  
**Created**: 2026-03-05  
**Status**: Draft  
**Input**: User description: "Fully implement the Activity Catalog and Worker Topology system described in docs/Temporal/ActivityCatalogAndWorkerTopology.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §1, §4 (lines 11-25, 96-115) | Workflows must orchestrate, all side effects must run in Activities, and Task Queues must be treated only as internal routing labels without product-level FIFO guarantees. |
| DOC-REQ-002 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §3, §5.1 (lines 56-75, 121-146) | Activity Type names must remain stable long-lived contracts, use the documented namespaces, and evolve by adding new types rather than changing existing semantics in place. |
| DOC-REQ-003 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §3, §5.2 (lines 65-75, 148-180) | Activity payloads must stay small, use artifact references and idempotency keys, and derive execution metadata from runtime context instead of duplicating it into business payloads. |
| DOC-REQ-004 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §6.1 (lines 186-232) | The artifact activity family must implement the defined lifecycle operations, retention/link-type behavior, redaction policy, and retry-safe artifact handling. |
| DOC-REQ-005 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §6.2 (lines 236-253) | Plan generation and validation must run as Activities, store plans as artifacts, and use validation as the authoritative pre-execution gate. |
| DOC-REQ-006 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §6.3 (lines 257-297) | Skill execution must use the hybrid binding model with `mm.skill.execute` as the default path and registry-driven explicit activity bindings only when an operational reason is declared. |
| DOC-REQ-007 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §6.4 (lines 301-326) | Sandbox activities must support repo/command/test operations under strong isolation, resource limits, cancellation awareness, and heartbeat-driven progress reporting. |
| DOC-REQ-008 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §6.5 (lines 329-349) | Integration activities must sit behind provider adapters, isolate provider credentials, and prefer callback-first monitoring with polling fallback only where needed. |
| DOC-REQ-009 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §7 (lines 353-383) | Routing must be capability-based per activity invocation, not per workflow type, and priority lanes remain deferred for v1. |
| DOC-REQ-010 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §4.1, §14 (lines 100-115, 599-607) | The minimal queue topology must include `mm.workflow`, `mm.activity.artifacts`, `mm.activity.llm`, `mm.activity.sandbox`, and `mm.activity.integrations`, with a single shared LLM queue in v1. |
| DOC-REQ-011 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §8 (lines 387-437) | Worker fleets must be segmented into workflow, artifacts, LLM, sandbox, and integrations roles with distinct privilege, scaling, and resource boundaries, provisionable in Docker Compose. |
| DOC-REQ-012 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §9 (lines 441-481) | Activities must follow standardized timeout, retry, heartbeat, and idempotency rules by family, including bounded retries for destructive or costly operations. |
| DOC-REQ-013 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §10 (lines 485-506) | The security model must enforce least privilege, network controls, secret hygiene, artifact redaction/preview behavior, and non-public local object storage even when app auth is disabled. |
| DOC-REQ-014 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §11 (lines 510-541) | Activities must emit structured logging, fleet metrics, trace propagation, and artifact-backed log references so operators can reconstruct outcomes safely. |
| DOC-REQ-015 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §12 (lines 545-566) | Validation must cover activity contracts, fleet routing, load behavior, failure injection, and traceability to contracts/tests when the catalog changes. |
| DOC-REQ-016 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §13, Appendix A (lines 570-595, 611-645) | The v1 implementation sequence and MVP catalog must include the baseline worker fleets and the initial artifact, planning, skill, sandbox, integration, and lifecycle activity types listed in the appendix. |
| DOC-REQ-017 | `docs/Temporal/ActivityCatalogAndWorkerTopology.md` §3, §14 (lines 71-75, 603-607) | Activities must not update Search Attributes or Memo fields directly; workflows own visibility updates, and the documented queue/priority decisions are fixed for v1 unless an explicit future change is made. |
| DOC-REQ-018 | Task objective runtime scope guard | Delivery must include production runtime code changes that implement the Activity Catalog and Worker Topology contracts, plus automated validation tests; docs-only completion is not acceptable. |

Each `DOC-REQ-*` listed above maps to at least one functional requirement below.

## Clarifications

### Session 2026-03-06

- Q: For this feature, does the implementation scope stop at Appendix A's suggested MVP list, or does it cover the full canonical activity system described across Sections 6-8 of the source document? → A: The feature scope follows the full canonical system described in the source document because the task objective requires fully implementing the Activity Catalog and Worker Topology; Appendix A is the minimum seed catalog, not the delivery ceiling.
- Q: What qualifies as a valid curated explicit skill-to-activity binding instead of the default `mm.skill.execute` path? → A: The registry may use an explicit activity binding only when it declares a concrete operational reason: stronger isolation, specialized credentials, or clearer routing; otherwise the default path remains `mm.skill.execute`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Route Canonical Activities to the Correct Worker Fleet (Priority: P1)

As a platform operator, I can run the Temporal worker topology with distinct workflow and activity fleets so each activity type executes only on the queue and fleet that matches its capability and privilege requirements.

**Why this priority**: Correct routing and fleet separation are the foundation for every runtime action in the new Temporal model.

**Independent Test**: Start the worker topology, invoke one activity from each canonical family, and verify each invocation is handled by the expected queue/fleet with no cross-fleet leakage.

**Acceptance Scenarios**:

1. **Given** the Temporal runtime is started with the v1 worker topology, **When** a workflow schedules artifact, plan, sandbox, and integration work, **Then** each activity is dispatched to its documented task queue and worker fleet.
2. **Given** LLM-backed planning or skill work is requested, **When** provider choice varies, **Then** the invocation still routes through the single shared `mm.activity.llm` queue in v1.
3. **Given** an unsupported or mismatched capability binding, **When** activity routing is resolved, **Then** the system rejects the invocation instead of silently routing it to an incorrect fleet.

---

### User Story 2 - Execute Stable Activity Contracts with Artifact-Backed Payloads (Priority: P1)

As a runtime developer, I can rely on a stable catalog of activity types and request/response envelopes so workflows, skills, and integrations exchange references and summaries instead of large inline payloads.

**Why this priority**: Stable contracts and payload discipline prevent workflow-history growth, reduce coupling, and let runtime features evolve safely.

**Independent Test**: Exercise artifact, plan, skill, sandbox, and integration activities using the documented request/response contracts and confirm large inputs/outputs are referenced through artifacts rather than embedded in workflow history.

**Acceptance Scenarios**:

1. **Given** a side-effecting activity request, **When** the invocation is created, **Then** it includes a correlation identifier, idempotency key, and artifact references for large inputs.
2. **Given** an activity produces logs or large outputs, **When** it completes, **Then** the result returns artifact references and compact summary data instead of raw blobs.
3. **Given** a workflow updates its visibility fields, **When** activity results are returned, **Then** the workflow applies the visibility change and the activity itself does not write Search Attributes or Memo fields directly.

---

### User Story 3 - Operate the Fleet Securely and Recover from Failures (Priority: P2)

As an operator, I can monitor, retry, and recover activity execution safely because each fleet follows explicit security boundaries, heartbeat/retry rules, and observability requirements.

**Why this priority**: Reliability and security controls determine whether the topology is safe to run under real workload and external-provider failures.

**Independent Test**: Inject retryable failures, long-running sandbox work, and restricted artifact access flows, then verify heartbeat data, idempotent recovery, logging, and least-privilege boundaries.

**Acceptance Scenarios**:

1. **Given** a sandbox command runs for an extended period, **When** progress is reported, **Then** heartbeat updates and artifact-backed logs allow the operator to observe the run without losing cancellation support.
2. **Given** an external provider call times out or is retried, **When** retry handling occurs, **Then** the system uses bounded backoff and preserves idempotent external identity for repeated starts.
3. **Given** restricted data or secrets are involved, **When** logs, artifacts, or local one-click deployments are used, **Then** raw secrets remain absent from workflow history, logs, and public storage access paths.

### Edge Cases

- An activity family is registered without a capability binding or task queue assignment.
- A long-running sandbox command is retried after partial side effects and must avoid creating duplicate workspaces or repeated destructive actions.
- A provider-specific integration requires stronger isolation before provider-specific queues have been introduced.
- Large request or result payloads exceed normal workflow payload expectations and must be redirected to artifacts.
- Workflow visibility needs updating after an activity result, but the activity itself must not write Search Attributes or Memo fields.
- Local one-click mode uses disabled end-user auth while object storage still must remain private on the internal network.
- A skill is mapped to a curated explicit activity type without a registry-declared operational reason and must be rejected instead of bypassing the default dispatcher.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The delivery MUST include production runtime code changes that implement the Activity Catalog and Worker Topology contracts, plus automated validation tests; docs-only completion is not acceptable. (DOC-REQ-018)
- **FR-002**: The system MUST keep workflows deterministic by executing all side effects and nondeterministic work in Activities and using Task Queues only as internal routing labels, not as product-level ordered queues. (DOC-REQ-001)
- **FR-003**: The system MUST implement the documented stable Activity Type namespaces and preserve backward-compatible semantics for existing activity names, adding new types instead of changing meaning in place. (DOC-REQ-002)
- **FR-004**: The system MUST implement the shared activity envelope rules so side-effecting activities accept `correlation_id`, `idempotency_key`, artifact references, and small parameters, and return compact summaries plus artifact references for large outputs. (DOC-REQ-003)
- **FR-005**: The system MUST derive execution metadata such as workflow ID, run ID, activity ID, and attempt from runtime context rather than requiring duplicated business payload fields by default. (DOC-REQ-003)
- **FR-006**: The system MUST implement the artifact activity family covering `artifact.create`, `artifact.write_complete`, `artifact.read`, `artifact.list_for_execution`, `artifact.compute_preview`, `artifact.link`, `artifact.pin`, `artifact.unpin`, and `artifact.lifecycle_sweep`, including the documented retention, link-type, redaction, and retry-safety rules. (DOC-REQ-004, DOC-REQ-016)
- **FR-007**: The system MUST implement plan generation and validation as activity-based runtime contracts that store plans as artifacts and treat validation as the authoritative pre-execution gate. (DOC-REQ-005, DOC-REQ-016)
- **FR-008**: The system MUST implement the hybrid skill execution model with `mm.skill.execute` as the default registry-dispatched path and allow curated explicit activity bindings only when the registry declares a concrete operational reason (`stronger isolation`, `specialized credentials`, or `clearer routing`). (DOC-REQ-006, DOC-REQ-016)
- **FR-009**: The system MUST implement the canonical sandbox activity contracts `sandbox.checkout_repo`, `sandbox.run_command`, `sandbox.apply_patch`, and `sandbox.run_tests` under strong isolation, explicit resource limits, cancellation awareness, and heartbeats for long-running work. (DOC-REQ-007, DOC-REQ-012, DOC-REQ-016)
- **FR-010**: The system MUST implement integration activity contracts behind provider adapters, isolate provider credentials from sandbox workers, and support callback-first completion with bounded polling fallback. (DOC-REQ-008, DOC-REQ-012, DOC-REQ-016)
- **FR-011**: The system MUST route activities by capability class per invocation, using registry or equivalent configuration to map activity type, capability, default queue, and timeout/retry policy without basing routing on workflow type. (DOC-REQ-009)
- **FR-012**: The system MUST implement the v1 minimal queue set `mm.workflow`, `mm.activity.artifacts`, `mm.activity.llm`, `mm.activity.sandbox`, and `mm.activity.integrations`, and MUST keep provider-specific LLM queues deferred in v1. (DOC-REQ-010)
- **FR-013**: The system MUST keep priority lanes deferred for v1 and rely on concurrency controls or rate limiting rather than promising strict ordering semantics. (DOC-REQ-009, DOC-REQ-017)
- **FR-014**: The system MUST provision and support distinct workflow, artifacts, LLM, sandbox, and integrations worker fleets with the documented least-privilege, secret, scaling, and resource-boundary expectations. (DOC-REQ-011)
- **FR-015**: The system MUST prevent activities from updating Search Attributes or Memo fields directly and require workflows to own all visibility updates derived from activity outcomes. (DOC-REQ-017)
- **FR-016**: The system MUST enforce family-appropriate timeout, retry, heartbeat, and idempotency behavior, including mandatory idempotency keys for side-effecting activities and bounded retry behavior for destructive or costly work. (DOC-REQ-012)
- **FR-017**: The system MUST enforce the security model for least privilege, network controls, secret distribution, artifact redaction/preview access, and private local object storage posture even when application auth is disabled. (DOC-REQ-013)
- **FR-018**: The system MUST emit structured activity logs, fleet metrics, and trace correlation data, and MUST write large log streams to artifacts referenced from results or summaries. (DOC-REQ-014)
- **FR-019**: The system MUST provide validation coverage for activity contract correctness, routing to the proper fleet, load-sensitive behavior, failure injection scenarios, and traceability between catalog changes, contracts, and runtime tests. (DOC-REQ-015)
- **FR-020**: The implementation MUST include every activity type listed in Appendix A as the minimum v1 seed catalog, and this feature's full-delivery scope MUST also satisfy the broader canonical family contracts defined above where they extend beyond that appendix list. (DOC-REQ-016)

### Key Entities *(include if feature involves data)*

- **ActivityTypeContract**: Defines a stable activity name, its family, expected business inputs/outputs, and its compatibility obligations.
- **ActivityInvocationEnvelope**: Captures correlation, idempotency, artifact references, small parameters, and compact result summaries for one activity execution.
- **CapabilityBinding**: Maps an activity or skill to a capability class, task queue, and default policy profile.
- **WorkerFleetProfile**: Describes one fleet's queues, privileges, scaling behavior, and resource/security boundaries.
- **ArtifactLifecycleRecord**: Represents an artifact's upload/completion, execution linkage, retention class, redaction level, and preview/pin state.
- **PlanArtifact**: Represents a generated or validated plan stored as an artifact reference and used as execution input.
- **SkillExecutionBinding**: Declares whether a skill uses the default dispatcher or a curated explicit activity type, along with the reason for that binding.
- **ObservabilitySummary**: Encapsulates structured log identifiers, metrics dimensions, trace correlation, and artifact references needed to explain activity outcomes.

### Assumptions & Dependencies

- Existing Temporal workflow lifecycle behavior can consume the new activity catalog without reintroducing non-Temporal queue abstractions.
- Artifact reference contracts and skill registry concepts already exist or are being introduced in adjacent Temporal work and can be extended to cover this topology.
- Docker Compose remains the baseline local and development deployment model for worker fleets.
- Appendix A is treated as the minimum seed catalog for v1, while this feature's acceptance scope still includes the broader canonical family contracts described in the source document.
- Provider-specific LLM or integration queues can be added later without breaking the initial v1 contract names or routing rules.
- Validation may combine unit, contract, and integration-style runtime tests as long as they prove the required routing, security, and reliability behavior.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated routing tests show 100% of canonical v1 activity invocations dispatch to the correct documented queue and worker fleet.
- **SC-002**: Automated contract tests show 100% of side-effecting activities accept idempotency-aware request envelopes and return artifact references for large outputs instead of raw payload blobs.
- **SC-003**: Automated reliability tests show heartbeat, retry, and idempotent recovery behavior works for all long-running sandbox flows and representative integration failure paths.
- **SC-004**: Automated security and topology validation shows each fleet only receives the privileges and secret scope documented for its role, with no sandbox access to provider-only credentials.
- **SC-005**: Automated observability tests confirm activity logs and summaries always include workflow/run/activity/correlation identifiers and artifact-backed log references where output is large.
- **SC-006**: Release acceptance for this feature includes production runtime implementation changes plus validation test coverage for catalog contracts, worker routing, and failure behavior, with no docs-only path accepted as complete.
