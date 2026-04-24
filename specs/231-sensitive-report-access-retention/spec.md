# Feature Specification: Apply Report Access and Lifecycle Policy

**Feature Branch**: `231-sensitive-report-access-retention`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-495 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-495-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-495 from MM project
Summary: Apply report access and lifecycle policy
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-495 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-495: Apply report access and lifecycle policy

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 10. Metadata model for report artifacts, 14. Security and access model, 15. Retention guidance
- Coverage IDs: DESIGN-REQ-011, DESIGN-REQ-017, DESIGN-REQ-018

User Story
As an operator, I want sensitive reports to reuse artifact authorization, preview, retention, pinning, and deletion behavior so report delivery is useful without widening access or lifecycle risk.

Acceptance Criteria
- Report artifacts use the existing artifact authorization model for preview and raw reads.
- Restricted `report.primary`, `report.structured`, and `report.evidence` artifacts can point `default_read_ref` to preview artifacts when raw access is disallowed.
- Report metadata does not expose secrets, raw access grants, cookies, session tokens, or large inline payloads.
- Default retention policy can map `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, and `report.export` to long retention, `report.structured` to long or standard, and `report.evidence` to standard or long by product policy.
- Final reports can be explicitly pinned and unpinned through the existing artifact API.
- Deleting a report artifact uses existing soft-delete/hard-delete behavior and does not implicitly delete unrelated observability artifacts.

Requirements
- Reuse artifact authorization for sensitive reports.
- Support preview-safe report presentation.
- Apply report-specific retention recommendations through existing lifecycle controls.
- Avoid unsafe cascading deletion semantics.

Relevant Implementation Notes
- Keep report artifact access within the existing artifact authorization, preview, and `default_read_ref` boundaries.
- Enforce bounded report metadata validation so report metadata stays safe for control-plane display.
- Use existing artifact APIs for pinning, unpinning, soft deletion, and hard deletion.
- Preserve separation between report artifact lifecycle behavior and unrelated runtime observability artifacts such as stdout, stderr, diagnostics, and logs.
- Preserve MM-495 and coverage IDs DESIGN-REQ-011, DESIGN-REQ-017, and DESIGN-REQ-018 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- Widening raw artifact access for sensitive reports.
- Creating report-specific authorization, retention, pinning, or deletion systems separate from the existing artifact system.
- Allowing unsafe report metadata values, secret-like tokens, or oversized inline payloads in control-plane metadata.
- Cascading report deletion into unrelated runtime stdout, stderr, diagnostics, logs, or other observability artifacts.

Validation
- Verify sensitive report presentation uses preview/default-read behavior where available and does not require raw download access.
- Verify report metadata remains bounded and safe for display.
- Verify `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, and `report.export` artifacts default to long retention unless policy overrides them.
- Verify `report.structured` and `report.evidence` artifacts follow standard or long retention based on report family and audit needs.
- Verify final report artifacts can be pinned and unpinned through existing artifact APIs.
- Verify report artifact deletion uses artifact-system-native soft/hard deletion without implicitly deleting unrelated runtime observability artifacts.

Needs Clarification
- None

## Classification

- Input type: Existing feature directory backed by a single-story Jira preset brief.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable operator story.
- Selected mode: Runtime.
- Source design: `docs/Artifacts/ReportArtifacts.md` is treated as runtime source requirements because the Jira brief points at implementation behavior.
- Resume decision: Existing Moon Spec artifacts were found in `specs/231-sensitive-report-access-retention`, but they were anchored to MM-463. The first incomplete stage for this request was spec alignment to MM-495; downstream artifacts were then updated conservatively to preserve coherence without regenerating completed implementation evidence.

## User Story - Apply Report Access and Lifecycle Policy

**Summary**: As an operator, I want sensitive report artifacts to reuse existing authorization, preview, retention, pinning, and deletion behavior so reports remain useful without widening raw access or leaking unsafe metadata.

**Goal**: Report artifacts preserve safe read behavior, bounded metadata, report-aware retention defaults, ordinary pin/unpin controls, and artifact-native deletion while keeping unrelated logs and diagnostics separate.

**Independent Test**: Create sensitive report artifacts through the artifact service, verify restricted raw access exposes preview/default-read metadata without granting raw download, verify report metadata rejects unsafe or oversized values, verify report link types derive the expected retention defaults, verify pin/unpin keeps report retention semantics, and verify deleting a report artifact does not delete unrelated observability artifacts.

**Acceptance Scenarios**:

1. **Given** a sensitive report artifact has restricted raw access and a preview artifact, **When** a caller can read metadata but cannot access the raw artifact, **Then** the artifact metadata exposes the preview as `default_read_ref` and raw download remains denied.
2. **Given** report metadata includes unsupported keys, secret-like values, cookies, session tokens, raw access grants, or oversized inline payloads, **When** the report artifact contract is validated, **Then** creation or report-link publication fails.
3. **Given** a `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, or `report.export` artifact is created without an explicit retention override, **When** it is linked to an execution, **Then** its default retention class is `long`.
4. **Given** a `report.structured` or `report.evidence` artifact is created without an explicit retention override, **When** it is linked to an execution, **Then** its default retention class is `standard` or `long` according to product policy and remains distinct from observability retention.
5. **Given** a final report artifact is pinned and later unpinned, **When** the existing artifact API completes both mutations, **Then** the artifact returns to its report-derived retention class rather than a generic default.
6. **Given** a report artifact and unrelated runtime observability artifacts are linked to the same execution, **When** the report artifact is deleted, **Then** only the report artifact enters deleted state and unrelated stdout, stderr, diagnostics, or log artifacts remain intact.

### Edge Cases

- A report artifact has restricted raw content but no preview artifact yet.
- A report artifact is explicitly created with a product-policy retention override.
- An artifact is linked as a report after being created with generic retention.
- A pinned report artifact is already deleted or has no active pin when unpin is requested.
- A report deletion happens while the execution still has related evidence and observability artifacts.
- A producer attempts to publish report metadata that contains unsupported keys or secret-like values.

## Assumptions

- Existing authorization rules determine which principals can read metadata and which principals can read raw artifact bytes.
- Product policy may still explicitly override retention at artifact creation time.
- `report.structured` and `report.evidence` default to `standard` unless producers or policy choose `long`; `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, and `report.export` default to `long`.
- Report metadata validation already exists at the report artifact contract boundary and remains the correct enforcement point for metadata safety.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-011 | `docs/Artifacts/ReportArtifacts.md` §10 | Report metadata must remain bounded and safe for control-plane display and must not contain secrets, raw access grants, cookies, session tokens, or large inline payloads. | In scope | FR-003 |
| DESIGN-REQ-017 | `docs/Artifacts/ReportArtifacts.md` §14 | Sensitive reports must use existing artifact authorization and preview/default-read behavior instead of widening raw access. | In scope | FR-001, FR-002, FR-004 |
| DESIGN-REQ-018 | `docs/Artifacts/ReportArtifacts.md` §15 | Report artifacts use report-aware retention defaults, support pin/unpin through existing artifact APIs, and retain artifact-native deletion boundaries. | In scope | FR-005, FR-006, FR-007, FR-008, FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST keep sensitive report artifacts under the existing artifact authorization model for preview and raw reads.
- **FR-002**: The system MUST expose a preview artifact as `default_read_ref` when a caller can read report metadata but cannot access restricted raw report bytes and a preview is available.
- **FR-003**: The system MUST reject report metadata that contains unsupported keys, secret-like values, raw access grants, cookies, session tokens, or oversized inline payloads.
- **FR-004**: The system MUST deny raw report download or presign operations when the caller lacks restricted raw access.
- **FR-005**: The system MUST derive `long` retention for `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, and `report.export` artifacts when no explicit retention override is provided.
- **FR-006**: The system MUST derive a non-observability retention class of `standard` or `long` for `report.structured` and `report.evidence` artifacts when no explicit retention override is provided.
- **FR-007**: The system MUST allow final report artifacts to be pinned and unpinned through the existing artifact API while restoring report-derived retention after unpin.
- **FR-008**: The system MUST soft-delete and hard-delete report artifacts through the existing artifact lifecycle path.
- **FR-009**: The system MUST NOT implicitly delete unrelated runtime stdout, stderr, diagnostics, logs, provider snapshots, or session continuity artifacts when deleting a report artifact.
- **FR-010**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-495.

### Key Entities

- **Sensitive Report Artifact**: A report artifact whose raw bytes may be restricted while metadata and preview/default-read behavior remain available to authorized readers.
- **Report Metadata**: The bounded metadata shown in control-plane responses for report artifacts.
- **Report Retention Class**: The retention class derived from report link type or explicit policy, including `long`, `standard`, and `pinned`.
- **Artifact Pin**: Existing artifact lifecycle state that protects an artifact from automatic deletion until it is unpinned.
- **Observability Artifact**: Runtime stdout, stderr, merged logs, diagnostics, provider snapshots, or session continuity artifacts that remain separate from curated report deletion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A restricted report artifact with a preview returns a `default_read_ref` pointing at that preview for a metadata-readable caller without raw access.
- **SC-002**: Report metadata validation rejects unsupported keys, secret-like values, cookies, session tokens, raw access grants, and oversized inline payloads before report publication.
- **SC-003**: 100% of newly created `report.primary`, `report.summary`, `report.appendix`, `report.findings_index`, and `report.export` artifacts without explicit retention overrides receive `long` retention.
- **SC-004**: `report.structured` and `report.evidence` artifacts without explicit retention overrides receive `standard` or `long` retention and never inherit observability-style retention.
- **SC-005**: Pinning then unpinning a report-primary artifact restores the report-derived retention class.
- **SC-006**: Deleting one report artifact changes only that artifact's status and leaves unrelated observability artifacts for the same execution readable and undeleted.
- **SC-007**: MM-495 appears in the spec, plan, tasks, verification evidence, and publish metadata for traceability.
