# Feature Specification: Route Binary Inputs Through Authorized Artifact Refs

**Feature Branch**: `321-route-binary-artifact-refs`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-628 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-628 MoonSpec Orchestration Input

## Source

- Jira issue: MM-628
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Route binary inputs through authorized artifact refs
- Priority: Medium
- Labels:
  - `moonmind-workflow-mm-86f66178-893d-469b-ba39-7bf1a3a19bb6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-628 from MM project
Summary: Route binary inputs through authorized artifact refs
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-628 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-628: Route binary inputs through authorized artifact refs

Source Reference
- Source document: `docs/Tasks/TaskArchitecture.md`
- Source title: Task Architecture (Control Plane)
- Source sections:
  - 3.2 Artifact-first binary handling
  - 5.2 Artifact upload orchestration
  - 9 Artifact and authorization boundary
  - 11 Invariants
- Coverage IDs:
  - DESIGN-REQ-002
  - DESIGN-REQ-007
  - DESIGN-REQ-020
  - DESIGN-REQ-022

User Story
As an operator, I want browser-selected binary inputs uploaded, authorized, finalized, and submitted as lightweight artifact refs so workflow histories and instructions never contain image bytes or storage credentials.

Acceptance Criteria
- Binary input bytes are stored as artifacts and never embedded in workflow history or inline instruction text.
- Incomplete or invalid uploads are rejected before execution submission.
- Execution submission includes only structured attachment refs.
- Browser preview/download use MoonMind APIs authorized by execution ownership and view permissions.
- Worker materialization uses service credentials and execution authorization.

Requirements
- Binary inputs are artifacts represented by lightweight refs, never workflow-history or instruction-text bytes.
- The browser creates upload intents, completes uploads before submission, rejects incomplete uploads, and submits structured refs only.
- Browsers use MoonMind APIs, preview/download is authorized by ownership, and workers use service credentials.
- Attachment policy is server-defined and enforced by browser/API; browsers do not call Jira, storage, or provider file endpoints directly.

Relevant Jira Links At Fetch Time
- MM-627: Normalize task-shaped submissions with explicit attachment targets (Done)
- MM-629: Persist authoritative task snapshots for reconstruction (Backlog)

## Orchestration Notes

- Input classification: single-story runtime feature request.
- Runtime mode: Jira Orchestrate should proceed from this artifact when later workflow steps are authorized.
- Source design path: `docs/Tasks/TaskArchitecture.md`.
- Preserve `MM-628`, the source design coverage IDs, and this canonical brief in downstream MoonSpec artifacts and final verification evidence.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /moonspec-breakdown for technical designs that contain multiple stories. -->

## User Story - Submit Binary Inputs As Authorized Artifact References

**Summary**: As an operator, I want browser-selected binary inputs uploaded, authorized, finalized, and submitted as lightweight artifact refs so workflow histories and instructions never contain image bytes or storage credentials.

**Goal**: Operators can submit tasks with binary inputs while MoonMind stores the bytes as artifacts, submits only structured refs to execution, enforces upload completeness, and keeps browser and worker access inside authorized MoonMind artifact boundaries.

**Independent Test**: Submit a task draft with browser-selected binary inputs, verify upload intents are created and finalized before execution submission, confirm the execution payload contains only structured artifact refs, and validate preview/download plus worker materialization use authorized MoonMind service paths without exposing raw bytes or storage credentials in workflow history or inline instructions.

**Acceptance Scenarios**:

1. **Given** an operator selects a binary input for a task, **When** the browser prepares the task for submission, **Then** MoonMind creates an artifact upload intent and stores the binary bytes as an artifact rather than embedding bytes in task instructions or workflow history.
2. **Given** a binary upload intent has not been completed or finalized, **When** the operator submits the task, **Then** the system rejects the submission before execution receives the task.
3. **Given** all selected binary inputs have completed valid uploads, **When** the task is submitted, **Then** the execution submission contains only structured attachment refs and no inline binary bytes or storage credentials.
4. **Given** an authorized user previews or downloads an attached binary input, **When** they access it from Mission Control, **Then** the browser uses MoonMind APIs that enforce execution ownership and view permissions.
5. **Given** a worker needs an attached binary input during execution, **When** the runtime materializes the input, **Then** materialization uses service credentials and execution authorization without exposing object-store credentials to the browser.
6. **Given** attachment metadata includes target kind, step reference, filename, or source import path, **When** the system stores or displays that metadata, **Then** target meaning remains defined by task contracts and snapshots rather than inferred from metadata or storage paths alone.

### Edge Cases

- An upload that starts but never finalizes must block submission rather than create a dangling execution reference.
- A finalized artifact ref that the submitting user is not authorized to use must be rejected before execution starts.
- Browser preview or download requests from a user without execution ownership or view permission must fail explicitly.
- Worker materialization must fail explicitly when execution authorization does not cover the requested artifact.
- Attachment metadata may be missing or partial, but missing metadata must not cause the system to infer a different target or authorization scope.
- Text instructions that mention an image or file must remain text and must not become a hidden binary payload.

## Assumptions

- The existing server-defined attachment policy remains authoritative for allowed binary input types, sizes, and target eligibility.
- This story covers artifact-backed binary submission, preview/download authorization, and worker materialization boundaries; broader attachment target preservation across edit, rerun, and resume flows is covered by adjacent Jira issues.

## Source Design Requirements

- **DESIGN-REQ-002** (Source: `docs/Tasks/TaskArchitecture.md` section 3.2, lines 67-75): Binary inputs must be stored as artifacts, referenced in execution contracts by lightweight refs, and never embedded in workflow histories or text instructions. Scope: in scope. Maps to FR-001, FR-002, FR-004, FR-005.
- **DESIGN-REQ-007** (Source: `docs/Tasks/TaskArchitecture.md` section 5.2, lines 164-169): The browser must create upload intents through MoonMind artifact APIs, upload files before execution submission, finalize artifact creation, reject incomplete uploads, and submit only structured attachment refs. Scope: in scope. Maps to FR-001, FR-003, FR-004, FR-006.
- **DESIGN-REQ-020** (Source: `docs/Tasks/TaskArchitecture.md` section 9, lines 535-559): Browser access must avoid long-lived object-store credentials, previews and downloads must be authorized by execution ownership and view permissions, worker downloads must use service credentials and execution authorization, artifact links must be execution-scoped, and target binding must not be inferred from storage paths alone. Scope: in scope. Maps to FR-007, FR-008, FR-009, FR-010, FR-011.
- **DESIGN-REQ-022** (Source: `docs/Tasks/TaskArchitecture.md` section 11, lines 574-612): Task system invariants require no binary payloads in Temporal history, explicit attachment targets, no silent attachment loss, text remaining text, server-defined attachment policy, MoonMind-owned browser APIs, and target-aware runtime consumption. Scope: in scope. Maps to FR-002, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST create artifact upload intents for browser-selected binary inputs through MoonMind artifact APIs before task execution submission.
- **FR-002**: System MUST store binary input bytes as artifacts and MUST NOT embed binary bytes in workflow histories, execution payload text, or inline task instructions.
- **FR-003**: Browser upload flow MUST complete binary file uploads before execution submission is accepted.
- **FR-004**: System MUST finalize artifact creation before accepting an execution submission that references the uploaded binary input.
- **FR-005**: Execution submissions MUST include only structured attachment refs for binary inputs, not object-store credentials, raw bytes, or provider-specific file payloads.
- **FR-006**: Incomplete, invalid, unauthorized, or unfinalized binary uploads MUST be rejected before execution receives the task.
- **FR-007**: Browser preview and download of binary inputs MUST go through MoonMind APIs rather than direct Jira, storage, or provider file endpoints.
- **FR-008**: Browser preview and download MUST enforce execution ownership and view permissions before returning artifact content.
- **FR-009**: Worker-side binary input download and materialization MUST use service credentials and execution authorization.
- **FR-010**: Artifact links for submitted binary inputs MUST be execution-scoped so refs cannot be reused outside their authorized execution context.
- **FR-011**: Attachment metadata MAY support observability, but target meaning and authorization scope MUST be defined by task contracts and snapshots rather than inferred from metadata or storage paths alone.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-628` and the original Jira preset brief.

### Key Entities

- **Binary Input Artifact**: Stored binary content selected by the browser and represented by a lightweight ref after upload and finalization.
- **Upload Intent**: The authorization and metadata envelope that permits a browser-selected binary file to be uploaded through MoonMind artifact APIs before submission.
- **Structured Attachment Ref**: The lightweight execution-facing reference to a finalized artifact, scoped to the authorized execution context.
- **Artifact Access Request**: A preview, download, or materialization request that must be authorized by user permissions or service execution authorization.
- **Attachment Metadata**: Optional descriptive information such as target kind, step reference, filename, or source import path that aids observability but does not define target meaning by itself.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid task submissions with browser-selected binary inputs complete and finalize artifact uploads before execution submission is accepted.
- **SC-002**: 100% of accepted execution submissions with binary inputs contain structured attachment refs and contain zero inline binary bytes, object-store credentials, or provider-specific file payloads.
- **SC-003**: 100% of incomplete, invalid, unauthorized, or unfinalized binary uploads are rejected before execution receives the task.
- **SC-004**: 100% of covered browser preview and download requests are served through MoonMind APIs and enforce execution ownership or view permissions.
- **SC-005**: 100% of covered worker materialization requests use service credentials and execution authorization rather than browser-visible storage credentials.
- **SC-006**: Traceability review confirms `MM-628`, the original Jira preset brief, and DESIGN-REQ-002, DESIGN-REQ-007, DESIGN-REQ-020, and DESIGN-REQ-022 remain preserved across MoonSpec artifacts and final verification evidence.
