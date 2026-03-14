# Feature Specification: Canonical Workflow Surface Naming

**Feature Branch**: `040-spec-removal`  
**Created**: 2026-02-24  
**Status**: Draft  
**Input**: User description: "Implement docs/SpecRemovalPlan.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/SpecRemovalPlan.md` - "Canonical naming target", item 1 | Canonical naming updates replace legacy `WORKFLOW_*` with `WORKFLOW_*`. |
| DOC-REQ-002 | `docs/SpecRemovalPlan.md` - "Canonical naming target", item 2 | Settings and path naming `workflow` must move to `workflow`. |
| DOC-REQ-003 | `docs/SpecRemovalPlan.md` - "Canonical naming target", item 3 | API route canonicalization uses `/api/workflows/*` in place of legacy workflow route families. |
| DOC-REQ-004 | `docs/SpecRemovalPlan.md` - "Canonical naming target", item 4 | Contract/data model identifiers normalize `Workflow*` to `Workflow*`. |
| DOC-REQ-005 | `docs/SpecRemovalPlan.md` - "Canonical naming target", item 5 | Metric namespaces normalize `moonmind.workflow*` to `moonmind.workflow*`; legacy automation metric prefixes are removed. |
| DOC-REQ-006 | `docs/SpecRemovalPlan.md` - "Canonical naming target", item 6 | Artifact directories under `var/artifacts/workflows` are renamed to canonical workflow artifact roots. |
| DOC-REQ-007 | `docs/SpecRemovalPlan.md` - "Hard Rules for this migration pass", items 1-3 | Canonical terminology applies consistently with no new aliasing language outside dedicated historical traceability sections. |
| DOC-REQ-008 | `docs/SpecRemovalPlan.md` - "Workstreams and sequencing", "Verification pass" | A verification pass must confirm legacy token removal except for explicitly documented migration exceptions. |
| DOC-REQ-009 | `docs/SpecRemovalPlan.md` - "Scope", item 3 | The source plan frames a docs/spec-only migration pass and excludes runtime code changes in that plan slice. |
| DOC-REQ-010 | `docs/SpecRemovalPlan.md` - "Deliverable checklist", item 3 | Delivery includes a verification report listing residual legacy references only in explicit historical sections. |
| DOC-REQ-011 | Task objective runtime scope guard (Step 2/16) | Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. |

### Source Requirement Reconciliation

- **DOC-REQ-009** is preserved as a source constraint from the planning document and treated as the baseline scope of the original doc-authored pass.
- Runtime intent (`DOC-REQ-011`) is authoritative for this implementation feature and extends the plan into production runtime code and test validation so delivery is not docs/spec-only.

## Clarifications

### Session 2026-03-01

- Q: Which feature spec is authoritative when `.specify` branch-name prechecks cannot resolve a `NNN-feature-name` branch? → A: Use `specs/040-spec-removal/spec.md` as the authoritative feature spec for this run.
- Q: When `docs/SpecRemovalPlan.md` docs/spec-only scope conflicts with the runtime delivery objective, which requirement wins? → A: Runtime delivery intent (`DOC-REQ-011`) is authoritative for this feature; docs/spec-only language remains historical planning context.

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

1. **Given** a runtime surface with `WORKFLOW_*` or `workflow` naming, **When** the migration is executed, **Then** equivalent `WORKFLOW_*`/`workflow` names are present.
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
- **FR-008**: The system MUST keep migration work bounded to listed workflow naming surfaces and the runtime parity surfaces they imply, avoiding unrelated refactors. (Maps: DOC-REQ-007)
- **FR-009**: The system MUST implement production runtime code changes for canonical naming parity across environment/config, API routes/contracts, metrics, and artifact-path runtime surfaces. (Maps: DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-011)
- **FR-010**: The system MUST ship validation tests that exercise renamed runtime surfaces and fail on regressions or unapproved legacy naming reintroduction. (Maps: DOC-REQ-008, DOC-REQ-011)
- **FR-011**: The system MUST preserve the planning document's historical-traceability intent by retaining legacy terminology only in explicitly labeled migration-context sections. (Maps: DOC-REQ-009, DOC-REQ-010)

### Key Entities *(include if feature involves data)*

- **Legacy-Canonical Token Map**: A mapping record of legacy term, canonical replacement, and rationale for each migration decision.
- **Migration Appendix**: A bounded section containing only intentionally retained legacy references and the reason for retention.
- **Verification Artifact**: A machine-readable list of remaining matches and an editorial sign-off checklist for exception approvals.
- **Runtime Naming Surface**: Environment keys, route names, schema identifiers, metrics, and artifact paths that must remain behaviorally stable while renaming for nomenclature.

### Assumptions & Dependencies

- Runtime rename changes can be introduced without changing billing, queue semantics, or model-effort pass-through behavior.
- Existing CI/unit test harnesses and workflow validation scripts are available to prove parity after runtime naming updates.
- Any unavoidable compatibility shims are explicitly rejected in favor of fail-fast behavior for unsupported legacy runtime inputs.
- Active feature context for this run is `specs/040-spec-removal/` based on explicit task objective and matching feature artifacts.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of targeted legacy tokens in listed files are replaced with canonical forms except those explicitly listed in a migration appendix.
- **SC-002**: Legacy naming checks report zero unintentional matches for `WORKFLOW_*`, `/api/spec-automation/*`, `/api/workflows/agentkit/*`, `Workflow*`, `workflow.*`, and `moonmind.workflow*` across migrated surface.
- **SC-003**: Production runtime code changes for canonical naming are present in the implementation diff and cover environment/config, API, metrics, and artifact surfaces described by this spec.
- **SC-004**: A migration verification report is attached to the plan/spec runbook with a documented list of any follow-up naming items outside this pass.
- **SC-005**: Manual reviewer sign-off confirms no aliasing language remains in active operational guidance except the historical section.
- **SC-006**: Validation test runs demonstrate renamed runtime surfaces behave equivalently and fail on legacy-token regressions before rollout.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode requirement coverage is explicit and deterministic across artifacts:
  - Production runtime code task coverage in `tasks.md`: `T004-T006`, `T017-T021`.
  - Validation task coverage in `tasks.md`: `T015`, `T016`, `T022`, `T023`.
- `DOC-REQ-*` coverage guard is explicit:
  - One-to-one source requirements are listed in this spec.
  - Per-`DOC-REQ-*` implementation and validation task mappings are defined in `contracts/requirements-traceability.md`.

### MEDIUM/LOW remediation status

- Determinism controls are recorded in `plan.md` and `tasks.md` so future task regeneration retains runtime-vs-docs authority and verification expectations.

### Residual risks

- Broad rename scope across many docs/spec files can regress without continuous scan enforcement.
- Runtime env/route/schema renames can surface hidden downstream dependencies during rollout; dedicated runtime validation tasks remain mandatory before publish.
