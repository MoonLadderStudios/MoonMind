# Feature Specification: Record Audit Events and Failure Diagnostics for Skills On Demand

**Feature Branch**: `319-audit-failure-diagnostics`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
For a single-story Jira preset brief, run moonspec-specify unless an active spec.md already passes the specify gate.
For a broad technical or declarative design, run moonspec-breakdown first, then select the recommended first generated spec unless the issue brief explicitly requires processing all specs.
Preserve Jira issue MM-616 and the original preset brief in spec.md so final verification can compare against them.

Canonical Jira preset brief:

# MM-616 MoonSpec Orchestration Input

## Source

- Jira issue: MM-616
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Record audit events and failure diagnostics for Skills On Demand
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:4ff1fd95-32ef-4b5a-b46e-2b9152af11cb/artifacts/moonspec-inputs/MM-616-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.
- Label: moonmind-workflow-mm-7bdf3ad6-c14c-4add-bc67-7352bceee655

## Canonical MoonSpec Feature Request

Jira issue: MM-616 from MM project
Summary: Record audit events and failure diagnostics for Skills On Demand
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-616 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-616: Record audit events and failure diagnostics for Skills On Demand

Source Reference
Source Document: docs/Steps/SkillsOnDemand.md
Source Title: Skills On Demand
Source Sections:
- 12. Failure Behavior
- 13. Observability and Audit
- 14. Security Rules
- 16. Test Cases
Coverage IDs:
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-014
As an operator, I want every Skills On Demand query and request to leave bounded audit evidence and actionable diagnostics so I can understand approvals, denials, snapshot transitions, and failures without exposing secrets or high-cardinality raw text.
Acceptance Criteria
- Each query records a skills_on_demand.query event with bounded fields and query_hash rather than raw high-cardinality query text in metrics.
- Each request records a skills_on_demand.request event with result, result_code, requested Skill names, parent/derived snapshot identifiers where applicable, and diagnostics refs where applicable.
- Failure responses use documented codes such as feature_disabled, policy_denied, snapshot_not_found, materialization_failed, and runtime_refresh_failed.
- Audit and diagnostics outputs do not expose secrets, full Skill bodies, or arbitrary artifact/database access.
Requirements
- Integrate Skills On Demand control paths with existing observability/audit or artifact-backed diagnostic mechanisms.
- Normalize failure codes and diagnostics consistently across query, request, materialization, and refresh failures.
- Include test coverage for the documented failure and observability matrix.

Relevant Jira links from trusted issue response:
- MM-615: Refresh managed runtimes after derived Skill activation (Blocks, outwardIssue)

## Relevant Implementation Notes

- Source design path: `docs/Steps/SkillsOnDemand.md`.
- Section 12 Failure Behavior defines fail-no-change behavior for disabled features, unsupported runtimes, invalid snapshots, missing or disallowed Skills, version resolution failures, runtime incompatibility, disallowed Tools, artifact/checksum failures, materialization failures, and runtime refresh failures.
- Section 12 also defines structured `SkillsOnDemandFailure` output with `status: "denied"`, stable failure `code`, human-readable `message`, optional `current_snapshot_ref`, and optional `diagnostics_ref`.
- Section 13 Observability and Audit requires one bounded event for each `skills_on_demand.query` and `skills_on_demand.request`; high-cardinality natural-language query text belongs behind hashes or diagnostics artifacts, not raw metrics fields.
- Section 13 request events include workflow/run/step/runtime identifiers, parent and derived snapshot identifiers, requested Skill names, result, result code, manifest ref, and diagnostics ref where applicable.
- Section 14 Security Rules prohibit secret exposure, hidden Skill body disclosure, arbitrary database/artifact access for Skill refs, mutable runtime projection publication as repo-authored changes, and policy bypasses for repo/local Skill sources.
- Section 16 Test Cases cover disabled feature, bounded query metadata, already-active Skill request, allowed Skill activation, policy denial, and materialization failure diagnostics.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for Skills On Demand audit and diagnostics behavior: implement bounded audit events and failure diagnostics across query, request, materialization, and refresh failures while preserving MM-616 traceability and the referenced design requirement IDs.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Bounded Skills On Demand Audit and Diagnostics

**Summary**: As an operator, I want every Skills On Demand query and request to leave bounded audit evidence and actionable diagnostics so I can understand approvals, denials, snapshot transitions, and failures without exposing secrets or high-cardinality raw text.

**Goal**: Operators can inspect Skills On Demand outcomes through consistent audit evidence and safe diagnostics for query, request, materialization, and refresh paths, while managed agents and observability surfaces receive only bounded metadata and stable failure codes.

**Independent Test**: Exercise Skills On Demand query and request flows for successful results, disabled feature, invalid or missing snapshot, unavailable or denied Skills, materialization failure, and runtime refresh failure; verify each flow records the expected bounded audit event, returns stable diagnostic codes where applicable, preserves active snapshots on failure, and exposes no secrets, Skill bodies, raw long query text, or arbitrary artifact/database access.

**Acceptance Scenarios**:

1. **Given** a managed runtime performs a Skills On Demand query, **When** MoonMind evaluates the query, **Then** it records a `skills_on_demand.query` audit event with bounded identifiers, query hash, result count, denial state, and denial code where applicable, without storing raw high-cardinality query text in metrics.
2. **Given** a managed runtime performs a Skills On Demand request, **When** MoonMind resolves the request, **Then** it records a `skills_on_demand.request` audit event with bounded workflow, runtime, snapshot, requested Skill, result, result code, derived snapshot, manifest, and diagnostics references where applicable.
3. **Given** Skills On Demand fails because the feature is disabled, the runtime is unsupported, the snapshot is invalid, a Skill or version is unavailable, policy denies the request, compatibility fails, an artifact or checksum cannot be used, materialization fails, or runtime refresh fails, **When** MoonMind returns the failure, **Then** the active snapshot remains unchanged and the response includes a stable failure code and safe message.
4. **Given** audit or diagnostics evidence is produced, **When** an operator or managed runtime inspects it, **Then** the evidence is sufficient to understand approval, denial, snapshot transition, and failure outcomes without exposing secrets, full Skill bodies, raw long query text, or arbitrary artifact/database access.
5. **Given** a denied request occurs, **When** MoonMind records and returns the result, **Then** the denial is visible enough for operators to understand why it was denied while still enforcing normal source, local-only, deployment, Tool, and runtime policy boundaries.

### Edge Cases

- A query produces zero results, very many possible matches, or is denied before catalog lookup completes.
- A request mixes already-active Skills with denied, unavailable, or version-incompatible Skills.
- A materialization failure happens after resolution succeeds but before any runtime can safely observe the derived snapshot.
- A runtime refresh failure happens after materialization has produced diagnostic context.
- Diagnostic references are unavailable or cannot be written.
- A request includes long natural-language reason text or high-cardinality query text.

## Assumptions

- MM-614 owns derived snapshot request resolution and MM-615 owns managed-runtime activation refresh; this story owns the audit and diagnostic evidence those paths emit or consume.
- Long natural-language query or reason text may be placed in controlled diagnostic artifacts when needed, but high-cardinality metrics and workflow-visible metadata remain bounded.
- `requires_approval` remains a reserved result value unless another story implements approval semantics.
- Existing operator-visible observability, audit, or artifact-backed diagnostic mechanisms may be reused as long as the observable behavior in this specification is satisfied.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: `docs/Steps/SkillsOnDemand.md` section 12, lines 442-456): Skills On Demand failures must leave the active snapshot unchanged for disabled feature, unsupported runtime, invalid snapshot, unavailable Skill or version, policy denial, runtime incompatibility, Tool policy denial, artifact or checksum failure, materialization failure, and runtime refresh failure. Scope: in scope. Maps to FR-001, FR-002, FR-006, FR-010.
- **DESIGN-REQ-002** (Source: `docs/Steps/SkillsOnDemand.md` section 12, lines 458-486): Failure responses should use a structured denial shape with a stable code, message, optional current snapshot ref, and optional diagnostics ref, including documented codes for expected denial and failure classes. Scope: in scope. Maps to FR-003, FR-004, FR-005, FR-006.
- **DESIGN-REQ-003** (Source: `docs/Steps/SkillsOnDemand.md` section 13, lines 490-509): Each query should record one audit or observability event with bounded workflow, run, step, runtime, snapshot, query hash, result count, denial, and denial-code fields. Scope: in scope. Maps to FR-007, FR-008, FR-009.
- **DESIGN-REQ-004** (Source: `docs/Steps/SkillsOnDemand.md` section 13, lines 511-528): Each request should record one audit or observability event with bounded workflow, run, step, runtime, parent snapshot, requested Skills, result, result code, derived snapshot, manifest, and diagnostics fields where applicable. Scope: in scope. Maps to FR-010, FR-011, FR-012.
- **DESIGN-REQ-005** (Source: `docs/Steps/SkillsOnDemand.md` section 13, line 530): Long natural-language query text must not be stored raw in high-cardinality metrics; metrics should use a hash and detailed diagnostics should be placed in controlled artifacts if needed. Scope: in scope. Maps to FR-008, FR-013, FR-014.
- **DESIGN-REQ-006** (Source: `docs/Steps/SkillsOnDemand.md` section 14, lines 536-544): Skills On Demand audit and diagnostics behavior must not expose secrets, hidden Skill bodies, arbitrary artifact or database access, policy bypasses, local-only or repo Skill misuse, or repo-authored projection changes. Scope: in scope. Maps to FR-013, FR-014, FR-015, FR-016.
- **DESIGN-REQ-007** (Source: `docs/Steps/SkillsOnDemand.md` section 16, lines 579-601): Validation should cover disabled feature, bounded query metadata, already-active requests, allowed requests, policy denial, and materialization failure diagnostics. Scope: in scope. Maps to FR-017, FR-018.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST preserve the current active Skill snapshot whenever a Skills On Demand query or request fails before a derived snapshot can be safely activated.
- **FR-002**: System MUST preserve the current active Skill snapshot when materialization or runtime refresh fails after request resolution.
- **FR-003**: Skills On Demand failure responses MUST include safe structured denial data with a stable code and human-readable message.
- **FR-004**: Failure responses SHOULD include the current snapshot reference when it is safe and relevant for operator diagnosis.
- **FR-005**: Failure responses SHOULD include a diagnostics reference when bounded diagnostic evidence is available.
- **FR-006**: System MUST distinguish at least `feature_disabled`, `unsupported_runtime`, `invalid_request`, `snapshot_not_found`, `skill_not_found`, `version_not_found`, `policy_denied`, `runtime_incompatible`, `tool_policy_denied`, `artifact_unavailable`, `checksum_mismatch`, `materialization_failed`, and `runtime_refresh_failed` outcomes where those outcomes can occur.
- **FR-007**: Each Skills On Demand query MUST record one bounded `skills_on_demand.query` audit or observability event.
- **FR-008**: Query audit evidence MUST include a bounded query hash rather than raw long natural-language query text in high-cardinality metrics.
- **FR-009**: Query audit evidence MUST include bounded workflow, run, step, runtime, current snapshot, result count, denied flag, and denial-code fields where available.
- **FR-010**: Each Skills On Demand request MUST record one bounded `skills_on_demand.request` audit or observability event.
- **FR-011**: Request audit evidence MUST include bounded workflow, run, step, runtime, parent snapshot, requested Skill names, result, result code, derived snapshot, manifest, and diagnostics references where available.
- **FR-012**: Request audit evidence MUST support `activated`, `denied`, `requires_approval`, and `no_change` result values without requiring approval behavior to be implemented by this story.
- **FR-013**: Audit events and diagnostics MUST NOT expose secrets, full Skill bodies, hidden Skill content, raw long query text in metrics, arbitrary database access, or arbitrary artifact access.
- **FR-014**: Detailed diagnostic evidence that would exceed bounded metric or event fields MUST be stored behind controlled diagnostic references rather than embedded directly in high-cardinality surfaces.
- **FR-015**: Denied requests MUST remain operator-understandable without bypassing source policy, local-only Skill policy, repo Skill source boundaries, Tool policy, or runtime compatibility policy.
- **FR-016**: Runtime projection changes under `.agents/skills` MUST NOT be represented as repo-authored source changes in audit or diagnostics evidence.
- **FR-017**: Test evidence MUST cover disabled feature, bounded query metadata, already-active request, allowed request, policy denial, materialization failure, and runtime refresh failure behavior.
- **FR-018**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-616` and the original Jira preset brief for traceability.

### Key Entities

- **Skills On Demand Query Event**: Bounded audit evidence for a managed-runtime query, including identifiers, query hash, result count, and denial state.
- **Skills On Demand Request Event**: Bounded audit evidence for a managed-runtime request, including identifiers, requested Skills, result, result code, snapshot refs, manifest refs, and diagnostics refs.
- **Failure Diagnostic**: Safe structured evidence describing why a query, request, materialization, or runtime refresh failed without changing the active snapshot.
- **Diagnostic Reference**: A controlled pointer to larger diagnostic evidence that is safe for the intended operator surface.
- **Active Skill Snapshot**: The currently effective immutable Skill set that must remain unchanged whenever a query, request, materialization, or refresh failure occurs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of exercised Skills On Demand query paths emit exactly one bounded query audit or observability event.
- **SC-002**: 100% of exercised Skills On Demand request paths emit exactly one bounded request audit or observability event.
- **SC-003**: 100% of exercised failure paths return one documented stable failure code and preserve the current active snapshot.
- **SC-004**: 100% of query audit metric records use a query hash rather than raw long natural-language query text.
- **SC-005**: 100% of audit and diagnostic outputs inspected in validation contain no secrets, full Skill bodies, hidden Skill content, arbitrary artifact/database access, or repo-authored projection mutations.
- **SC-006**: Validation covers at least disabled feature, bounded query metadata, already-active request, allowed request, policy denial, materialization failure, and runtime refresh failure cases.
- **SC-007**: Traceability review confirms `MM-616`, the original Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-007 remain preserved across MoonSpec artifacts and final verification evidence.
