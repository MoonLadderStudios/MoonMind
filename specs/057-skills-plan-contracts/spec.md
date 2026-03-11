# Feature Specification: Skills and Plans Runtime Contracts

**Feature Branch**: `045-skills-plan-contracts`  
**Created**: 2026-03-05  
**Status**: Draft  
**Input**: User description: "Implement docs/Skills/SkillAndPlanContracts.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Skills/SkillAndPlanContracts.md` §2 Design principles, items 1 and 6 (lines 35-37, 50-51) | Workflow execution must remain deterministic orchestration, while side effects run in activities; execution progress must be structured and retrievable. |
| DOC-REQ-002 | `docs/Skills/SkillAndPlanContracts.md` §2 item 2 and §7.1 (lines 38-39, 351-354) | Every executable operation, including planning, must run as a skill invocation. |
| DOC-REQ-003 | `docs/Skills/SkillAndPlanContracts.md` §2 items 3-4 and §6.1 (lines 41-45, 276) | Plans must be validated deterministic DAG data artifacts, not executable code. |
| DOC-REQ-004 | `docs/Skills/SkillAndPlanContracts.md` §3.2 Rules (lines 77-80) | Artifact references are opaque, immutable, and required for payloads larger than small JSON. |
| DOC-REQ-005 | `docs/Skills/SkillAndPlanContracts.md` §4.2 Required fields (lines 146-152) | Skill definitions must include required schema, executor binding, policy, and capability fields. |
| DOC-REQ-006 | `docs/Skills/SkillAndPlanContracts.md` §4.3 Rules (lines 178-181) | Skill invocations must have unique IDs, pinned-snapshot skill resolution, input validation, and bounded overrides. |
| DOC-REQ-007 | `docs/Skills/SkillAndPlanContracts.md` §4.4 Rules (lines 208-209) | Skill results must keep inline outputs small and store large outputs as artifacts. |
| DOC-REQ-008 | `docs/Skills/SkillAndPlanContracts.md` §4.5 Standard error codes and retry semantics (lines 232-247) | Skill failures must normalize to the standard error model and enforce policy-driven retry behavior. |
| DOC-REQ-009 | `docs/Skills/SkillAndPlanContracts.md` §5.2 Discovery model (lines 260-264) | v1 runtime must use a static, immutable registry snapshot that is deployment-coupled. |
| DOC-REQ-010 | `docs/Skills/SkillAndPlanContracts.md` §6.3-§6.6 and §12 Q2 v1 rule (lines 315-318, 337-343, 530) | Dependency, concurrency, and failure semantics must enforce dependency-success execution with no conditional edges in v1. |
| DOC-REQ-011 | `docs/Skills/SkillAndPlanContracts.md` §6.4 Rules (lines 332-333) | Cross-node data references must resolve to valid node outputs deterministically. |
| DOC-REQ-012 | `docs/Skills/SkillAndPlanContracts.md` §9.1 and §12 Q3 v1 rule (lines 385-389, 556) | Runtime must split structural/deep validation and must not begin execution before validation succeeds. |
| DOC-REQ-013 | `docs/Skills/SkillAndPlanContracts.md` §12 Q1 Validation rule (line 509) | Interpreter must resolve skill definitions from the plan's pinned registry snapshot, never from latest state. |
| DOC-REQ-014 | `docs/Skills/SkillAndPlanContracts.md` §12 Q4 Rule (line 581) | Skill registry must declare activity type binding; interpreter must not infer or guess binding. |
| DOC-REQ-015 | `docs/Skills/SkillAndPlanContracts.md` §10 and §13 Deliverables (lines 431-449, 589-609) | Runtime must provide structured progress/intermediate outputs and deliver registry, plan, and execution contract capabilities. |
| DOC-REQ-016 | `docs/Skills/SkillAndPlanContracts.md` §14 Implementation checklist (lines 615-624) | Minimum implementation must cover registry loader/validator, snapshot storage, deep plan validation, interpreter, dispatcher, and progress outputs. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Execute Contract-Compliant Plans (Priority: P1)

A platform operator runs a workflow using a plan artifact and expects deterministic plan execution that follows dependencies, failure policy, and concurrency limits.

**Why this priority**: Deterministic and policy-correct execution is the core value of the skills-and-plans runtime.

**Independent Test**: Submit a valid DAG plan with a pinned registry snapshot and confirm execution order, concurrency caps, result handling, and final summary behavior without relying on any other feature slice.

**Acceptance Scenarios**:

1. **Given** a valid plan artifact with dependency edges and a pinned registry snapshot, **When** execution starts, **Then** only dependency-ready nodes are scheduled and policy concurrency limits are enforced.
2. **Given** a plan configured as `FAIL_FAST`, **When** one node fails, **Then** outstanding independent nodes are cancelled and the run is completed with a failure summary.
3. **Given** a plan configured as `CONTINUE`, **When** one branch fails, **Then** independent branches continue and the final summary includes both successes and failures.

---

### User Story 2 - Validate and Dispatch Skills Safely (Priority: P2)

A runtime maintainer defines skills and invocations in the registry and expects invalid contracts to fail early while valid contracts dispatch to compatible workers.

**Why this priority**: Early contract validation and correct dispatch prevent unsafe or inconsistent execution behavior.

**Independent Test**: Load a mixed registry/invocation dataset and verify invalid entries fail validation, while valid entries route with declared activity bindings and capability constraints.

**Acceptance Scenarios**:

1. **Given** a skill definition missing required contract fields, **When** registry validation runs, **Then** that definition is rejected before execution.
2. **Given** an invocation whose inputs violate the skill input schema, **When** plan validation runs, **Then** execution does not start and a normalized validation failure is returned.
3. **Given** a valid invocation with a declared activity type and capability requirements, **When** the node is dispatched, **Then** routing uses the declared binding and does not rely on inferred defaults.

---

### User Story 3 - Observe Progress and Artifacts (Priority: P3)

An operations user monitors active and completed runs and needs structured progress plus artifact references for auditing and debugging.

**Why this priority**: Structured observability is required to operate plan execution safely at runtime.

**Independent Test**: Start a multi-node run and verify progress query responses, optional progress artifacts, node result artifacts, and final summary artifact availability.

**Acceptance Scenarios**:

1. **Given** an in-progress run, **When** progress is queried, **Then** counts for pending/running/succeeded/failed plus last event and update timestamp are returned.
2. **Given** a node that produces large output data, **When** node execution completes, **Then** large output is exposed through artifact references rather than inline payload expansion.
3. **Given** a completed run, **When** summary data is retrieved, **Then** the run returns final outcome and references to per-node outputs and summary artifacts.

### Edge Cases

- Plan includes a dependency edge that references a node ID not present in the node list.
- Plan graph contains a cycle that prevents deterministic scheduling.
- A node input reference points to a missing output path from an upstream node.
- Registry snapshot digest is missing or does not match supplied snapshot artifact metadata.
- A node fails repeatedly with a non-retryable error code while retry policy still allows attempts.
- A run includes both small inline outputs and large artifact outputs in the same execution path.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The runtime MUST keep workflow logic deterministic orchestration and confine side effects to activities. (Maps: DOC-REQ-001)
- **FR-002**: The runtime MUST model planning and all executable operations as explicit skill invocations. (Maps: DOC-REQ-002)
- **FR-003**: The runtime MUST treat plans as validated DAG data artifacts and reject non-DAG or unsupported plan structures. (Maps: DOC-REQ-003, DOC-REQ-010)
- **FR-004**: The runtime MUST use immutable artifact references for large inputs/outputs and keep workflow payload data small. (Maps: DOC-REQ-004, DOC-REQ-007)
- **FR-005**: The runtime MUST validate skill definition contracts for required fields, policy bounds, executor binding, and capability declarations before execution. (Maps: DOC-REQ-005, DOC-REQ-009)
- **FR-006**: The runtime MUST validate each plan node for unique ID, pinned skill availability, schema-compliant inputs, and bounded overrides. (Maps: DOC-REQ-006, DOC-REQ-012)
- **FR-007**: The runtime MUST normalize failures to the standard skill error code set and apply policy-driven retries. (Maps: DOC-REQ-008)
- **FR-008**: The runtime MUST resolve inter-node references deterministically and reject plans with invalid reference targets. (Maps: DOC-REQ-011, DOC-REQ-012)
- **FR-009**: The plan interpreter MUST schedule nodes only after all dependencies succeed, enforce max concurrency, and apply configured failure mode (`FAIL_FAST` or `CONTINUE`). (Maps: DOC-REQ-010)
- **FR-010**: The interpreter MUST resolve skill contracts from the plan's pinned registry snapshot and MUST NOT resolve from latest registry state. (Maps: DOC-REQ-013)
- **FR-011**: Node dispatch MUST use activity type and capability requirements declared by the skill registry, and interpreter logic MUST NOT guess bindings. (Maps: DOC-REQ-014)
- **FR-012**: The runtime MUST expose structured progress state during execution and support durable progress artifact publication. (Maps: DOC-REQ-001, DOC-REQ-015)
- **FR-013**: The runtime MUST produce per-node results and a final summary artifact for completed plan executions. (Maps: DOC-REQ-015)
- **FR-014**: Delivery MUST include production runtime code changes implementing the contracts in `docs/Skills/SkillAndPlanContracts.md`; docs/spec-only edits are not sufficient for completion. (Maps: runtime scope guard)
- **FR-015**: Delivery MUST include validation tests covering registry validation, plan validation, reference resolution, interpreter scheduling/failure behavior, dispatcher routing, and progress reporting. (Maps: DOC-REQ-016, runtime scope guard)
- **FR-016**: Delivery MUST implement the minimum runtime capability set from the source checklist: registry loader/validator, snapshot digest + artifact storage, deep plan validation activity, plan interpreter, skill dispatcher, and progress query/artifact output. (Maps: DOC-REQ-016)

### DOC-REQ to Functional Requirement Mapping

| DOC-REQ ID | Mapped FR(s) |
| --- | --- |
| DOC-REQ-001 | FR-001, FR-012 |
| DOC-REQ-002 | FR-002 |
| DOC-REQ-003 | FR-003 |
| DOC-REQ-004 | FR-004 |
| DOC-REQ-005 | FR-005 |
| DOC-REQ-006 | FR-006 |
| DOC-REQ-007 | FR-004 |
| DOC-REQ-008 | FR-007 |
| DOC-REQ-009 | FR-005 |
| DOC-REQ-010 | FR-003, FR-009 |
| DOC-REQ-011 | FR-008 |
| DOC-REQ-012 | FR-006, FR-008 |
| DOC-REQ-013 | FR-010 |
| DOC-REQ-014 | FR-011 |
| DOC-REQ-015 | FR-012, FR-013 |
| DOC-REQ-016 | FR-015, FR-016 |

### Key Entities *(include if feature involves data)*

- **ArtifactRef**: Immutable reference envelope for large input/output payloads, including content metadata and creation timestamp.
- **RegistrySnapshot**: Immutable digest + artifact reference bundle that pins available skill definitions for one execution.
- **ToolDefinition**: Versioned skill contract with input/output schemas, executor binding, policy defaults, and capability/security constraints.
- **Step Node**: Plan node containing unique ID, pinned skill name/version, validated inputs, and bounded execution overrides.
- **Plan**: DAG artifact containing metadata, policy, node list, and dependency edges.
- **ToolResult / ToolFailure**: Standardized per-node execution outcome model with small inline outputs and artifact-based large outputs.
- **ExecutionProgress**: Structured progress state for query/read models (node counts, last event, timestamp).

### Assumptions & Dependencies

- Temporal workflow and activity infrastructure is available to execute deterministic orchestration with external side effects delegated to activities.
- Artifact storage/read APIs can persist immutable artifacts and return stable references.
- Worker capability metadata and queue routing metadata are available for activity dispatch decisions.
- Runtime intent is authoritative for this feature; docs/spec-only closure does not satisfy completion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of source requirements listed in `DOC-REQ-*` are mapped to at least one functional requirement with no unmapped entries.
- **SC-002**: Validation rejects 100% of tested invalid plans (unsupported version, cyclic edges, missing skill, invalid input schema, invalid data references) before runtime execution starts.
- **SC-003**: Execution tests demonstrate dependency ordering and enforce configured max concurrency with no out-of-order dependent-node start events.
- **SC-004**: Failure mode tests confirm `FAIL_FAST` cancellation behavior and `CONTINUE` branch continuation behavior in all defined acceptance scenarios.
- **SC-005**: For outputs above the runtime small-payload threshold, tests confirm data is returned via artifact references rather than expanded inline payloads.
- **SC-006**: Feature completion includes production runtime code changes plus validation tests, and unit tests pass via `./tools/test_unit.sh`.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode requirement coverage is explicit and deterministic across artifacts:
  - Production runtime code task coverage in `tasks.md`: `T001`, `T004-T013`, `T017-T020`, `T025-T030`, `T034-T037`.
  - Validation task coverage in `tasks.md`: `T014-T016`, `T021-T024`, `T031-T033`, `T039-T041`.
- `DOC-REQ-*` coverage guard is explicit:
  - Source requirements include `DOC-REQ-001` through `DOC-REQ-016`.
  - Deterministic implementation and validation task mappings are defined in `contracts/requirements-traceability.md` and the `DOC-REQ Coverage Matrix` in `tasks.md`.

### MEDIUM/LOW remediation status

- Cross-artifact determinism is preserved by aligning runtime-mode language and scope-gate requirements across `spec.md`, `plan.md`, and `tasks.md`.
- Requirements traceability explicitly lists implementation and validation task coverage for every `DOC-REQ-*` row.

### Residual risks

- Contract behavior spans multiple runtime modules (`tool_registry`, `plan_validation`, `tool_dispatcher`, and `plan_interpreter`), so implementation drift remains possible if changes bypass shared contracts.
- Failure-policy and dependency-ordering regressions remain possible if edge-case tests are skipped during implementation.
