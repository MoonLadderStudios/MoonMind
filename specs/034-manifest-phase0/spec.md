# Feature Specification: Manifest Phase 0 Rebaseline

**Feature Branch**: `031-manifest-phase0`  
**Created**: 2026-02-19  
**Last Updated**: 2026-03-02  
**Status**: Draft (rebaseline in progress)  
**Input**: User description: "Update specs/031-manifest-phase0 to make it align with the current state and strategy of the MoonMind project. Implement all of the updated tasks when done. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Rebaseline Manifest Runtime Contract (Priority: P1)

Platform engineers need the Manifest Phase 0 specification to match the runtime behavior currently expected by MoonMind so implementation and validation work can proceed without ambiguity.

**Why this priority**: If the baseline is wrong, every downstream task risks implementing stale behavior and introducing regressions.

**Independent Test**: Compare the updated requirements to queue + manifest runtime behavior, apply required production changes, and verify all impacted unit suites pass via `./tools/test_unit.sh`.

**Acceptance Scenarios**:

1. **Given** the rebaseline scope is approved, **When** runtime behavior differs from the updated Phase 0 requirements, **Then** production code is updated to match the new contract before completion.
2. **Given** manifest submissions enter the queue, **When** they are validated and normalized, **Then** required metadata, capability routing inputs, and secret-safety guarantees match the updated specification.

---

### User Story 2 - Align Registry + Queue Flows With Current Strategy (Priority: P2)

API and operations teams need manifest registry and queue flows to reflect current MoonMind strategy, including canonical workflow naming and clear boundaries between supported Phase 0 behavior and future phases.

**Why this priority**: Strategy alignment prevents drift across docs, APIs, and runtime behavior, reducing coordination and release risk.

**Independent Test**: Exercise manifest registry upsert/read/run flows and queue listings, then verify responses and behavior align with updated requirements and naming conventions.

**Acceptance Scenarios**:

1. **Given** a manifest is stored and later run, **When** APIs and queue records are inspected, **Then** returned metadata is consistent with the updated contract and excludes raw secrets.
2. **Given** Phase 0 unsupported actions or sources are submitted, **When** validation executes, **Then** requests fail fast with actionable errors.

---

### User Story 3 - Lock Behavior With Regression Tests (Priority: P3)

Maintainers need validation tests that prove the rebaseline changes are enforced in runtime code so future updates cannot silently reintroduce stale behavior.

**Why this priority**: A rebaseline without automated verification will decay quickly and fail to protect future delivery.

**Independent Test**: Run `./tools/test_unit.sh` after implementing updated tasks and confirm new or updated tests fail when key rules are removed.

**Acceptance Scenarios**:

1. **Given** rebaseline code changes are implemented, **When** unit tests run, **Then** manifest contract, queue behavior, and registry workflows are covered by regression checks.
2. **Given** a critical requirement is intentionally broken, **When** tests run, **Then** at least one targeted test fails and identifies the violated rule.

### Edge Cases

- Existing manifests created before rebaseline may lack newly required metadata; migration/backfill behavior must preserve operability while enforcing the new contract for subsequent writes.
- Legacy naming or endpoint references may still exist in historical sections; only explicitly marked historical contexts may retain them.
- Validation must reject payloads that attempt to bypass Phase 0 constraints (unsupported actions, source kinds, or secret handling rules) even if older specs implied looser behavior.

## Requirements *(mandatory)*

### Document-Backed Source Requirements

| DOC-REQ ID | Source Reference | Requirement Summary |
|------------|------------------|---------------------|
| DOC-REQ-001 | `docs/ManifestTaskSystem.md` §6.1 | Register `manifest` as a queue job type and keep worker routing capability-gated. |
| DOC-REQ-002 | `docs/ManifestTaskSystem.md` §6.2 | Use a dedicated manifest contract for normalization/validation of manifest payloads. |
| DOC-REQ-003 | `docs/ManifestTaskSystem.md` §6.5 | Derive `requiredCapabilities` server-side from manifest content instead of trusting client hints. |
| DOC-REQ-004 | `docs/ManifestTaskSystem.md` §11.1 | Keep queue/registry payloads token-safe and reject raw secret material before persistence. |
| DOC-REQ-005 | `docs/ManifestTaskSystem.md` §7.1-§7.2 | Support manifest registry CRUD plus registry-backed run submission flows. |
| DOC-REQ-006 | `docs/ManifestTaskSystem.md` §6.3 | Restrict manifest source kinds to Phase 0 supported modes with explicit validation failures for unsupported kinds. |
| DOC-REQ-007 | `docs/ManifestTaskSystem.md` §6.4 | Restrict Phase 0 manifest actions to supported values with fail-fast validation. |
| DOC-REQ-008 | `docs/ManifestTaskSystem.md` §6.6 | Preserve secret-reference metadata while keeping raw tokens out of persisted payloads and responses. |
| DOC-REQ-009 | Runtime scope guard from task objective | Delivery must include production runtime code changes and validation tests; docs/spec-only completion is not acceptable. |

Each `DOC-REQ-*` listed above maps to at least one functional requirement below.

### Functional Requirements

- **FR-001**: The `031-manifest-phase0` artifacts MUST be updated so requirements reflect current MoonMind strategy and current runtime expectations for Phase 0 manifest queue + registry behavior. *(Maps: DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009)*
- **FR-002**: Any gap between updated requirements and runtime behavior MUST be resolved with production code changes in the same implementation cycle; docs-only alignment is not sufficient. *(Maps: DOC-REQ-009)*
- **FR-003**: Manifest queue submission behavior MUST enforce deterministic normalization, including required metadata derivation and explicit validation errors for unsupported payload structures. *(Maps: DOC-REQ-002, DOC-REQ-003)*
- **FR-004**: Manifest queue and registry responses MUST remain token-safe by exposing only sanitized references/metadata and rejecting raw secret material before persistence. *(Maps: DOC-REQ-004, DOC-REQ-008)*
- **FR-005**: Manifest capability routing metadata MUST remain deterministic and sufficient for worker-eligibility checks under current Phase 0 rules. *(Maps: DOC-REQ-001, DOC-REQ-003)*
- **FR-006**: Registry CRUD + run submission flows MUST stay consistent with queue contract expectations, including job linkage and run-state metadata needed for operational auditing. *(Maps: DOC-REQ-005)*
- **FR-007**: Unsupported Phase 0 inputs (including disallowed actions or source modes) MUST fail fast with actionable 4xx validation messages. *(Maps: DOC-REQ-006, DOC-REQ-007)*
- **FR-008**: Updated artifacts MUST use current canonical MoonMind naming for workflow/runtime concepts outside explicitly marked historical migration context.
- **FR-009**: Updated implementation tasks MUST preserve MoonMind compatibility policy by avoiding hidden compatibility transforms that alter execution semantics or billing-relevant values.
- **FR-010**: Validation coverage MUST include new or changed runtime behavior with unit tests executed through `./tools/test_unit.sh` plus runtime scope-gate checks.
- **FR-011**: The rebaseline MUST produce traceable mapping between updated requirements and verification evidence (tests and observable runtime outcomes). *(Maps: DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008)*
- **FR-012**: Completion criteria for this feature MUST explicitly require production runtime code changes plus validation tests. *(Maps: DOC-REQ-009)*

### Key Entities *(include if feature involves data)*

- **Manifest Phase 0 Runtime Contract**: The set of queue/registry behaviors, validation rules, and metadata guarantees expected for Phase 0.
- **Manifest Submission Payload**: Request body used to submit inline or registry-backed manifest runs, normalized before queue persistence.
- **Manifest Registry Record**: Stored manifest entry with versioning/hash/run linkage metadata used by operators and queue submission flows.
- **Rebaseline Verification Evidence**: Unit-test and runtime-validation outcomes that demonstrate updated requirements are implemented.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of rebaseline requirements that affect runtime behavior are implemented as production code changes in this feature scope (no docs-only closure).
- **SC-002**: `./tools/test_unit.sh` passes with updated manifest-focused tests, and at least one test exists for each rebaseline requirement area (contract validation, queue flow, registry flow, secret handling, capability routing).
- **SC-003**: Manual/API validation confirms manifest queue + registry flows produce metadata and errors consistent with updated requirements for all primary acceptance scenarios.
- **SC-004**: No non-historical docs/spec references in `specs/031-manifest-phase0` conflict with canonical MoonMind naming after rebaseline updates.
- **SC-005**: Runtime scope validation passes in runtime mode for both tasks and implementation diff checks using `validate-implementation-scope.sh`.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode requirement coverage is explicit and deterministic across artifacts:
  - Production runtime code task coverage in `tasks.md`: `T004-T007`, `T010-T012`, `T015-T017`, `T020-T021`.
  - Validation task coverage in `tasks.md`: `T008-T009`, `T013-T014`, `T018-T019`, `T023-T025`.
- `DOC-REQ-*` coverage guard is explicit:
  - Source requirements include `DOC-REQ-001` through `DOC-REQ-009`.
  - Deterministic implementation and validation task mappings are defined in `contracts/requirements-traceability.md` and the `DOC-REQ Coverage Matrix` in `tasks.md`.

### MEDIUM/LOW remediation status

- Cross-artifact determinism is preserved by aligning runtime-mode language and scope-gate requirements across `spec.md`, `plan.md`, and `tasks.md`.
- Runtime validation evidence requirements are now explicit in `quickstart.md` and tied to requirement traceability updates.

### Residual risks

- Manifest contract behavior can still drift if future changes bypass the shared normalization path; regression tests and scope gates must remain required.
- Legacy manifests with incomplete metadata may still require operational cleanup outside this feature's runtime contract.

## Assumptions

- Manifest Phase 0 remains focused on control-plane queue/registry behavior; full ingestion execution remains out of scope for this rebaseline unless explicitly added by updated tasks.
- Existing queue/auth/artifact infrastructure remains available; the rebaseline concentrates on correctness, consistency, and validation coverage.
- Historical references may remain only where explicitly labeled as migration context.

## Dependencies & Risks

- **As-Built Drift Risk**: Existing runtime behavior may have diverged from old Phase 0 artifacts; rebaseline work must resolve conflicts explicitly rather than preserving stale text.
- **Cross-Artifact Consistency Risk**: Spec/plan/tasks/contracts may drift if updates are not synchronized; traceability updates are required to control this.
- **Regression Risk**: Tightening validation rules can break existing flows unless tests cover both success and failure paths.
