# Feature Specification: Policy-Aware Skill Query

**Feature Branch**: `316-policy-aware-skill-query`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-613 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-613 MoonSpec Orchestration Input

## Source

- Jira issue: MM-613
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose policy-aware Skill metadata query for managed runtimes
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.
- Trusted response artifact: `/work/agent_jobs/mm:c890dc23-df31-428f-bf72-53621ff56aa0/artifacts/moonspec-inputs/MM-613-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-613 from MM project
Summary: Expose policy-aware Skill metadata query for managed runtimes
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-613 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-613: Expose policy-aware Skill metadata query for managed runtimes

Source Reference
Source Document: docs/Steps/SkillsOnDemand.md
Source Title: Skills On Demand
Source Sections:
- 5.2 On-Demand Skill Query
- 6. Core Invariants
- 8.1 moonmind.skills.query
- 9.2 On-demand query
- 14. Security Rules
Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-010
- DESIGN-REQ-013
- DESIGN-REQ-014

As a managed agent, I want to ask MoonMind for bounded metadata about available Skills so I can discover relevant help without receiving hidden Skill bodies or bypassing deployment policy.

Acceptance Criteria
- moonmind.skills.query validates query, runtime_id, current_snapshot_ref, and max_results inputs.
- Results include metadata fields such as name, title, description, latest_version, source_kind, supported_runtimes, eligible, in_current_snapshot, and eligibility_summary where available.
- Results never include full Skill bodies or direct content refs that permit body reads.
- Ineligible matches are either filtered or explicitly marked eligible false with diagnostic summaries.
- Query payloads and results remain bounded for workflow/activity use.

Requirements
- Add SkillsOnDemandQuery and SkillsOnDemandQueryResult contracts.
- Reuse existing Skill resolver/catalog primitives instead of creating a parallel catalog.
- Enforce source-kind restrictions, runtime compatibility summaries, and content exposure limits on query results.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Policy-Aware Skill Metadata Discovery

**Summary**: As a managed agent, I want to query MoonMind for bounded metadata about available Skills so that I can discover relevant help without receiving hidden Skill bodies or bypassing deployment policy.

**Goal**: Managed runtimes can perform a governed Skill metadata query that validates caller input, returns only safe metadata, reflects eligibility and active-snapshot context, and preserves all existing Skill policy boundaries.

**Independent Test**: This can be tested independently by enabling Skills On Demand for a managed-runtime query path, submitting valid and invalid Skill metadata queries, and confirming that successful results are bounded metadata-only responses while denied or ineligible cases do not expose Skill bodies or alter active snapshots.

**Acceptance Scenarios**:

1. **Given** Skills On Demand query support is enabled and a managed runtime submits a valid query with runtime and snapshot context, **When** MoonMind evaluates the query, **Then** the response includes only bounded Skill metadata fields and marks whether each result is eligible and already in the current snapshot.
2. **Given** a managed runtime submits a query with missing, blank, malformed, or excessive input values, **When** MoonMind validates the request, **Then** the request is rejected or constrained with a structured failure that identifies the invalid input without returning catalog contents.
3. **Given** a matching Skill is blocked by source policy or runtime compatibility, **When** the managed runtime queries for it, **Then** MoonMind either filters the match from results or returns it with `eligible` set to false and a compact diagnostic summary.
4. **Given** a Skill has body content, artifact refs, or hidden source details, **When** it appears in query results, **Then** the response omits full bodies and direct content refs that would permit unmanaged body reads.
5. **Given** a managed runtime includes the current snapshot reference, **When** query results overlap with already active Skills, **Then** each overlapping result identifies that it is already present in the current snapshot.

### Edge Cases

- Queries with an empty or whitespace-only search term are handled deterministically without returning an unbounded catalog dump.
- Requests with `max_results` below or above supported bounds are rejected or constrained according to the public query contract.
- Unknown runtime identifiers or invalid current snapshot references do not bypass policy or expose hidden catalog details.
- Ineligible results include only diagnostic metadata that is safe for operators and managed agents to see.
- No query outcome creates, mutates, or materializes a new active Skill snapshot.

## Assumptions

- The query story is limited to metadata discovery; activating additional Skills remains a separate on-demand request story.
- Runtime intent is required because the source brief asks for product behavior, not documentation-only alignment.
- The existing Skill catalog and resolver policy remain the authoritative source of eligibility and metadata.

## Source Design Requirements

- **DESIGN-REQ-002** (`docs/Steps/SkillsOnDemand.md` section 5.2 On-Demand Skill Query): Managed runtimes may query for available Skill metadata but must not directly fetch or inspect hidden Skill bodies. Scope: in scope. Mapped to FR-001, FR-004, FR-005.
- **DESIGN-REQ-003** (`docs/Steps/SkillsOnDemand.md` section 6 Core Invariants): MoonMind must resolve and police Skill access, keep snapshots immutable, keep workflow payloads compact, and avoid exposing large Skill bodies in runtime-visible data. Scope: in scope. Mapped to FR-004, FR-006, FR-008, FR-009.
- **DESIGN-REQ-010** (`docs/Steps/SkillsOnDemand.md` section 8.1 moonmind.skills.query): The query contract must validate query, runtime, snapshot, and result limit inputs and return metadata-only search results with eligibility and current-snapshot indicators. Scope: in scope. Mapped to FR-001, FR-002, FR-003, FR-005, FR-007.
- **DESIGN-REQ-013** (`docs/Steps/SkillsOnDemand.md` section 9.2 On-demand query): Query handling must check feature availability, identify runtime and active snapshot context, search allowed Skill metadata, return bounded results, and record the query for observability. Scope: in scope. Mapped to FR-001, FR-002, FR-003, FR-006, FR-010.
- **DESIGN-REQ-014** (`docs/Steps/SkillsOnDemand.md` section 14 Security Rules): Query responses must preserve security boundaries by preventing secret exposure, hidden body exposure, arbitrary content reads, and source-policy bypass. Scope: in scope. Mapped to FR-004, FR-006, FR-008, FR-009.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a managed-runtime Skill metadata query capability only through MoonMind-governed control paths.
- **FR-002**: System MUST validate query text, runtime identity, current snapshot reference, and requested result limit before returning Skill metadata.
- **FR-003**: System MUST return bounded query results that include, when available, Skill name, title, description, latest version, source kind, supported runtimes, eligibility, current-snapshot membership, and eligibility summary.
- **FR-004**: System MUST NOT return full Skill bodies, unrestricted content references, secrets, or hidden catalog details in query responses.
- **FR-005**: System MUST identify whether each returned Skill is already present in the current active snapshot when snapshot context is supplied.
- **FR-006**: System MUST apply the same source-kind restrictions, runtime compatibility checks, and deployment policy constraints used for normal Skill resolution.
- **FR-007**: System MUST handle ineligible matches by either filtering them from results or marking them ineligible with safe diagnostic summaries.
- **FR-008**: System MUST ensure Skill metadata queries do not create, mutate, materialize, or activate Skill snapshots.
- **FR-009**: System MUST keep query payloads and results compact enough for managed workflow and activity boundaries.
- **FR-010**: System MUST record enough query outcome information for operator observability without storing unsafe high-cardinality or sensitive body content in runtime-visible results.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-613` and the canonical Jira preset brief.

### Key Entities

- **Skill Metadata Query**: A managed-runtime request containing query text, optional runtime identity, optional current snapshot reference, and a bounded maximum result count.
- **Skill Metadata Result**: A metadata-only representation of a matching Skill, including display fields, source and runtime compatibility indicators, eligibility, and current-snapshot membership.
- **Eligibility Summary**: A compact explanation of why a Skill is eligible or ineligible for the requesting runtime and deployment policy.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid query responses contain only metadata fields and no full Skill body content or direct body-readable refs.
- **SC-002**: 100% of invalid query-shape cases return a structured denial or validation failure before catalog results are returned.
- **SC-003**: Query responses never return more results than the accepted result limit.
- **SC-004**: At least one validation path confirms that ineligible matches are filtered or marked `eligible: false` with a safe diagnostic summary.
- **SC-005**: At least one validation path confirms that a Skill already active in the current snapshot is marked as present in that snapshot.
- **SC-006**: Final verification confirms `MM-613` and DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-013, and DESIGN-REQ-014 remain traceable across MoonSpec artifacts.
