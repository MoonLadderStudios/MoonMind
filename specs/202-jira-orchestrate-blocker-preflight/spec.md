# Feature Specification: Jira Orchestrate Blocker Preflight

**Feature Branch**: `202-jira-orchestrate-blocker-preflight`
**Created**: 2026-04-17
**Status**: Draft
**Input**: User description:

```text
Jira issue: MM-398 from MM project
Summary: Jira orchestrate should not proceed if the issue is marked as blocked by another issue that is not done yet
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-398 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-398: Jira orchestrate should not proceed if the issue is marked as blocked by another issue that is not done yet

Source Reference
- Source Document: api_service/data/task_step_templates/jira-orchestrate.yaml
- Source Title: Jira Orchestrate preset
- Source Sections:
  - Load Jira preset brief
  - Classify request and resume point
  - Create or select Moon Spec
  - Implement the task breakdown
  - Create pull request
  - Move Jira issue to Code Review
- Related Design:
  - specs/173-jira-orchestrate-preset/spec.md
  - docs/Tools/JiraIntegration.md

User Story
As a Jira Orchestrate operator, I want orchestration to stop before implementation when the requested Jira issue is blocked by another Jira issue that is not done, so dependent work does not start before its prerequisite is complete.

Acceptance Criteria
- Jira Orchestrate performs a trusted Jira dependency preflight after fetching the target issue and before MoonSpec implementation work starts.
- If the target issue is marked as blocked by one or more linked Jira issues whose status is not Done, orchestration stops before MoonSpec specify/plan/tasks/implement, pull request creation, and Code Review transition.
- The blocked outcome reports the target issue key, each blocking issue key available from the trusted Jira response, and each blocking issue status available from the trusted Jira response.
- If all blocking issues are Done, or the issue has no blocker links, Jira Orchestrate proceeds with the existing MoonSpec lifecycle unchanged.
- The guard uses the trusted Jira tool surface and Jira issue-link metadata; it does not scrape Jira, hardcode transition IDs, or infer blocker state from prompt text alone.
- Existing Jira Orchestrate behavior remains unchanged for moving the target issue to In Progress before implementation and moving it to Code Review only after a confirmed pull request URL exists.

Requirements
- Add a Jira blocker/dependency preflight to the Jira Orchestrate flow before implementation work can begin.
- Detect blocker links from the trusted `jira.get_issue` response shape available to managed agents.
- Treat linked blocker issues as satisfied only when their Jira status is Done.
- Fail closed when a blocker link is present but the linked blocker issue status cannot be determined through trusted Jira data.
- Preserve MM-398 in all downstream MoonSpec artifacts, verification output, commit text, and pull request metadata.
- Keep the change within the trusted Jira boundary; do not introduce raw Jira credential use in agent shells or client-side scraping.

Relevant Implementation Notes
- The seeded preset definition currently lives at `api_service/data/task_step_templates/jira-orchestrate.yaml`.
- The original Jira Orchestrate preset behavior is documented in `specs/173-jira-orchestrate-preset/spec.md`.
- The current seeded flow transitions the issue to In Progress, loads the Jira preset brief, runs MoonSpec orchestration, creates a pull request, and moves the issue to Code Review.
- The new guard should run after the target issue is fetched and before any MoonSpec implementation stage can start.
- The guard should inspect Jira issue links that represent blocking relationships. In Jira's common blocker-link wording, this means the target issue is "is blocked by" another issue; implementation should use the normalized or raw link metadata returned by the trusted Jira tool rather than relying on a single display string when structured fields are available.
- When blocker issue status is not embedded in the first `jira.get_issue` response, the flow may fetch the linked blocker issue through trusted `jira.get_issue` before deciding whether to proceed.
- A non-Done blocker should produce a deterministic blocked result, not a generic failure.
- The blocked result should be operator-readable and should not transition MM-398 to Code Review or create a pull request.
- The trusted Jira fetch for MM-398 at brief-build time showed no description, acceptance criteria, implementation notes, labels, or exposed preset-brief fields; this synthesized brief is therefore intentionally scoped to the issue summary and relevant existing Jira Orchestrate behavior.

Verification
- Verify a Jira Orchestrate run for an issue with an unresolved blocker stops before MoonSpec specify/plan/tasks/implement.
- Verify the blocked output includes the target issue key, blocker issue key, and blocker status when available.
- Verify a Jira Orchestrate run for an issue whose blockers are all Done proceeds through the existing MoonSpec lifecycle.
- Verify a Jira Orchestrate run for an issue with no blocker links proceeds as it did before this change.
- Verify the guard uses trusted Jira tool calls and does not require raw Atlassian credentials in the agent runtime.
- Verify existing Jira Orchestrate tests for In Progress transition, MoonSpec lifecycle, pull request creation, and Code Review transition still pass or are updated to include the new preflight step.
- Preserve MM-398 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- None exposed by the trusted MM-398 Jira issue response at fetch time.
```

## User Story - Stop Blocked Jira Orchestration

**Summary**: As a Jira Orchestrate operator, I want the workflow to stop before implementation when the target Jira issue is blocked by an unresolved issue, so dependent work does not start before its prerequisite is complete.

**Goal**: Prevent Jira Orchestrate from starting MoonSpec implementation, creating a pull request, or moving the target issue to Code Review when Jira dependency evidence shows the target issue is blocked by another issue that is not Done.

**Independent Test**: Can be fully tested by running the Jira Orchestrate flow against target issues representing unresolved blockers, resolved blockers, and no blockers, then verifying the blocked case stops before implementation while the resolved and unblocked cases continue normally.

**Acceptance Scenarios**:

1. **Given** a target Jira issue has a blocker relationship to another issue whose status is not Done, **When** Jira Orchestrate evaluates the issue before implementation work, **Then** orchestration stops with a blocked outcome before MoonSpec implementation, pull request creation, and Code Review transition.
2. **Given** a target Jira issue has blocker relationships only to issues whose statuses are Done, **When** Jira Orchestrate evaluates the issue before implementation work, **Then** orchestration proceeds through the existing MoonSpec lifecycle.
3. **Given** a target Jira issue has no blocker relationships, **When** Jira Orchestrate evaluates the issue before implementation work, **Then** orchestration proceeds as it did before this feature.
4. **Given** a blocker relationship is present but the blocking issue status cannot be determined from trusted Jira data, **When** Jira Orchestrate evaluates the issue, **Then** orchestration stops with an operator-readable blocked outcome rather than guessing.
5. **Given** orchestration stops because of an unresolved blocker, **When** the operator reviews the result, **Then** the result identifies the target issue key, the available blocking issue key, and the available blocking issue status.

### Edge Cases

- The target issue has multiple blocker relationships, with a mix of Done and non-Done blocking issues.
- The target issue has Jira links that are not blocker relationships.
- The blocker issue key is available but its status is missing or unavailable from trusted Jira data.
- The target issue is already In Progress before the blocker preflight runs.
- The trusted Jira tool surface is unavailable or policy-denied while blocker state is needed.

## Assumptions

- Jira issue status `Done` is the only status that satisfies a blocking prerequisite for this story.
- Existing Jira Orchestrate behavior for moving the target issue to In Progress before implementation remains in place; this story adds a pre-implementation blocker gate without redefining the whole preset lifecycle.
- Existing Jira Orchestrate behavior for moving the target issue to Code Review remains gated on a confirmed pull request URL.
- A missing or unavailable blocker status is treated as blocked because proceeding would risk starting dependent work prematurely.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `specs/173-jira-orchestrate-preset/spec.md`, Acceptance Scenario 2 and FR-003. Jira Orchestrate moves the requested issue to In Progress before implementation work. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-002**: Source `specs/173-jira-orchestrate-preset/spec.md`, Acceptance Scenario 3 and FR-004. Jira Orchestrate uses the Jira preset brief as the canonical MoonSpec orchestration input. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-003**: Source `specs/173-jira-orchestrate-preset/spec.md`, FR-007 and FR-008. Jira Orchestrate moves the issue to Code Review only after a confirmed pull request exists and stops before Code Review when pull request creation is blocked. Scope: in scope. Maps to FR-009.
- **DESIGN-REQ-004**: Source MM-398 Jira preset brief, Acceptance Criteria. Jira Orchestrate performs a trusted Jira blocker preflight before MoonSpec implementation work. Scope: in scope. Maps to FR-001, FR-002, and FR-003.
- **DESIGN-REQ-005**: Source MM-398 Jira preset brief, Requirements. Jira Orchestrate treats linked blocker issues as satisfied only when their status is Done. Scope: in scope. Maps to FR-004.
- **DESIGN-REQ-006**: Source MM-398 Jira preset brief, Requirements and Relevant Implementation Notes. Jira Orchestrate stops deterministically when blocker status cannot be determined through trusted Jira data. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-007**: Source MM-398 Jira preset brief, Acceptance Criteria and Requirements. Blocked output includes operator-readable target and blocker issue details when available. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-008**: Source MM-398 Jira preset brief, Requirements. Jira Orchestrate stays within the trusted Jira boundary and does not rely on scraping, prompt text alone, or raw Jira credentials. Scope: in scope. Maps to FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST evaluate blocker relationships for a Jira Orchestrate target issue before MoonSpec implementation work starts.
- **FR-002**: System MUST stop before MoonSpec specify, planning, task generation, implementation, pull request creation, and Code Review transition when the target issue is blocked by at least one issue that is not Done.
- **FR-003**: System MUST continue the existing Jira Orchestrate lifecycle when the target issue has no blocker relationships.
- **FR-004**: System MUST continue the existing Jira Orchestrate lifecycle when every detected blocking issue is Done.
- **FR-005**: System MUST stop with a blocked outcome when a blocker relationship exists but the blocking issue status cannot be determined from trusted Jira data.
- **FR-006**: System MUST report blocked outcomes with the target Jira issue key and all available blocking issue keys and statuses.
- **FR-007**: System MUST preserve the existing behavior that the target issue is moved to In Progress before implementation begins.
- **FR-008**: System MUST preserve the Jira preset brief, including Jira issue MM-398, as the canonical MoonSpec input for downstream artifacts.
- **FR-009**: System MUST preserve the existing behavior that Code Review transition occurs only after a confirmed pull request URL exists.
- **FR-010**: System MUST use trusted Jira data for blocker evaluation and MUST NOT rely on raw Jira credentials, web scraping, hardcoded transition IDs, or prompt text alone to decide blocker state.

### Key Entities

- **Target Jira Issue**: The Jira issue selected for Jira Orchestrate, including its issue key, current status, and issue relationships.
- **Blocking Jira Issue**: A linked issue that blocks the target issue, including its issue key and status when available.
- **Blocker Preflight Outcome**: The pre-implementation decision to continue orchestration or stop as blocked, with operator-readable issue details.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of Jira Orchestrate runs with at least one detected non-Done blocking issue stop before MoonSpec implementation work begins.
- **SC-002**: 100% of blocked outcomes include the target issue key and every available blocker issue key from trusted Jira data.
- **SC-003**: 100% of Jira Orchestrate runs with only Done blockers or no blocker relationships proceed through the same lifecycle stages as before this feature.
- **SC-004**: 0 blocked outcomes caused by this feature create a pull request or transition the target issue to Code Review.
- **SC-005**: Verification covers unresolved blocker, resolved blocker, no blocker, and missing blocker-status cases.
