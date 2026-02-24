# Feature Specification: Canonical Workflow Surface Naming

**Feature Branch**: `040-spec-removal`  
**Created**: 2026-02-24  
**Status**: Draft  
**Input**: User description: "Implement the spec removal plan described in docs/SpecRemovalPlan.md"

## Source Document Requirements

- **DOC-REQ-001**: Canonical naming updates replace legacy `SPEC_WORKFLOW_*` with `WORKFLOW_*`.
- **DOC-REQ-002**: Settings and path naming `spec_workflow` must move to `workflow` in canonical runtime/docs/spec artifacts.
- **DOC-REQ-003**: API route canonicalization uses `/api/workflows/*` in place of legacy `/api/spec-automation/*` and `/api/workflows/speckit/*`.
- **DOC-REQ-004**: Contract/data model identifiers normalize `SpecWorkflow*` to `Workflow*`.
- **DOC-REQ-005**: Metric namespaces normalize `moonmind.spec_workflow*` to `moonmind.workflow*` and `spec_automation.*` variants are replaced/deprecated.
- **DOC-REQ-006**: Artifact directories under `var/artifacts/spec_workflows` are renamed to canonical `var/artifacts/workflow_runs` or `var/artifacts/workflows`.
- **DOC-REQ-007**: Canonical terminology applies across the files listed in the plan; no aliasing language is introduced except in an explicit historical appendix.
- **DOC-REQ-008**: A verification pass must confirm legacy token removal except for explicitly documented migration exceptions.
- **DOC-REQ-009**: This migration wave is docs/spec artifact-only; production runtime behavior changes and runtime tests are captured through US4 follow-up tasks (`T040`, `T041`).
- **DOC-REQ-010**: Delivery includes residual legacy references only through explicit historical sections when traceability requires.

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Canonical migration of surface vocabulary (Priority: P1)

As a spec maintainer, I want all legacy `SPEC` naming in docs and spec artifacts replaced with canonical workflow naming, so contributors use one terminology and reduce operational ambiguity.

**Why this priority**: This is the core value of the initiative and must be completed before any broader rollout.

**Independent Test**: Execute the canonical rename pass over listed `docs/` and `specs/` files, then verify the resulting diff contains only canonical names and the expected migration appendix entries.

**Acceptance Scenarios**:

1. **Given** a target file in the allowed migration surface, **When** the migration pass runs, **Then** canonical terms replace all legacy terms listed in the mapping.
2. **Given** an intentionally preserved historical reference, **When** the pass is reviewed, **Then** it appears only in an explicit migration appendix with no operational guidance using legacy aliases.

---

### User Story 2 - Runtime surface alignment (Priority: P2)

As an operator, I want runtime assets and configuration to adopt canonical workflow naming, so the documentation/spec canon is consistent with actual running behavior.

**Why this priority**: Prevents drift between documented standards and executable behavior and enables reliable verification.

**Independent Test**: Apply canonical naming updates in runtime-relevant artifacts and run focused validation tests for affected paths, env names, metrics, and artifacts.

**Acceptance Scenarios**:

1. **Given** a runtime surface with `SPEC_WORKFLOW_*` or `spec_workflow` naming, **When** the migration is executed, **Then** equivalent `WORKFLOW_*`/`workflow` names are present.
2. **Given** migrated workflow endpoints and metrics, **When** existing tests and sanity checks run, **Then** naming changes remain behaviorally equivalent.
---

### User Story 3 - Verification and governance (Priority: P3)

As a reviewer, I want an auditable verification step, so no unintentional legacy token usage remains outside the migration appendix.

**Why this priority**: This protects from partial migrations and reduces regression risk.

**Independent Test**: Run documented grep verification and review the attached report before handoff.

**Acceptance Scenarios**:

1. **Given** completed migration edits, **When** the verification command runs, **Then** only explicitly whitelisted legacy references remain.
2. **Given** migration artifacts are generated, **When** reviewers inspect them, **Then** the runbook/plan can be approved with traceable rationale for exceptions.

---

### Edge Cases

- What happens when the migration touches non-canonical phrases that include legacy tokens as part of unrelated domain text?
  - Those matches become documented exceptions only when they are clearly historical/non-operational and explicitly justified.
- How are pre-existing historical references in changelogs or archives represented without introducing operational aliasing?
  - Keep legacy references only in dedicated migration appendix sections and keep active operational guidance canonical.
- How does the process behave when a targeted file is intentionally out-of-scope for the current wave?
  - Add the file/path and follow-up owner to residual backlog and keep it out of this execution slice.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST replace legacy tokens with canonical tokens using the mapping defined in the plan for all listed files under `docs/` and `specs/`. (Maps: DOC-REQ-001, DOC-REQ-005)
- **FR-002**: The system MUST provide explicit migration notes for any legacy term left intentionally in a historical appendix. (Maps: DOC-REQ-010)
- **FR-003**: The system MUST ensure runtime naming and contract identifiers in the canonical scope adopt `WORKFLOW*` and `workflow*` terminology where they currently use legacy equivalents. (Maps: DOC-REQ-002)
- **FR-004**: The system MUST update contract names, route families, and operation names to the canonical equivalents listed in the plan. (Maps: DOC-REQ-003, DOC-REQ-004)
- **FR-005**: The system MUST rename/introduce canonical artifact path references for workflow run outputs while avoiding ambiguous mixed naming. (Maps: DOC-REQ-006)
- **FR-006**: The system MUST include validation tests/checks that fail when unapproved legacy references remain outside approved migration exceptions. (Maps: DOC-REQ-008)
- **FR-007**: The system MUST generate a migration verification report that identifies changed files, remaining legacy occurrences, and any unresolved follow-up actions. (Maps: DOC-REQ-008)
- **FR-008**: The system MUST keep migration work bounded to listed file set and planned follow-up scope only, avoiding unrelated refactors. (Maps: DOC-REQ-007)
- **FR-009**: The system MUST preserve production runtime behavior during this docs/spec pass and provide a rollback-safe verification point with explicit runtime follow-up evidence. (Maps: DOC-REQ-009)
- **FR-010**: The system MUST define runtime naming parity validation in the follow-up runtime slice (`T040`/`T041`) for surfaces touched by this migration. (Maps: DOC-REQ-002, DOC-REQ-008)

### Key Entities *(include if feature involves data)*

- **Legacy-Canonical Token Map**: A mapping record of legacy term, canonical replacement, and rationale for each migration decision.
- **Migration Appendix**: A bounded section containing only intentionally retained legacy references and the reason for retention.
- **Verification Artifact**: A machine-readable list of remaining matches and an editorial sign-off checklist for exception approvals.
- **Runtime Naming Surface**: Environment keys, route names, schema identifiers, metrics, and artifact paths that must remain behaviorally stable while renaming for nomenclature.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of targeted legacy tokens in listed files are replaced with canonical forms except those explicitly listed in a migration appendix.
- **SC-002**: Legacy naming checks report zero unintentional matches for `SPEC_WORKFLOW_*`, `/api/spec-automation/*`, `/api/workflows/speckit/*`, `SpecWorkflow*`, `spec_workflow.*`, and `moonmind.spec_workflow*` across migrated surface.
- **SC-003**: Runtime follow-up validation for env/route/config/metric/artifact naming is completed in US4 (`T040`/`T041`) before rollout, with baseline equivalence checks in that follow-up slice.
- **SC-004**: A migration verification report is attached to the plan/spec runbook with a documented list of any follow-up naming items outside this pass.
- **SC-005**: Manual reviewer sign-off confirms no aliasing language remains in active operational guidance except the historical section.
- **SC-006**: The production runtime follow-up (`T040`, `T041`) is recorded in this feature artifacts with acceptance evidence before runtime rollout.
