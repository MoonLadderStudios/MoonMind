# Feature Specification: Manifest Queue Alignment and Hardening

**Feature Branch**: `028-manifest-queue`  
**Created**: 2026-02-19  
**Updated**: 2026-03-02  
**Status**: In Progress  
**Input**: User description: "Update specs/028-manifest-queue to align with the current MoonMind state/strategy and implement the updated tasks." Runtime scope guard: "Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fail-Fast Manifest Run Actions (Priority: P1)

As an API client submitting manifest runs, I need `/api/manifests/{name}/runs` to reject unsupported `action` values before queue submission so bad requests fail early with deterministic errors.

**Why this priority**: Fail-fast validation for unsupported runtime input is a current MoonMind compatibility strategy and reduces invalid queue writes.

**Independent Test**: Build `ManifestRunRequest` payloads with valid and invalid action values and verify only `plan`/`run` are accepted while unsupported values raise validation errors before service submission.

**Acceptance Scenarios**:

1. **Given** an action value of `"run"` or `"plan"`, **When** the API parses the request, **Then** the action is accepted and forwarded to service submission.
2. **Given** an action value with extra whitespace or mixed case (for example `" PLAN "`), **When** the API parses the request, **Then** the normalized value is accepted as `"plan"`.
3. **Given** an unsupported action like `"evaluate"`, **When** the API parses the request, **Then** it returns a validation failure and does not call queue submission logic.

---

### User Story 2 - Spec Artifacts Match Runtime Reality (Priority: P1)

As a platform engineer, I need `specs/028-manifest-queue` artifacts to reflect the real implementation state so planning and onboarding are based on accurate contracts, paths, and behavior.

**Why this priority**: The current runtime already contains Phase 0 manifest queue plumbing plus hardening; stale artifacts create implementation drift and incorrect tasking.

**Independent Test**: Cross-check `spec.md`, `plan.md`, `tasks.md`, `data-model.md`, `contracts/manifests-api.md`, and `quickstart.md` against current code paths and API behavior.

**Acceptance Scenarios**:

1. **Given** the current queue runtime, **When** maintainers review artifact file references, **Then** they point to existing paths (`moonmind/workflows/agent_queue/*`, `api_service/api/routers/manifests.py`, `api_service/services/manifests_service.py`, existing test locations).
2. **Given** the current API behavior, **When** maintainers read the manifests contract doc, **Then** request/response and error semantics align with implementation (including validation failures and response fields).

---

### User Story 3 - Regression Coverage for Alignment Rules (Priority: P2)

As a maintainer, I need focused unit coverage for the new action validation rule so future changes do not reintroduce permissive behavior.

**Why this priority**: Runtime hardening must be protected by automated tests that execute in the project-standard test wrapper.

**Independent Test**: Run `./tools/test_unit.sh` and confirm tests assert action normalization/defaulting and unsupported action rejection.

**Acceptance Scenarios**:

1. **Given** valid and default action payloads, **When** schema tests run, **Then** normalization/default behavior remains deterministic.
2. **Given** unsupported action payloads, **When** schema tests run, **Then** validation fails with a message listing supported values.

### Edge Cases

- `action` omitted from `/api/manifests/{name}/runs` requests should continue defaulting to `run`.
- Whitespace-only or non-string action inputs should fail validation rather than silently coercing to an unsupported value.
- Existing manifest normalization (hashing, capabilities, secret-reference extraction, payload sanitization) must remain unchanged by this hardening.

### Dependencies & Assumptions

- Existing manifest queue and registry plumbing remains in place and is not being redesigned in this update.
- Runtime validation work is limited to request-shape hardening; queue payload normalization remains owned by `manifest_contract` and `AgentQueueService`.
- Unit tests are executed through `./tools/test_unit.sh` per repository policy.
- This feature remains runtime-intent; documentation updates alone are insufficient without corresponding production code and validation test changes.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Refresh `specs/028-manifest-queue` artifacts so they match current implementation structure, behavior, and strategy. *(Maps: DOC-REQ-002)*
- **FR-002**: Manifest run request parsing MUST accept only `action` values `plan` or `run`, with normalized lowercase output. *(Maps: DOC-REQ-001)*
- **FR-003**: Unsupported manifest run `action` values MUST fail before queue submission logic executes. *(Maps: DOC-REQ-001)*
- **FR-004**: Existing manifest queue normalization behavior (`manifestHash`, `manifestVersion`, derived capabilities, secret reference handling, payload sanitization) MUST remain backward compatible. *(Maps: DOC-REQ-005)*
- **FR-005**: Add or update automated tests validating action defaulting, normalization, and rejection paths, and execute them via `./tools/test_unit.sh`. *(Maps: DOC-REQ-003, DOC-REQ-004)*
- **FR-006**: Required deliverables MUST include production runtime code changes (not docs/spec-only) plus validation tests. *(Maps: DOC-REQ-004)*

## Source Document Requirements

| ID | Source | Requirement | Functional Requirement Mapping |
|----|--------|-------------|--------------------------------|
| DOC-REQ-001 | `docs/ManifestTaskSystem.md` §6.4 | Manifest actions for queue-backed runs must remain fail-fast and limited to supported values (`plan`, `run`) in this project phase. | FR-002, FR-003 |
| DOC-REQ-002 | `.specify/memory/constitution.md` Principle VII | Spec artifacts must stay aligned with implementation reality; if behavior changes, update `spec.md`, `plan.md`, and `tasks.md`. | FR-001 |
| DOC-REQ-003 | `AGENTS.md` Testing Instructions | Unit validation must run through `./tools/test_unit.sh` (not direct `pytest`) so CI/local behavior stays consistent. | FR-005 |
| DOC-REQ-004 | Runtime scope guard in feature input | Deliverables must include production runtime changes plus validation tests; docs-only updates are insufficient. | FR-005, FR-006 |
| DOC-REQ-005 | `docs/ManifestTaskSystem.md` §6.6 | Manifest queue metadata/normalization semantics (`manifestHash`, `manifestVersion`, capabilities, secret-hygiene outputs) must remain compatible while hardening request validation. | FR-004 |

### Key Entities *(include if feature involves data)*

- **ManifestRunRequest**: API request model for `POST /api/manifests/{name}/runs`; now explicitly constrained to supported actions.
- **ManifestRunResponse**: API response containing queue metadata (`type`, `requiredCapabilities`, `manifestHash`) and job identifier.
- **ManifestQueuePayload**: Persisted queue payload generated by `normalize_manifest_job_payload`; unchanged by this feature except for upstream request hardening.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `specs/028-manifest-queue` artifacts no longer reference stale paths/contracts and are internally consistent.
- **SC-002**: Invalid action values for manifest run requests are rejected at schema validation time and do not reach queue submission.
- **SC-003**: Valid action values (`plan`, `run`) and default action behavior remain functional and covered by automated tests.
- **SC-004**: `./tools/test_unit.sh` runs with updated runtime + test changes and reports no manifest-scope regressions (any unrelated failures are explicitly documented).
- **SC-005**: Final deliverables include both production runtime behavior updates and validation tests, not specification changes alone.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode scope coverage is explicit in `tasks.md`:
  - Production runtime implementation tasks: `T010`, `T011`, `T023`.
  - Runtime validation tasks: `T012`, `T024`, `T025`.
- `DOC-REQ-*` coverage remains deterministic:
  - Every `DOC-REQ-001` through `DOC-REQ-005` maps to implementation + validation tasks in `tasks.md`.
  - `contracts/requirements-traceability.md` mirrors those mappings with implementation surfaces and validation strategy.

### MEDIUM/LOW remediation status

- Prompt B scope controls were added to `tasks.md` so runtime and `DOC-REQ-*` gates remain visible before implementation.
- User Story 3 task grouping now keeps execution validation (`T024`) in test coverage, improving deterministic ordering.

### Residual risks

- Manifest queue compatibility can still regress if downstream payload-shape changes bypass `manifest_contract` coverage.
- Cross-artifact drift can recur in future edits unless `T020`, `T025`, and `T026` are run and recorded each cycle.
