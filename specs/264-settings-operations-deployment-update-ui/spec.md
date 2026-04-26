# Feature Specification: Settings Operations Deployment Update UI

**Feature Branch**: `264-settings-operations-deployment-update-ui`
**Created**: 2026-04-26
**Status**: Draft
**Input**:

```text
# MM-522 MoonSpec Orchestration Input

## Source

- Jira issue: MM-522
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Settings Operations deployment update UI
- Labels: `moonmind-workflow-mm-d22f5e68-8c97-4885-b9c4-cc74b6576885`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-522 from MM project
Summary: Settings Operations deployment update UI
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Tools/DockerComposeUpdateSystem.md
Source Title: Docker Compose Deployment Update System
Source Sections:
- 6. Settings -> Operations UX
- 17. Interaction with Settings information architecture
- 18. UI copy recommendations
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-016
- DESIGN-REQ-017

As a MoonMind administrator, I need a Settings -> Operations Deployment Update card that shows current deployment state and lets me submit a confirmed target image update, so I can update MoonMind without SSH access.

Acceptance Criteria
- The Deployment Update card appears under /tasks/settings?section=operations and not as top-level navigation.
- Current deployment shows stack name, Compose project, configured image, running image ID or digest when available, version/build when available, health summary, and last run result.
- Update target controls prefer digest-pinned or release-tagged choices and show a mutable-tag warning for latest or other mutable references.
- Update mode defaults to Restart changed services and offers Force recreate all services only when policy permits it, with the documented warning.
- The operator must enter a reason and confirm current image, target image, mode, stack, expected affected services, mutable tag warning when applicable, and restart warning before submission.
- Recent actions show status, requested image, resolved digest, operator, reason, timestamps, run detail link, logs artifact link, and before/after summary.
- Raw command-log links are hidden or disabled for users without operational-admin permissions.

Requirements
- Expose the operator-facing deployment update workflow in Settings Operations.
- Make target MoonMind image the primary UI choice.
- Keep updater runner image internal and absent from ordinary UI controls.
- Use concise progress states and artifact links for review.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-522 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
```

## Classification

Input classification: single-story feature request. The brief selects one independently testable runtime UI behavior story from `docs/Tools/DockerComposeUpdateSystem.md`: an Operations page Deployment Update card that reads existing deployment state/target data, collects a policy-valid update request, confirms the restart-sensitive action, and displays recent update context.

## User Story - Deployment Update Operations Card

**Summary**: As a MoonMind administrator, I need a Deployment Update card under Settings Operations so I can inspect current deployment state and submit a confirmed MoonMind image update without SSH access.

**Goal**: The Operations settings surface provides an operator-facing deployment update workflow with current stack state, target image controls, default restart mode, reason and confirmation requirements, mutable-tag warnings, recent action context, and no ordinary control for the privileged updater runner image.

**Independent Test**: Render the Settings Operations page with mocked deployment endpoints, verify the card appears only in the Operations section, inspect displayed current state and target controls, submit a mutable-tag update, confirm the dialog text, and assert the request payload uses the typed deployment update endpoint without exposing updater runner image controls.

**Acceptance Scenarios**:

1. **Given** an administrator opens `/tasks/settings?section=operations`, **When** deployment state and image targets load, **Then** the Deployment Update card appears in the Operations section with current stack, Compose project, configured image, running image evidence when available, health summary, and last run result.
2. **Given** image targets include release tags and mutable tags, **When** the card renders, **Then** the target controls prefer a release-tagged choice when available and show a warning when the selected reference is mutable such as `latest`.
3. **Given** the policy allows both update modes, **When** the card renders, **Then** Restart changed services is selected by default and Force recreate all services is available with a restart warning.
4. **Given** the operator enters a target and reason, **When** they submit the update, **Then** confirmation includes current image, target image, mode, stack, affected service summary, mutable-tag warning when applicable, and service restart warning before the typed update request is sent.
5. **Given** recent deployment update data is available, **When** the card renders, **Then** recent actions include status, requested image, resolved digest, operator, reason, timestamps, run detail link, logs artifact link, and before/after summary when those fields are present.
6. **Given** the operator lacks operational-admin artifact permissions, **When** raw command-log links are not allowed by the response, **Then** the card does not expose raw command-log links.

### Edge Cases

- Deployment endpoints may fail independently from worker pause controls; the deployment card reports its own loading/error state without hiding the rest of Operations.
- If running image ID or digest is unavailable, the card shows that evidence as unavailable rather than inventing values.
- If only mutable targets are available, the card still allows policy-provided references but keeps the mutable-tag warning visible.
- If Force recreate all services is not policy-provided by target metadata, the UI does not offer it.

## Assumptions

- MM-518 through MM-521 own authorization, queueing, typed tool contract, desired-state persistence, locking, execution, verification, and artifacts. This story owns the Mission Control Settings Operations UI behavior over the existing deployment endpoints.
- The initial current-state endpoint may expose placeholder or partial state; the UI must render available fields without implying stronger evidence than the response contains.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Settings Operations section MUST render a Deployment Update card and MUST NOT add deployment update as top-level Mission Control navigation.
- **FR-002**: The card MUST fetch and display current deployment state, including stack, Compose project, configured image, running image ID or digest when available, service health summary, and last update result when available.
- **FR-003**: The card MUST fetch image target metadata and expose target image selection centered on the MoonMind image repository and reference, not on updater runner images.
- **FR-004**: The initial selected target reference MUST prefer a recent release tag or digest-pinned target when available before mutable tags such as `latest`.
- **FR-005**: Selecting a mutable reference such as `latest` MUST show a warning that the tag may resolve differently over time.
- **FR-006**: The update mode control MUST default to Restart changed services and MUST offer Force recreate all services only when policy metadata or local allowed-mode data permits it, with the documented warning.
- **FR-007**: The card MUST require a non-empty reason before submitting a deployment update request.
- **FR-008**: Before submitting, the card MUST require confirmation that includes current image, target image, mode, stack, expected affected services, mutable-tag warning when applicable, and restart warning.
- **FR-009**: On confirmation, the card MUST call the typed deployment update endpoint with stack, image repository/reference, mode, options, and reason using the existing API contract.
- **FR-010**: Recent deployment update actions MUST show available status, requested image, resolved digest, operator, reason, timestamps, run detail link, logs artifact link, and before/after summary fields.
- **FR-011**: The card MUST hide or disable raw command-log links when the response does not expose an operational-admin-permitted link.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-522` and this canonical Jira preset brief.

### Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tools/DockerComposeUpdateSystem.md` section 6.1 requires deployment update to live under Settings -> Operations. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-002**: Source section 6.2 requires the card to show current deployment state, target image controls, update mode, options, reason, and confirmation details. Scope: in scope. Maps to FR-002 through FR-009.
- **DESIGN-REQ-016**: Source section 6.2 and core invariants require the UI to select target deployment image and keep the privileged updater runner image internal. Scope: in scope. Maps to FR-003.
- **DESIGN-REQ-017**: Source section 6.3 and UI copy guidance require concise recent action/progress context and artifact links while hiding raw command logs from users without operational-admin permissions. Scope: in scope. Maps to FR-010 and FR-011.

### Success Criteria *(mandatory)*

- **SC-001**: A UI test verifies the Deployment Update card renders under `/tasks/settings?section=operations` with current deployment state and no top-level navigation item.
- **SC-002**: A UI test verifies default target selection prefers a recent release tag over `latest` and shows mutable-tag warning when `latest` is selected.
- **SC-003**: A UI test verifies confirmation text and the submitted deployment update payload.
- **SC-004**: A UI test verifies updater runner image controls are absent and raw command-log links are hidden unless explicitly exposed.
- **SC-005**: Traceability evidence preserves `MM-522` and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-016, and DESIGN-REQ-017 across MoonSpec artifacts.
