# Feature Specification: Resolve On-Demand Skill Requests

**Feature Branch**: `317-resolve-on-demand-skill-requests`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-614 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-614 MoonSpec Orchestration Input

## Source

- Jira issue: MM-614
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Resolve approved on-demand Skill requests into derived snapshots
- Priority: Medium
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-614 from MM project
Summary: Resolve approved on-demand Skill requests into derived snapshots
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-614 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-614: Resolve approved on-demand Skill requests into derived snapshots

Source Reference
Source Document: docs/Steps/SkillsOnDemand.md
Source Title: Skills On Demand
Source Sections:
- 5.3 On-Demand Skill Request
- 5.4 Derived Skill Snapshot
- 8.2 moonmind.skills.request
- 9.3 On-demand request
- 9.4 Snapshot lineage
- 12. Failure Behavior

Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-004
- DESIGN-REQ-005
- DESIGN-REQ-008
- DESIGN-REQ-013
- DESIGN-REQ-014

As a managed agent, I want MoonMind to evaluate my request for additional Skills and activate only policy-eligible additions as a new immutable snapshot so active Skill state changes are governed and traceable.

Acceptance Criteria:
- `moonmind.skills.request` validates `current_snapshot_ref`, `requested_skills`, optional versions, reason, runtime_id, and step_id.
- Every request is resolved by MoonMind using existing skill resolution policy, not by the agent or adapter.
- Already-active requested Skills return `no_change` and keep the current snapshot ref.
- Allowed additions create a derived immutable `ResolvedSkillSet` with parent snapshot lineage and requested Skill metadata.
- Denied or failed requests preserve the previous active snapshot and return structured code/message data.

Requirements:
- Add `SkillsOnDemandRequest`, `SkillsOnDemandRequestResult`, and `SkillsOnDemandFailure` contracts.
- Add or extend activity/service behavior for `agent_skill.request_on_demand`.
- Persist compact lineage metadata including parent snapshot, request origin, reason, requested Skills, and resulting refs.
- Keep workflow history compact by carrying refs and metadata rather than Skill bodies.

Relevant Jira links from trusted issue response:
- MM-614 blocks MM-613: Expose policy-aware Skill metadata query for managed runtimes. Current linked status at fetch time: Done.
- MM-614 is blocked by MM-615: Refresh managed runtimes after derived Skill activation. Current linked status at fetch time: Backlog.

Additional orchestration constraints:
- Jira Orchestrate always runs as a runtime implementation workflow.
- If the brief points at an implementation document, treat it as runtime source requirements.
- Source design path: docs/Steps/SkillsOnDemand.md
- Classify the input as a single-story runtime feature request unless later MoonSpec analysis finds the source document requires breakdown.
- Inspect existing MoonSpec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Governed On-Demand Skill Activation

**Summary**: As a managed agent, I want MoonMind to evaluate my request for additional Skills and activate only policy-eligible additions as a new immutable snapshot so active Skill state changes are governed and traceable.

**Goal**: A managed runtime can request additional Skills through MoonMind, receive `no_change` when every requested Skill is already active, receive structured denial for invalid or disallowed requests, or receive compact derived-snapshot refs and activation guidance when the request is allowed.

**Independent Test**: Enable Skills On Demand, provide an active snapshot and policy-eligible catalog entries, call the managed-runtime request path with already-active, allowed-addition, invalid, and disallowed request shapes, then verify the previous snapshot remains unchanged unless an allowed request produces one new derived immutable snapshot with compact lineage and activation metadata.

**Acceptance Scenarios**:

1. **Given** Skills On Demand is enabled and the managed runtime provides a matching current snapshot ref, **When** it requests only Skills already present in the active snapshot, **Then** MoonMind returns `no_change`, preserves the current snapshot ref, and creates no derived snapshot.
2. **Given** Skills On Demand is enabled and the request identifies one or more policy-eligible inactive Skills, **When** MoonMind resolves the request, **Then** it creates a derived immutable active snapshot containing the previous Skills plus the approved additions and returns compact refs plus activation guidance.
3. **Given** a request omits or mismatches the current snapshot ref, includes no requested Skills, or includes blank Skill names, **When** MoonMind validates it, **Then** the request is denied with structured code/message data and the previous active snapshot remains unchanged.
4. **Given** a requested Skill is unavailable, version-incompatible, source-policy denied, or runtime-incompatible, **When** MoonMind evaluates the request, **Then** the request is denied with a safe diagnostic code/message and no derived snapshot is created.
5. **Given** a derived snapshot is created, **When** downstream runtime refresh or next-turn activation consumes the result, **Then** lineage metadata identifies the parent snapshot, requested Skills, request origin, reason, and resulting refs without embedding Skill bodies in workflow history.

### Edge Cases

- Duplicate requested Skill names are treated deterministically and do not create duplicate active entries.
- A request that mixes already-active Skills with one or more allowed additions produces one derived snapshot only for the additions.
- A request that mixes allowed and denied additions fails as a whole unless policy explicitly permits partial activation.
- Runtime identifiers, step identifiers, and reason text are optional but must be rejected when present as blank values.
- Failure responses must not expose hidden Skill body content, secrets, arbitrary artifact refs, or unchecked catalog data.

## Assumptions

- This story covers the enabled-mode request path after the disabled control story and metadata query story are already available.
- Live mid-turn projection mutation is not required; the derived snapshot may be activated on the next managed-session turn or controlled steer point.
- `requires_approval` remains reserved for future use, so this story validates `activated`, `denied`, and `no_change` outcomes only.
- MM-615 covers runtime refresh after derived activation, so this story needs to return refresh-ready metadata but does not own all adapter-specific refresh behavior.

## Source Design Requirements

- **DESIGN-REQ-002** (Source: `docs/Steps/SkillsOnDemand.md` sections 5.3 and 5.4, lines 152-162): A managed-runtime on-demand Skill request is selector intent that MoonMind must validate, resolve, and turn into a derived immutable snapshot only when approved. Scope: in scope. Maps to FR-001, FR-005, FR-006.
- **DESIGN-REQ-004** (Source: `docs/Steps/SkillsOnDemand.md` section 8.2, lines 282-299): The request contract must accept current snapshot ref, requested Skill names with optional versions, optional reason, runtime_id, and step_id. Scope: in scope. Maps to FR-001, FR-002, FR-003.
- **DESIGN-REQ-005** (Source: `docs/Steps/SkillsOnDemand.md` section 8.2, lines 301-328): Request results must support `activated`, `denied`, and `no_change` v1 outcomes with compact refs, activation guidance, and no active-snapshot mutation on failures. Scope: in scope. Maps to FR-004, FR-006, FR-007, FR-008.
- **DESIGN-REQ-008** (Source: `docs/Steps/SkillsOnDemand.md` section 9.3, lines 354-367): The request lifecycle must check feature availability, validate shape, load the active snapshot, combine current and requested selections, apply normal policy gates, create and persist a derived snapshot, materialize it, and return a compact activation summary. Scope: in scope. Maps to FR-001, FR-004, FR-005, FR-006, FR-009, FR-010.
- **DESIGN-REQ-013** (Source: `docs/Steps/SkillsOnDemand.md` section 9.4, lines 369-386): Derived snapshots should preserve compact lineage including parent snapshot, creation reason, requester, requested Skills, and resulting refs without placing large bodies in workflow history. Scope: in scope. Maps to FR-009, FR-010, FR-011.
- **DESIGN-REQ-014** (Source: `docs/Steps/SkillsOnDemand.md` section 12, lines 442-485): Request failures must leave the active snapshot unchanged and return structured denial data for disabled feature, unsupported runtime, invalid/missing snapshot, unavailable Skill/version, policy/runtime/tool denial, artifact/checksum failure, materialization failure, or runtime refresh failure. Scope: in scope. Maps to FR-004, FR-007, FR-008, FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose managed-runtime on-demand Skill request handling only through MoonMind-governed control paths.
- **FR-002**: System MUST validate `current_snapshot_ref`, requested Skill names, optional versions, optional reason, runtime_id, and step_id before resolving requested additions.
- **FR-003**: System MUST reject blank current snapshot refs, blank requested Skill names, blank optional metadata values, and empty requested Skill lists with structured denial data.
- **FR-004**: System MUST preserve the previous active snapshot for every denied or failed request.
- **FR-005**: System MUST resolve requested Skills by applying the same source, version, runtime compatibility, selected Tool policy, and deployment policy gates used for normal Skill resolution.
- **FR-006**: System MUST return `no_change` without creating a new snapshot when all requested Skills are already active in the current snapshot.
- **FR-007**: System MUST create exactly one derived immutable active snapshot when an allowed request adds policy-eligible inactive Skills.
- **FR-008**: System MUST return structured `activated`, `denied`, or `no_change` result data for v1 and MUST NOT emit `requires_approval` until approval semantics are implemented.
- **FR-009**: Activated request results MUST include compact parent snapshot, derived snapshot, resolved skillset, materialization, and activation guidance metadata sufficient for a managed runtime refresh or next-turn activation.
- **FR-010**: Derived snapshot metadata MUST preserve compact lineage for parent snapshot, request origin, reason, requested Skills, and resulting refs.
- **FR-011**: Request and result payloads MUST remain compact and MUST NOT embed Skill bodies or unrestricted body-readable refs in workflow/activity history.
- **FR-012**: Denied and failed request results MUST include safe structured code/message data for invalid request, snapshot not found, Skill not found, version not found, policy denied, runtime incompatible, tool policy denied, artifact unavailable, checksum mismatch, materialization failed, and runtime refresh failed cases where applicable.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-614` and the canonical Jira preset brief.

### Key Entities

- **On-Demand Skill Request**: A managed-runtime request containing the current active snapshot ref, requested Skills with optional versions, and optional runtime context.
- **Derived Skill Snapshot**: A new immutable active Skill set produced from an approved request and linked to its parent snapshot.
- **Request Result**: A compact response that reports `activated`, `denied`, or `no_change` plus safe refs, activation guidance, and denial diagnostics.
- **Snapshot Lineage**: Compact metadata that explains why a derived snapshot exists and which request produced it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of already-active-only request cases return `no_change` and create zero derived snapshots.
- **SC-002**: 100% of allowed-addition request cases produce exactly one derived snapshot containing the previous active Skills plus the approved additions.
- **SC-003**: 100% of invalid request-shape cases return structured denial data before resolution or materialization begins.
- **SC-004**: 100% of policy, version, runtime, artifact, or materialization failure cases preserve the previous active snapshot.
- **SC-005**: Activated responses include compact lineage and activation metadata while keeping Skill bodies out of workflow/activity payloads.
- **SC-006**: Traceability review confirms `MM-614`, the canonical Jira preset brief, and DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-008, DESIGN-REQ-013, and DESIGN-REQ-014 remain preserved across MoonSpec artifacts and final verification evidence.
