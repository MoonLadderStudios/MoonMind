# Feature Specification: Run Manifest Page Form

**Feature Branch**: `216-run-manifest-page-form`
**Created**: 2026-04-21
**Status**: Draft
**Input**: User description: the Jira preset brief for MM-419, preserved verbatim below.

```text
Jira issue: MM-419 from MM project
Summary: Run registry or inline manifests from the Manifests page
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-419 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-419: Run registry or inline manifests from the Manifests page

Source Reference
- Source Document: docs/UI/ManifestsPage.md
- Source Title: Manifests Page
- Source Sections:
  - Page structure
  - Run Manifest card
  - Source type toggle
  - Fields
  - Validation rules
  - Responsive behavior
  - Accessibility
  - Why inline form is preferred over a drawer/modal
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-005
  - DESIGN-REQ-006
  - DESIGN-REQ-007
  - DESIGN-REQ-013
  - DESIGN-REQ-014
  - DESIGN-REQ-016
  - DESIGN-REQ-018

User Story
As a dashboard user, I want a compact Run Manifest form on the Manifests page that supports registry names and inline YAML so I can start either kind of manifest run from the same context.

Acceptance Criteria
- Given /tasks/manifests loads, then a Run Manifest card is visible above Recent Runs and advanced options are collapsed by default.
- Given Registry Manifest mode is active, then manifest name and action are required and inline YAML is not required.
- Given Inline YAML mode is active, then non-empty YAML and action are required and registry manifest name is not required.
- Given a user switches between source modes, then values entered in each mode are preserved independently for the current session.
- Given max docs is provided, then non-positive or non-integer values are rejected before submit.
- Given submitted content or helper fields contain raw secret-style entries, then the UI rejects them or requires env/Vault references instead.
- Given the page is used with keyboard or assistive technology, then the source toggle, validation messages, YAML fallback, and primary action have accessible labels and field associations.
- Given the viewport narrows, then form controls stack in a clear form-first flow without hiding the primary Run Manifest action behind advanced options.

Requirements
- The Run Manifest card must be available directly on /tasks/manifests.
- Registry Manifest mode must accept a required manifest name and existing supported action values.
- Inline YAML mode must accept required YAML content and existing supported action values.
- Advanced options must include supported dry run, force full sync, max docs, and priority controls where those controls already exist or are backend-supported.
- The primary action label must be Run Manifest and must disable while submission is in progress.
- The implementation must not introduce raw secret entry into the UI.
- Autocomplete from /api/manifests may be added only as progressive enhancement with free-text fallback.

Implementation Notes
- This story is about adding compact Manifests-page authoring for manifest runs, not replacing the existing manifest execution contract.
- The UI must support both registry manifest names and inline YAML as separate source modes.
- Mode switching must preserve the entered values independently for the current session.
- Validation must happen before submit for required source fields, supported actions, max docs, and raw secret-style values.
- Preserve MM-419 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-419 is blocked by MM-420, whose embedded status is Done.
- Trusted Jira link metadata also shows MM-419 blocks MM-418, which is not a blocker for MM-419 and is ignored for dependency gating.
```

## User Story - Run Manifest From Manifests Page

**Summary**: As a dashboard user, I want a compact Run Manifest form on the Manifests page that supports registry names and inline YAML so I can start either kind of manifest run from the same context.

**Goal**: Users can launch supported manifest runs from `/tasks/manifests` without leaving the recent-runs context, while client-side validation prevents unsupported or unsafe submissions before any run request is sent.

**Independent Test**: Open `/tasks/manifests`, exercise registry and inline source modes, submit valid runs through the existing manifest run flow, and verify invalid names, empty YAML, invalid max docs, and raw secret-shaped values are rejected before submission.

**Acceptance Scenarios**:

1. **Given** a user opens `/tasks/manifests`, **When** the page renders, **Then** the Run Manifest card appears before Recent Runs and advanced options are collapsed by default.
2. **Given** Registry Manifest mode is active, **When** a user submits without a registry manifest name, **Then** the page reports that the registry manifest name is required and does not require inline YAML.
3. **Given** Inline YAML mode is active, **When** a user submits without inline YAML, **Then** the page reports that manifest YAML is required and does not require a registry manifest name.
4. **Given** a user enters values in one source mode, **When** they switch to the other source mode and back, **Then** values entered for each mode are preserved independently for the current session.
5. **Given** a user enters a non-positive or non-integer max docs value, **When** they submit, **Then** the page rejects the value before sending any manifest upsert or run request.
6. **Given** submitted content or helper fields contain raw secret-style values, **When** the user submits, **Then** the page rejects the submission before sending any manifest upsert or run request and directs the user to env or Vault references.
7. **Given** keyboard or assistive technology users interact with the form, **When** they navigate controls and validation messages, **Then** source selection, source-specific inputs, action, advanced options, and submit feedback have accessible names or associations.
8. **Given** the viewport narrows, **When** form controls wrap or stack, **Then** the form remains first in the page flow and the Run Manifest action remains visible without opening advanced options.

### Edge Cases

- Blank or whitespace-only registry manifest names are rejected in registry mode.
- Blank or whitespace-only inline YAML is rejected in inline mode.
- `max docs` values such as `0`, negative numbers, decimals, and alphabetic text are rejected before submit.
- Raw secret-shaped values in inline YAML, manifest names, or helper fields are rejected, while env/Vault reference-style values remain allowed.
- Failed submission preserves entered data and leaves the user on `/tasks/manifests`.

## Assumptions

- Priority is not added to the UI unless a supported manifest-run backend field exists; the current manifest run options support dry run, force full sync, and max docs.
- Registry autocomplete remains a progressive enhancement and free-text registry names are sufficient for this story.

## Source Design Requirements

The preserved Jira brief lists source coverage IDs `DESIGN-REQ-003`, `DESIGN-REQ-004`, `DESIGN-REQ-005`, `DESIGN-REQ-006`, `DESIGN-REQ-007`, `DESIGN-REQ-013`, `DESIGN-REQ-014`, `DESIGN-REQ-016`, and `DESIGN-REQ-018`. The mappings below are this single-story spec's extracted requirements from `docs/UI/ManifestsPage.md`; they keep the story traceable without requiring unrelated source sections to become in-scope work.

- **DESIGN-REQ-001** (`docs/UI/ManifestsPage.md`, Page structure): The page has a Run Manifest section above Recent Runs. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-002** (`docs/UI/ManifestsPage.md`, Source type toggle): The run form distinguishes Registry Manifest and Inline YAML modes and preserves mode values during switching. Scope: in scope. Maps to FR-002, FR-003, FR-004.
- **DESIGN-REQ-003** (`docs/UI/ManifestsPage.md`, Fields): The form includes required source fields, supported action values, and supported advanced options. Scope: in scope. Maps to FR-002, FR-003, FR-005.
- **DESIGN-REQ-004** (`docs/UI/ManifestsPage.md`, Validation rules): The form rejects missing required inputs, invalid max docs values, and raw secret-shaped values. Scope: in scope. Maps to FR-005, FR-006.
- **DESIGN-REQ-005** (`docs/UI/ManifestsPage.md`, Submission behavior): Successful submissions stay on the Manifests page, refresh recent runs, and expose a run detail affordance. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-006** (`docs/UI/ManifestsPage.md`, Accessibility): Controls and validation feedback are keyboard and assistive-technology accessible. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-007** (`docs/UI/ManifestsPage.md`, Responsive behavior): Narrow layouts keep a clear form-first flow and visible primary action. Scope: in scope. Maps to FR-009.
- **DESIGN-REQ-008** (`docs/UI/ManifestsPage.md`, Optional future section): Saved manifest registry browsing is optional and must not be required for this story. Scope: out of scope because MM-419 only requires free-text fallback and direct run submission.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST show a Run Manifest form directly on `/tasks/manifests` above Recent Runs, with advanced options collapsed by default.
- **FR-002**: The system MUST support Registry Manifest mode with required manifest name and supported action values, without requiring inline YAML.
- **FR-003**: The system MUST support Inline YAML mode with required YAML content and supported action values, without requiring a registry manifest name.
- **FR-004**: The system MUST preserve entered values for Registry Manifest and Inline YAML modes independently during source-mode switching in the current session.
- **FR-005**: The system MUST reject non-positive and non-integer max docs values before sending manifest upsert or run requests.
- **FR-006**: The system MUST reject raw secret-style values in submitted manifest content or helper fields before sending manifest upsert or run requests, while allowing env/Vault reference-style values.
- **FR-007**: Successful valid submissions MUST remain on `/tasks/manifests`, expose the started run, and refresh Recent Runs in place.
- **FR-008**: Form controls and feedback MUST expose accessible names or associations for source mode, source-specific fields, action, advanced options, and submission status.
- **FR-009**: The form MUST keep the Run Manifest action visible in the default form flow without requiring advanced options to be opened.
- **FR-010**: The implementation MUST preserve Jira issue key MM-419 in Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend validation tests show invalid max docs and raw secret-shaped values are rejected before any manifest upsert or run request.
- **SC-002**: Frontend tests show registry and inline submissions still call the existing manifest APIs and refresh recent runs in place.
- **SC-003**: Accessibility-oriented tests can locate the source mode, source-specific inputs, action, advanced options, and Run Manifest action by accessible label or role.
- **SC-004**: The final verification report traces implementation evidence back to MM-419 and all in-scope DESIGN-REQ mappings.
