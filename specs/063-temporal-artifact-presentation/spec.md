# Feature Specification: Temporal Artifact Presentation Contract

**Feature Branch**: `047-temporal-artifact-presentation`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs\Temporal\ArtifactPresentationContract.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

Requested source contract `docs/Temporal/ArtifactPresentationContract.md` is not present in the repository as of 2026-03-06. The requirements below are derived from the existing authoritative Temporal dashboard and artifact design documents that define artifact presentation behavior.

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/UI/TemporalDashboardIntegration.md` §9.2 "Temporal detail fetch sequence" (lines 350-361) | Temporal-backed task detail MUST fetch execution detail first, then fetch artifacts using the latest `temporalRunId` from the detail response, while keeping the route anchored to `taskId == workflowId`. |
| DOC-REQ-002 | `docs/UI/TemporalDashboardIntegration.md` §9.3 "Detail header model" (lines 363-384) | Temporal-backed detail MUST render the documented header fields and MAY expose raw workflow/debug metadata without replacing the task-oriented primary view. |
| DOC-REQ-003 | `docs/UI/TemporalDashboardIntegration.md` §9.4 "Timeline / event model" (lines 386-400) | v1 Temporal detail MUST use a synthesized summary/timeline view, surface waiting state context, keep artifacts as the main durable evidence surface, and MUST NOT expose raw Temporal history JSON directly. |
| DOC-REQ-004 | `docs/UI/TemporalDashboardIntegration.md` §10.1-10.3 "Action Mapping" (lines 404-441) | Temporal-backed detail MUST map task actions onto the documented execution update/signal/cancel behaviors and use task-oriented copy for the user-facing controls. |
| DOC-REQ-005 | `docs/UI/TemporalDashboardIntegration.md` §11.1-11.4 "Submit Integration" (lines 443-480) | Temporal-backed submit and redirect behavior MUST stay task-oriented, avoid a visible Temporal runtime selector, and keep the canonical detail route stable across reruns. |
| DOC-REQ-006 | `docs/UI/TemporalDashboardIntegration.md` §12.1-12.2 "Artifact Integration" (lines 482-503) | Temporal-backed flows MUST remain artifact-first for large inputs/outputs, and the dashboard MUST support create, upload, complete, metadata fetch, execution-scoped list, and download behaviors for artifacts. |
| DOC-REQ-007 | `docs/UI/TemporalDashboardIntegration.md` §12.3 "Presentation rules" (lines 505-513) | Artifact presentation MUST prefer execution linkage metadata, prefer preview when available, respect raw-access policy fields, avoid unsafe inline assumptions, and treat artifact edits as new immutable references. |
| DOC-REQ-008 | `docs/UI/TemporalDashboardIntegration.md` §12.4 "Run scoping" (lines 515-523) | Temporal detail MUST default artifact presentation to the latest run only, and prior-run browsing MUST NOT be mixed into the default artifact view. |
| DOC-REQ-009 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §12.2-12.3 "Presigned URLs" and "Redaction strategy" (lines 343-363) | Artifact download and preview behavior MUST use short-lived scoped access grants and default UI display to preview artifacts when raw content is restricted. |
| DOC-REQ-010 | Runtime scope guard from the task objective | Delivery MUST include production runtime code changes plus validation tests; docs-only output is insufficient. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View the Right Artifacts for a Temporal Task (Priority: P1)

As a task operator, I can open a Temporal-backed task detail page and see the latest-run artifacts, status context, and timeline summary without needing to understand Temporal internals.

**Why this priority**: Artifact visibility is the core user-facing value of the contract, and incorrect run scoping or route resolution would make the detail view misleading.

**Independent Test**: Open a Temporal-backed task detail page for an execution with multiple runs and linked artifacts, then verify that the page resolves by `taskId`, loads execution detail first, and displays only artifacts from the latest run.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed task with a stable `workflowId` and a newer latest run, **When** the operator opens the canonical task detail route, **Then** the page resolves by `taskId == workflowId` and loads artifacts using the latest `temporalRunId` returned by execution detail.
2. **Given** a Temporal-backed task with linked output, summary, and log artifacts, **When** detail data loads, **Then** the artifact section is shown as the primary durable evidence surface instead of raw workflow history.
3. **Given** a Temporal-backed task awaiting external input, **When** the operator views task detail, **Then** the page shows normalized status plus waiting context without exposing raw Temporal internals as the primary UX.

---

### User Story 2 - Safely Preview and Download Execution Artifacts (Priority: P1)

As a task operator, I can preview or download execution-linked artifacts through MoonMind-controlled access flows so sensitive data is handled safely and large inputs or outputs remain artifact-first.

**Why this priority**: Artifact presentation is incomplete if users cannot safely access the content that the detail page surfaces.

**Independent Test**: Create or link artifacts with preview and raw-access variants, then verify preview-first rendering, restricted raw access handling, and download behavior through authorized artifact endpoints.

**Acceptance Scenarios**:

1. **Given** an artifact with a preview reference, **When** the operator expands that artifact in task detail, **Then** the UI prefers the preview presentation instead of defaulting to raw download.
2. **Given** an artifact whose raw content is restricted, **When** a standard detail view requests access, **Then** raw content is not shown inline and the UI respects the artifact access policy metadata.
3. **Given** a large input or output must be attached to a Temporal-backed task, **When** the task is created or updated, **Then** artifact-first flows support create, upload, complete, metadata fetch, listing, and authorized download behavior.

---

### User Story 3 - Use Task-Oriented Controls Without Temporal Jargon (Priority: P2)

As a task operator, I can act on a Temporal-backed task using familiar task controls and wording while the system maps those actions onto execution operations behind the scenes.

**Why this priority**: Task-oriented copy and action mapping reduce migration friction and prevent the UI from leaking execution-engine concepts into the core product surface.

**Independent Test**: Exercise enabled controls on a Temporal-backed task and verify that action availability follows the documented state matrix while labels remain task-oriented.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed task in a running state, **When** the operator views available controls, **Then** the page exposes only the actions allowed for that state and labels them using task-oriented copy.
2. **Given** a Temporal-backed task is rerun or continued as new, **When** the operator returns to detail, **Then** the canonical task route remains stable and the page resolves the latest run metadata correctly.
3. **Given** submit support is enabled for a Temporal-backed flow, **When** the operator creates work from the task UI, **Then** the UI remains organized around task fields rather than exposing a Temporal runtime selector.

### Edge Cases

- What happens when task detail is opened from a stale list row whose cached `temporalRunId` no longer matches the latest run?
- How does the artifact section behave when an execution has no artifacts for the latest run but older runs do?
- What happens when preview metadata exists but the preview artifact cannot be read or has expired access grants?
- How does the UI respond when raw access is denied but a preview is allowed?
- What happens when a rerun occurs between initial detail load and artifact-section refresh?
- How does the default view prevent prior-run artifacts from being mixed into the latest-run artifact list?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The delivery MUST include production runtime code changes that implement Temporal artifact presentation behavior in the product runtime path, not docs-only updates. (Maps: DOC-REQ-010)
- **FR-002**: The delivery MUST include automated validation tests covering Temporal task detail artifact presentation, access-policy handling, and route/run-scoping behavior. (Maps: DOC-REQ-010)
- **FR-003**: The system MUST resolve Temporal-backed task detail through the canonical task route using `taskId == workflowId`, fetch execution detail before artifact listing, and use the latest `temporalRunId` from detail for artifact queries. (Maps: DOC-REQ-001)
- **FR-004**: The Temporal-backed detail header MUST present task-oriented summary fields and MAY include advanced execution metadata without replacing the primary task-oriented detail experience. (Maps: DOC-REQ-002)
- **FR-005**: The Temporal-backed detail experience MUST present a synthesized summary/timeline view, surface waiting context when applicable, and MUST NOT expose raw Temporal history JSON as the default detail experience. (Maps: DOC-REQ-003)
- **FR-006**: The detail page MUST expose only the documented action set appropriate to the current Temporal-backed state and MUST use task-oriented labels for those controls. (Maps: DOC-REQ-004)
- **FR-007**: Temporal-backed submit and redirect behavior MUST remain task-oriented, MUST avoid a visible Temporal runtime selector, and MUST keep the canonical task route stable across reruns. (Maps: DOC-REQ-005)
- **FR-008**: The system MUST support artifact-first task flows for large inputs and outputs, including artifact creation, upload completion, metadata fetch, execution-scoped listing, and authorized download behavior needed by Temporal-backed task UX. (Maps: DOC-REQ-006)
- **FR-009**: Artifact presentation MUST render linkage labels and metadata when available, prefer preview presentation when available, and MUST NOT assume every artifact is safe for inline raw display. (Maps: DOC-REQ-007, DOC-REQ-009)
- **FR-010**: Artifact presentation MUST respect access-policy metadata, including restricted raw access and default read references, before allowing inline view or download behavior. (Maps: DOC-REQ-007, DOC-REQ-009)
- **FR-011**: Artifact edits initiated from task flows MUST create new artifact references rather than mutating existing artifact content in place. (Maps: DOC-REQ-007)
- **FR-012**: The default artifact view for a Temporal-backed task MUST show artifacts for the latest run only and MUST NOT silently mix prior-run artifacts into the default presentation. (Maps: DOC-REQ-008)
- **FR-013**: Artifact preview and download behavior MUST use MoonMind-controlled short-lived scoped access flows instead of direct unmanaged object-store access. (Maps: DOC-REQ-006, DOC-REQ-009)

### Key Entities *(include if feature involves data)*

- **Temporal Task Detail**: The unified task-oriented detail view for a Temporal-backed execution, keyed by `taskId == workflowId`.
- **Execution Artifact Entry**: A presentation record for one artifact linked to a specific execution run, including display label, metadata, preview availability, and access-policy indicators.
- **Artifact Access Policy**: The set of presentation-relevant permissions and defaults that determine whether preview or raw content can be shown or downloaded.
- **Latest Run Scope**: The execution-run context selected from the current execution detail response and used as the default boundary for artifact presentation.
- **Task Action Surface**: The set of task-oriented controls that map onto Temporal-backed update, signal, cancel, and rerun operations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In automated validation, 100% of Temporal-backed detail loads fetch artifacts using the latest run returned by execution detail rather than a stale run identifier.
- **SC-002**: In automated validation, 100% of default Temporal-backed artifact views show only latest-run artifacts and do not mix artifacts from prior runs.
- **SC-003**: In automated validation, 100% of restricted-raw artifacts are prevented from unsafe inline raw display while still allowing approved preview or authorized download behavior.
- **SC-004**: At least 95% of preview-capable artifacts render through preview-first flows without requiring users to fall back to raw download.
- **SC-005**: Release acceptance confirms the feature ships with production runtime implementation changes plus validation tests, with no docs-only completion path.
- **SC-006**: `DOC-REQ-001` through `DOC-REQ-010` each remain mapped to one or more functional requirements, planned implementation surfaces, and validation paths across the feature artifacts.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode coverage is explicit and deterministic in `tasks.md`:
  - Production runtime code tasks: `T001-T006`, `T009-T012`, `T015-T018`, `T020-T022`.
  - Validation tasks: `T007-T008`, `T013-T014`, `T019`, `T024-T026`.
- `DOC-REQ-001` through `DOC-REQ-010` keep implementation + validation traceability through:
  - requirement-to-FR mapping in this spec,
  - planned implementation + validation mapping in `contracts/requirements-traceability.md`,
  - implementation/validation task mapping in the `DOC-REQ Coverage Matrix` in `tasks.md`.

### MEDIUM/LOW remediation status

- Prompt B scope-control language is aligned across `spec.md`, `plan.md`, and `tasks.md` so runtime implementation mode remains explicit and deterministic.
- Traceability wording now matches the current Temporal artifact-policy runtime surfaces and the validation suites that cover dashboard/runtime, artifact API, and `DOC-REQ-*` mapping gates.

### Residual risks

- The original requested source file `docs/Temporal/ArtifactPresentationContract.md` is still absent, so future contract alignment depends on the cited Temporal dashboard and artifact design documents remaining authoritative.
- Multi-surface implementation can still drift if future edits update dashboard/runtime code without keeping the `DOC-REQ` traceability contract and validation tasks synchronized.

## Assumptions

- The missing `docs/Temporal/ArtifactPresentationContract.md` will either be added later or superseded by the existing design documents already cited in this specification.
- Temporal-backed task detail continues to share the existing `/tasks/*` product surface rather than introducing a separate execution-only UI.
- Artifact authorization, metadata, and preview services remain MoonMind-owned surfaces rather than direct browser access to object storage or Temporal APIs.

## Dependencies

- Temporal execution detail and action APIs that provide stable workflow identity and latest-run metadata.
- Artifact metadata, preview, and download APIs capable of exposing execution-linked artifact information and access-policy metadata.
- Dashboard runtime configuration and task-detail surfaces that can render Temporal-backed detail alongside existing sources.
